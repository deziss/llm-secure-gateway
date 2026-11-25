"""GPU/infrastructure-aware backend selection (``ROUTING_STRATEGY=gpu_aware``).

Scores every candidate backend from live infrastructure state instead of raw utilization:

* KV-cache pressure and queue depth (vLLM server metrics from the poller)
* free VRAM headroom on the GPUs attributed to the server (``ModelServer.gpu_host`` /
  ``gpu_indices``) — a GPU at 90% util with room to allocate beats a 40% GPU that is
  out of allocatable memory, so memory dominates and util is weighted lightly
* historical latency (gateway-measured EWMA per base URL)
* recent per-model errors on the backend (gateway side) and poller health status

The score is lower-is-better and every candidate gets a human-readable rationale that
lands in the routing log line, so operators can answer "why this GPU server?".

All inputs are optional: any missing signal falls back to a neutral contribution, so
the strategy degrades to queue/latency balancing on hosts without GPU exporters.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Weights chosen so KV-cache + queue dominate (serving capacity today), memory
# headroom follows (can a new model/KV fit), latency refines, util only nudges.
W_KV = 1.5
W_QUEUE = 1.2
W_MEM = 0.8
W_UTIL = 0.4
W_LAT = 0.9
HEALTH_PENALTY = 10.0
QUEUE_FULL_AT = 32.0        # waiting requests mapped to 1.0
LAT_BAD_AT_S = 5.0          # avg latency mapped to 1.0
NEUTRAL = 0.4               # contribution when a signal is entirely unknown
SATURATED_KV = 95.0         # KV cache % that marks a server saturated


@dataclass
class CandidateScore:
    backend: Any
    score: float
    reasons: dict[str, Any] = field(default_factory=dict)


def _norm(x: Optional[float], lo: float, hi: float) -> Optional[float]:
    if x is None:
        return None
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def _match_server_stat(backend, snapshot: dict) -> Optional[dict]:
    """Find the poller ServerStat for a backend by name (preferred) or URL."""
    servers = snapshot.get("servers") or []
    name = getattr(backend, "name", "") or ""
    url = (getattr(backend, "base_url", "") or "").rstrip("/")
    alt_urls = {(u or "").rstrip("/") for u in (getattr(backend, "fallback_urls", None) or []) if u}
    for s in servers:
        if name and s.get("name") == name:
            return s
    for s in servers:
        surl = (s.get("url") or "").rstrip("/")
        if url and (surl == url or surl in alt_urls):
            return s
    return None


def _gpu_pressure(backend, snapshot: dict) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """(worst_free_vram_frac, mean_util_pct, temp_c) over the GPUs attributed to a backend.

    Uses pinned ``gpu_indices`` when set, otherwise all GPUs reported for the server's
    gpu_host (mirrors poller attribution). No attribution -> (None, None, None).
    """
    host_name = getattr(backend, "gpu_host", None) or None
    indices = list(getattr(backend, "gpu_indices", None) or []) or None
    if not host_name:
        stat_url = (getattr(backend, "base_url", "") or "").rstrip("/")
        for s in snapshot.get("servers") or []:
            if s.get("name") == getattr(backend, "name", None) or (s.get("url") or "").rstrip("/") == stat_url:
                host_name = s.get("gpu_host") or None
                if indices is None:
                    indices = list(s.get("gpu_indices") or []) or None
                break
    if not host_name:
        return None, None, None
    for h in snapshot.get("gpu_hosts") or []:
        if h.get("name") != host_name:
            continue
        gpus = h.get("gpus") or []
        if indices is not None:
            sel = [g for g in gpus if g.get("index") in indices]
            if sel:
                gpus = sel
        if not gpus:
            return None, None, None
        free_fracs: list[float] = []
        utils: list[float] = []
        temps: list[float] = []
        for g in gpus:
            used, total = g.get("mem_used_mib"), g.get("mem_total_mib")
            if used is not None and total:
                free_fracs.append(max(0.0, 1.0 - (used / total)))
            if g.get("util_pct") is not None:
                utils.append(float(g["util_pct"]))
            if g.get("temp_c") is not None:
                temps.append(float(g["temp_c"]))
        free = min(free_fracs) if free_fracs else None
        util = sum(utils) / len(utils) if utils else None
        temp = max(temps) if temps else None
        return free, util, temp
    return None, None, None


def score_backend(backend, model: str, snapshot: dict, avg_latency_s: float, inflight: int) -> CandidateScore:
    stat = _match_server_stat(backend, snapshot)
    reasons: dict[str, Any] = {}

    # --- serving capacity (vLLM / Ollama server stats) --------------------
    kv_pct: Optional[float] = None
    waiting: Optional[float] = None
    status: Optional[str] = None
    if stat:
        status = stat.get("status")
        mstats = (stat.get("model_stats") or {}).get(model) or {}
        kv_pct = mstats.get("kv_cache_pct")
        if kv_pct is None:
            kv_pct = stat.get("kv_cache_pct")
        waiting = mstats.get("waiting")
        if waiting is None:
            waiting = stat.get("waiting")
    if waiting is not None:
        waiting = float(waiting) + max(0, inflight)   # gateway in-flight joins the queue
    elif inflight:
        waiting = float(inflight)

    kv_n = _norm(kv_pct, 0.0, 100.0)
    queue_n = _norm(waiting, 0.0, QUEUE_FULL_AT)

    # --- GPU capacity ------------------------------------------------------
    free_vram, gpu_util_pct, gpu_temp = _gpu_pressure(backend, snapshot)
    mem_pressure_n = (1.0 - free_vram) if free_vram is not None else None
    util_n = _norm(gpu_util_pct, 0.0, 100.0)

    lat_n = _norm(avg_latency_s or 0.0, 0.0, LAT_BAD_AT_S)

    parts: list[float] = []
    for weight, val in ((W_KV, kv_n), (W_QUEUE, queue_n), (W_MEM, mem_pressure_n), (W_UTIL, util_n), (W_LAT, lat_n)):
        parts.append(weight * (NEUTRAL if val is None else val))
    score = sum(parts)

    healthy = status in (None, "ok", "unsupported", "disabled")  # unknown/absent poller != unhealthy
    if not healthy:
        score += HEALTH_PENALTY
    if kv_pct is not None and kv_pct >= SATURATED_KV:
        score += 3.0   # saturated KV cache: new requests will stall behind preemption

    reasons.update(
        status=status or "unknown",
        kv_cache_pct=None if kv_pct is None else round(kv_pct, 1),
        waiting=None if waiting is None else int(waiting),
        free_vram=None if free_vram is None else f"{free_vram * 100:.0f}%",
        gpu_util=None if gpu_util_pct is None else f"{gpu_util_pct:.0f}%",
        gpu_temp_c=None if gpu_temp is None else round(gpu_temp),
        avg_latency_s=round(avg_latency_s, 3) if avg_latency_s else None,
        inflight=inflight,
        reason="healthy" if healthy else f"unhealthy:{status}",
    )
    return CandidateScore(backend=backend, score=round(score, 4), reasons=reasons)


def select_gpu_aware(backends, model, snapshot, get_latencies, get_inflight, store=None) -> tuple[Any, list[CandidateScore]]:
    """Rank backends; returns (chosen_backend, all scores sorted best-first).

    ``get_latencies(backend)->float`` and ``get_inflight(backend)->int`` are injected so
    this module stays free of config/live-stats imports (unit-testable).
    """
    scored = [
        score_backend(b, model, snapshot, float(get_latencies(b) or 0.0), int(get_inflight(b) or 0))
        for b in backends
    ]
    ranked = sorted(scored, key=lambda c: (c.score, -max(1, int(getattr(c.backend, "weight", 100) or 100))))
    return ranked[0].backend, ranked
