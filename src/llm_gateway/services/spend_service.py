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

from ..models import SpendRecord, Owner, APIKey, SystemSetting

logger = logging.getLogger(__name__)

# Default pricing (USD per 1K tokens).  Admins can override via
# SystemSetting key=MODEL_PRICING with a JSON dict.
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    # OpenAI
    "gpt-4o":            {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini":       {"input": 0.00015, "output": 0.0006},
    "gpt-4.1":           {"input": 0.002, "output": 0.008},
    "gpt-4.1-mini":      {"input": 0.0004, "output": 0.0016},
    "gpt-4.1-nano":      {"input": 0.0001, "output": 0.0004},
    "gpt-4-turbo":       {"input": 0.01, "output": 0.03},
    "gpt-3.5-turbo":     {"input": 0.0005, "output": 0.0015},
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
    # Groq (self-hosted inference — effectively free per token)
    "llama-3.3-70b-versatile": {"input": 0.00059, "output": 0.00079},
    # Local (vLLM / Ollama — zero cost)
    "_default_local":    {"input": 0.0, "output": 0.0},
}


async def _load_pricing(session: AsyncSession) -> dict:
    """Load pricing table: DB override merged on top of defaults."""
    try:
        result = await session.execute(
            select(SystemSetting).where(SystemSetting.key == "MODEL_PRICING")
        )
        setting = result.scalars().first()
        if setting and setting.value:
            custom = json.loads(setting.value)
            merged = dict(DEFAULT_PRICING)
            merged.update(custom)
            return merged
    except Exception as exc:
        logger.warning("Failed to load MODEL_PRICING from DB: %s", exc)
    return DEFAULT_PRICING


def _get_model_cost(pricing: dict, model: str, provider: str) -> dict[str, float]:
    """Resolve per-1K-token pricing for a model."""
    if model in pricing:
        return pricing[model]
    # Local providers are free
    if provider in ("ollama", "vllm"):
        return pricing.get("_default_local", {"input": 0.0, "output": 0.0})
    # Unknown cloud model — use a conservative default
    return {"input": 0.003, "output": 0.015}


def calculate_cost(pricing: dict, model: str, provider: str,
                   input_tokens: int, output_tokens: int) -> float:
    """Calculate USD cost for a request."""
    rates = _get_model_cost(pricing, model, provider)
    cost = (input_tokens / 1000.0) * rates["input"] + \
           (output_tokens / 1000.0) * rates["output"]
    return round(cost, 8)


async def record_spend(
    session: AsyncSession,
    owner_id: str,
    api_key_hash: Optional[str],
    model: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
) -> SpendRecord:
    """Record a spend event and return the record."""
    pricing = await _load_pricing(session)
    cost = calculate_cost(pricing, model, provider, input_tokens, output_tokens)

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
