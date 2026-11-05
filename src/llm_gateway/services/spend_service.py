"""Spend tracking and budget enforcement service.

Tracks per-request costs using a configurable model pricing table,
enforces monthly budget caps per Owner and per API key.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select, extract
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import SpendRecord, Owner, APIKey, SystemSetting, ModelPricing
from .pricing_catalog import (
    PROVIDER_PRICING_CATALOG,
    lookup_catalog_rate,
    auto_seed_pricing_for_provider,
)

logger = logging.getLogger(__name__)

# Default pricing fallback (USD per 1K tokens) for legacy compatibility.
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    # OpenAI
    "gpt-4o":            {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini":       {"input": 0.00015, "output": 0.0006},
    "gpt-4.1":           {"input": 0.002, "output": 0.008},
    "gpt-4.1-mini":      {"input": 0.0004, "output": 0.0016},
    "gpt-4.1-nano":      {"input": 0.0001, "output": 0.0004},
    "gpt-4-turbo":       {"input": 0.01, "output": 0.03},
    "gpt-3.5-turbo":     {"input": 0.0005, "output": 0.0015},
    "o1":                {"input": 0.015, "output": 0.06},
    "o1-mini":           {"input": 0.0011, "output": 0.0044},
    "o3":                {"input": 0.01, "output": 0.04},
    "o3-mini":           {"input": 0.0011, "output": 0.0044},
    "o4-mini":           {"input": 0.0011, "output": 0.0044},
    # Anthropic
    "claude-sonnet-4-20250514":   {"input": 0.003, "output": 0.015},
    "claude-3-5-sonnet-20241022": {"input": 0.003, "output": 0.015},
    "claude-3-5-haiku-20241022":  {"input": 0.0008, "output": 0.004},
    "claude-3-opus-20240229":     {"input": 0.015, "output": 0.075},
    # Google
    "gemini-2.5-pro":    {"input": 0.00125, "output": 0.01},
    "gemini-2.5-flash":  {"input": 0.00015, "output": 0.0006},
    "gemini-2.0-flash":  {"input": 0.0001, "output": 0.0004},
    # Groq
    "llama-3.3-70b-versatile": {"input": 0.00059, "output": 0.00079},
    # Local (vLLM / Ollama — zero cost)
    "_default_local":    {"input": 0.0, "output": 0.0},
}


async def _load_pricing(session: AsyncSession) -> dict:
    """Load pricing table: DB ModelPricing overrides merged on top of defaults."""
    merged: dict = dict(DEFAULT_PRICING)

    # 1. First, check legacy SystemSetting key=MODEL_PRICING if present
    try:
        result = await session.execute(
            select(SystemSetting).where(SystemSetting.key == "MODEL_PRICING")
        )
        setting = result.scalars().first()
        if setting and setting.value:
            custom = json.loads(setting.value)
            merged.update(custom)
    except Exception as exc:
        logger.debug("No legacy MODEL_PRICING setting found: %s", exc)

    # 2. Primary: Load all explicit ModelPricing rules from DB
    try:
        res = await session.execute(
            select(ModelPricing).order_by(ModelPricing.id.asc())
        )
        pricing_rows = res.scalars().all()
        for row in pricing_rows:
            entry = {
                "input_per_1m": row.input_cost_per_1m,
                "output_per_1m": row.output_cost_per_1m,
                "cached_input_per_1m": row.cached_input_cost_per_1m,
                "input": row.input_cost_per_1m / 1000.0,
                "output": row.output_cost_per_1m / 1000.0,
                "source": row.source,
                "provider": row.provider,
            }
            # Index by (model, provider) and model
            merged[(row.model, row.provider)] = entry
            # Provider-specific overrides have higher priority than generic
            if row.provider == "*" or row.model not in merged:
                merged[row.model] = entry
    except Exception as exc:
        logger.warning("Failed to load ModelPricing from DB: %s", exc)

    return merged


def _get_model_cost(pricing: dict, model: str, provider: str) -> dict:
    """Resolve token pricing for a model."""
    p_clean = (provider or "").lower().strip()
    if p_clean == "google":
        p_clean = "gemini"

    # 1. Exact (model, provider) match
    if (model, p_clean) in pricing:
        return pricing[(model, p_clean)]

    # 2. Exact (model, "*") match
    if (model, "*") in pricing:
        return pricing[(model, "*")]

    # 3. Model name match
    if model in pricing:
        return pricing[model]

    # 4. Local providers are free
    if p_clean in ("ollama", "vllm", "llamacpp"):
        return {
            "input_per_1m": 0.0,
            "output_per_1m": 0.0,
            "cached_input_per_1m": 0.0,
            "input": 0.0,
            "output": 0.0,
        }

    # 5. Check provider catalog registry
    catalog_rate = lookup_catalog_rate(model, p_clean)
    if catalog_rate:
        return {
            "input_per_1m": catalog_rate["input_cost_per_1m"],
            "output_per_1m": catalog_rate["output_cost_per_1m"],
            "cached_input_per_1m": catalog_rate.get("cached_input_cost_per_1m"),
            "input": catalog_rate["input_cost_per_1m"] / 1000.0,
            "output": catalog_rate["output_cost_per_1m"] / 1000.0,
        }

    # 6. Unknown cloud model — conservative default: $3.00/1M in, $15.00/1M out
    return {
        "input_per_1m": 3.00,
        "output_per_1m": 15.00,
        "cached_input_per_1m": 1.50,
        "input": 0.003,
        "output": 0.015,
    }


def calculate_cost(
    pricing: dict,
    model: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
) -> float:
    """Calculate USD cost for a request using 1M-token standard rates."""
    rates = _get_model_cost(pricing, model, provider)

    rate_in_1m = rates.get("input_per_1m")
    if rate_in_1m is None:
        rate_in_1m = rates.get("input", 0.003) * 1000.0

    rate_out_1m = rates.get("output_per_1m")
    if rate_out_1m is None:
        rate_out_1m = rates.get("output", 0.015) * 1000.0

    rate_cached_1m = rates.get("cached_input_cost_per_1m")
    if rate_cached_1m is None:
        rate_cached_1m = rates.get("cached_input_per_1m")
    if rate_cached_1m is None:
        rate_cached_1m = rate_in_1m * 0.5 if cached_tokens > 0 else rate_in_1m

    regular_input = max(0, input_tokens - cached_tokens)
    cost = (
        (regular_input / 1_000_000.0) * rate_in_1m
        + (cached_tokens / 1_000_000.0) * rate_cached_1m
        + (output_tokens / 1_000_000.0) * rate_out_1m
    )
    return round(cost, 8)


async def record_spend(
    session: AsyncSession,
    owner_id: str,
    api_key_hash: Optional[str],
    model: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
) -> SpendRecord:
    """Record a spend event and return the record."""
    pricing = await _load_pricing(session)
    cost = calculate_cost(
        pricing, model, provider, input_tokens, output_tokens, cached_tokens=cached_tokens
    )

    record = SpendRecord(
        owner_id=owner_id,
        api_key_hash=api_key_hash,
        model=model,
        provider=provider,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
    )
    session.add(record)
    # Don't commit here — let the caller manage the transaction
    return record


async def get_monthly_spend(session: AsyncSession, owner_id: str) -> float:
    """Sum cost_usd for the current calendar month for an owner."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    result = await session.execute(
        select(func.coalesce(func.sum(SpendRecord.cost_usd), 0.0)).where(
            SpendRecord.owner_id == owner_id,
            extract("year", SpendRecord.created_at) == now.year,
            extract("month", SpendRecord.created_at) == now.month,
        )
    )
    return float(result.scalar_one())


async def get_monthly_spend_by_key(session: AsyncSession, key_hash: str) -> float:
    """Sum cost_usd for the current calendar month for an API key."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    result = await session.execute(
        select(func.coalesce(func.sum(SpendRecord.cost_usd), 0.0)).where(
            SpendRecord.api_key_hash == key_hash,
            extract("year", SpendRecord.created_at) == now.year,
            extract("month", SpendRecord.created_at) == now.month,
        )
    )
    return float(result.scalar_one())


async def check_budget(
    session: AsyncSession, owner_id: str, api_key_hash: Optional[str]
) -> None:
    """Raise HTTP 429 if monthly budget is exceeded for owner or key."""
    # Check owner budget
    if owner_id and owner_id != "anonymous":
        result = await session.execute(
            select(Owner.monthly_budget_usd).where(Owner.id == owner_id)
        )
        budget = result.scalar_one_or_none()
        if budget is not None:
            spent = await get_monthly_spend(session, owner_id)
            if spent >= budget:
                raise HTTPException(
                    status_code=429,
                    detail=f"Monthly budget exceeded: ${spent:.2f} / ${budget:.2f}",
                    headers={"X-Budget-Spent": f"{spent:.4f}", "X-Budget-Limit": f"{budget:.2f}"},
                )

    # Check API key budget
    if api_key_hash:
        result = await session.execute(
            select(APIKey.monthly_budget_usd).where(APIKey.key_hash == api_key_hash)
        )
        budget = result.scalar_one_or_none()
        if budget is not None:
            spent = await get_monthly_spend_by_key(session, api_key_hash)
            if spent >= budget:
                raise HTTPException(
                    status_code=429,
                    detail=f"API key monthly budget exceeded: ${spent:.2f} / ${budget:.2f}",
                    headers={"X-Budget-Spent": f"{spent:.4f}", "X-Budget-Limit": f"{budget:.2f}"},
                )


async def get_spend_summary(session: AsyncSession) -> list[dict]:
    """Monthly spend summary grouped by owner."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    result = await session.execute(
        select(
            SpendRecord.owner_id,
            func.sum(SpendRecord.cost_usd).label("total_cost"),
            func.sum(SpendRecord.input_tokens).label("total_input"),
            func.sum(SpendRecord.output_tokens).label("total_output"),
            func.count(SpendRecord.id).label("request_count"),
        ).where(
            extract("year", SpendRecord.created_at) == now.year,
            extract("month", SpendRecord.created_at) == now.month,
        ).group_by(SpendRecord.owner_id)
    )
    return [
        {
            "owner_id": row.owner_id,
            "total_cost": round(float(row.total_cost), 4),
            "total_input_tokens": int(row.total_input),
            "total_output_tokens": int(row.total_output),
            "request_count": int(row.request_count),
        }
        for row in result.fetchall()
    ]


async def get_spend_by_model(session: AsyncSession) -> list[dict]:
    """Monthly spend summary grouped by model."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    result = await session.execute(
        select(
            SpendRecord.model,
            SpendRecord.provider,
            func.sum(SpendRecord.cost_usd).label("total_cost"),
            func.count(SpendRecord.id).label("request_count"),
        ).where(
            extract("year", SpendRecord.created_at) == now.year,
            extract("month", SpendRecord.created_at) == now.month,
        ).group_by(SpendRecord.model, SpendRecord.provider)
    )
    return [
        {
            "model": row.model,
            "provider": row.provider,
            "total_cost": round(float(row.total_cost), 4),
            "request_count": int(row.request_count),
        }
        for row in result.fetchall()
    ]


async def list_model_pricing(session: AsyncSession) -> list[ModelPricing]:
    """List all model pricing rules from DB. Auto-seeds defaults if empty."""
    res = await session.execute(
        select(ModelPricing).order_by(ModelPricing.model.asc(), ModelPricing.provider.asc())
    )
    rows = list(res.scalars().all())
    if not rows:
        # Seed default catalog pricing for popular providers
        for p in ["openai", "anthropic", "gemini", "groq", "deepseek"]:
            await auto_seed_pricing_for_provider(session, p)
        res = await session.execute(
            select(ModelPricing).order_by(ModelPricing.model.asc(), ModelPricing.provider.asc())
        )
        rows = list(res.scalars().all())
    return rows


async def upsert_model_pricing(
    session: AsyncSession,
    model: str,
    provider: str,
    input_cost_per_1m: float,
    output_cost_per_1m: float,
    cached_input_cost_per_1m: Optional[float] = None,
    description: Optional[str] = None,
) -> ModelPricing:
    """Create or manually override pricing for a model."""
    m_clean = model.strip()
    p_clean = (provider or "*").strip().lower()

    res = await session.execute(
        select(ModelPricing).where(
            ModelPricing.model == m_clean,
            ModelPricing.provider == p_clean,
        )
    )
    existing = res.scalars().first()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if existing:
        existing.input_cost_per_1m = float(input_cost_per_1m)
        existing.output_cost_per_1m = float(output_cost_per_1m)
        if cached_input_cost_per_1m is not None:
            existing.cached_input_cost_per_1m = float(cached_input_cost_per_1m)
        if description is not None:
            existing.description = description
        existing.source = "manual"
        existing.updated_at = now
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        return existing

    new_rule = ModelPricing(
        model=m_clean,
        provider=p_clean,
        input_cost_per_1m=float(input_cost_per_1m),
        output_cost_per_1m=float(output_cost_per_1m),
        cached_input_cost_per_1m=float(cached_input_cost_per_1m) if cached_input_cost_per_1m is not None else None,
        source="manual",
        description=description,
        created_at=now,
        updated_at=now,
    )
    session.add(new_rule)
    await session.commit()
    await session.refresh(new_rule)
    return new_rule


async def delete_model_pricing(session: AsyncSession, pricing_id: int) -> bool:
    """Delete a model pricing rule by ID."""
    res = await session.execute(
        select(ModelPricing).where(ModelPricing.id == pricing_id)
    )
    item = res.scalars().first()
    if not item:
        return False
    await session.delete(item)
    await session.commit()
    return True


async def sync_all_catalog_pricing(session: AsyncSession, provider: Optional[str] = None) -> list[ModelPricing]:
    """Sync standard catalog rates for one or all providers."""
    providers_to_sync = [provider] if provider else list(PROVIDER_PRICING_CATALOG.keys())
    all_seeded = []
    for p in providers_to_sync:
        if p in PROVIDER_PRICING_CATALOG:
            seeded = await auto_seed_pricing_for_provider(session, p)
            all_seeded.extend(seeded)
    return all_seeded

