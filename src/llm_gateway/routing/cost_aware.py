"""Cost-aware backend selection (``ROUTING_STRATEGY=cost_aware``).

Ranks candidate backends by the dollar cost of serving the request on each, falling
back to latency/health when no pricing is configured. Complements ``gpu_aware``
(capacity routing): cost routing answers "which healthy replica serving this model
is cheapest right now?".

Pricing comes from ``model_pricing`` (USD per 1M tokens, provider wildcard ``*``
supported). Local backends (ollama/vllm/llama-cpp/…) have no token price, so they
score as free — which is exactly right for marginal cost, and the latency term keeps
slow free replicas from swamping fast cheap ones.

The pricing map is loaded lazily with a short TTL and a hard per-request DB timeout
so routing never stalls behind the database. Any failure degrades to free/neutral.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Local inference engines have no per-token provider cost.
LOCAL_ENGINE_TYPES = {"ollama", "vllm", "llama-cpp", "llama_cpp", "local", "xinference", "sglang", "tgi"}

PRICING_TTL_S = 30.0            # pricing table refresh window
PRICING_DB_TIMEOUT_S = 0.25     # hard cap: never block a request on the DB
W_COST = 2.0                    # dominates when several backends are healthy
W_LAT = 1.0
LAT_BAD_AT_S = 5.0
HEALTH_PENALTY = 10.0
NEUTRAL = 0.4

# Cache: {(model, provider_lower): (input_per_1m, output_per_1m)} keyed by model only
_pricing_cache: dict[str, tuple[float, float]] | None = None
_pricing_cache_ts: float = 0.0


@dataclass
class CostScore:
    backend: Any
    score: float
    estimated_cost_usd: Optional[float]
    reasons: dict[str, Any] = field(default_factory=dict)


def _backend_provider(backend) -> str:
    bt = getattr(backend, "backend_type", None)
    return (bt.value if hasattr(bt, "value") else str(bt or "")).lower()


def _is_local(provider: str) -> bool:
    return provider in LOCAL_ENGINE_TYPES


async def _load_pricing(session) -> dict:
    """{(model_lower, provider_lower): (in_per_1m, out_per_1m)} from model_pricing."""
    from sqlmodel import select

    from ..models import ModelPricing

    rows = (await session.execute(select(ModelPricing))).scalars().all()
    out: dict = {}
    for r in rows:
        key = ((r.model or "").lower(), (r.provider or "*").lower())
        if not key[0]:
            continue
        out.setdefault(key, (float(r.input_cost_per_1m or 0.0), float(r.output_cost_per_1m or 0.0)))
    return out


async def get_pricing_map(session) -> dict[str, tuple[float, float]]:
    """TTL-cached pricing map; never raises, never waits > DB timeout."""
    global _pricing_cache, _pricing_cache_ts
    now = time.monotonic()
    if _pricing_cache is not None and (now - _pricing_cache_ts) < PRICING_TTL_S:
        return _pricing_cache
    try:
        m = await asyncio.wait_for(_load_pricing(session), timeout=PRICING_DB_TIMEOUT_S)
        _pricing_cache, _pricing_cache_ts = m, now
        return m
    except asyncio.TimeoutError:
        logger.debug("cost_aware: pricing load timed out; using stale/empty map")
        return _pricing_cache or {}
    except Exception as e:  # table missing, DB down -> routing must not fail
        logger.debug("cost_aware: pricing load failed: %s", e)
        return _pricing_cache or {}


def invalidate_pricing_cache() -> None:
    global _pricing_cache, _pricing_cache_ts
    _pricing_cache, _pricing_cache_ts = None, 0.0


def _cost_usd(pricing: tuple[float, float], est_in: float, est_out: float) -> float:
    in_per_1m, out_per_1m = pricing
    return (est_in / 1_000_000.0) * in_per_1m + (est_out / 1_000_000.0) * out_per_1m


def _lookup_pricing(pricing_map: dict, model: str, provider: str):
    """Exact (model, provider) row wins; provider '*' wildcard is the fallback."""
    m = (model or "").lower()
    return pricing_map.get((m, provider or "*")) or pricing_map.get((m, "*"))


def score_backend(backend, model: str, pricing_map: dict, avg_latency_s: float,
                  inflight: int, est_in: float, est_out: float,
                  status: Optional[str] = None) -> CostScore:
    provider = _backend_provider(backend)
    local = _is_local(provider)
    pricing = _lookup_pricing(pricing_map, model, provider)
    exact = pricing is not None

    if local or pricing is not None:
        p = pricing or (0.0, 0.0)
        cost = _cost_usd(p, est_in, est_out)
    else:
        cost = None  # unknown price: neutral, not free

    # Normalise cost across candidates later (see rank()); keep raw here.
    lat_n = max(0.0, min(1.0, avg_latency_s / LAT_BAD_AT_S)) if avg_latency_s else 0.0
    queue_n = min(1.0, inflight / 32.0)

    reasons: dict[str, Any] = {
        "provider": provider or "unknown",
        "cost_usd": None if cost is None else round(cost, 6),
        "pricing": "local" if local else ("exact" if exact else "unknown"),
        "avg_latency_s": round(avg_latency_s, 3) if avg_latency_s else None,
        "inflight": inflight,
        "status": status or "unknown",
    }
    healthy = status in (None, "ok", "unsupported", "disabled")
    reasons["reason"] = "healthy" if healthy else f"unhealthy:{status}"

    score = W_LAT * lat_n + 0.3 * queue_n
    if cost is None:
        score += W_COST * NEUTRAL
    if not healthy:
        score += HEALTH_PENALTY
    return CostScore(backend=backend, score=round(score, 4), estimated_cost_usd=cost, reasons=reasons)


def rank(backends, model: str, pricing_map: dict, get_latencies, get_inflight,
         est_in: float, est_out: float, statuses: Optional[dict] = None) -> list[CostScore]:
    """Score then re-rank with a cross-candidate cost term, best first."""
    statuses = statuses or {}
    scored = [
        score_backend(b, model, pricing_map, float(get_latencies(b) or 0.0),
                      int(get_inflight(b) or 0), est_in, est_out,
                      status=statuses.get(getattr(b, "name", "")))
        for b in backends
    ]
    known = [c.estimated_cost_usd for c in scored if c.estimated_cost_usd is not None]
    hi = max(known) if known else 0.0
    for c in scored:
        if c.estimated_cost_usd is None:
            continue
        cost_n = (c.estimated_cost_usd / hi) if hi > 0 else 0.0
        c.score = round(c.score + W_COST * cost_n, 4)
    return sorted(scored, key=lambda c: (c.score, -max(1, int(getattr(c.backend, "weight", 100) or 100))))


def select_cost_aware(backends, model: str, pricing_map: dict, get_latencies, get_inflight,
                      est_in: float, est_out: float,
                      statuses: Optional[dict] = None) -> tuple[Any, list[CostScore]]:
    ranked = rank(backends, model, pricing_map, get_latencies, get_inflight, est_in, est_out, statuses)
    return ranked[0].backend, ranked
