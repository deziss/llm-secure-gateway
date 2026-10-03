"""Aggregates for the dashboard's summary cards.

One read-only endpoint so the page makes a single request every 30 s instead
of one per card. Everything here is a database aggregate or in-process state
(circuit breaker, latency tracker): no network I/O, so a dead backend can't
slow it down. The live traffic numbers stay on the SSE stream in admin.py.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import APIKey, AuditLog, CachedResponse, LLMBackend, Owner, SpendRecord
from ..services import ConfigService, get_config_service
from .admin import require_manager

router = APIRouter(prefix="/admin/dashboard", tags=["admin-dashboard"])

BUDGET_WARN_RATIO = 0.8
EXPIRY_WINDOW = timedelta(days=7)
SPEND_DAYS = 14


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


async def _spend(session: AsyncSession, now: datetime) -> dict:
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = today.replace(day=1)
    prev_month = (month - timedelta(days=1)).replace(day=1)
    # Same number of elapsed days into last month, for a like-for-like delta.
    prev_month_cut = min(prev_month + (now - month), month)

    def window(start, end):
        return and_(SpendRecord.created_at >= start, SpendRecord.created_at < end)

    totals = select(
        func.coalesce(func.sum(SpendRecord.cost_usd), 0.0),
        func.coalesce(func.sum(SpendRecord.input_tokens), 0),
        func.coalesce(func.sum(SpendRecord.output_tokens), 0),
        func.count(SpendRecord.id),
    )

    async def total(start, end):
        cost, tin, tout, n = (await session.execute(totals.where(window(start, end)))).one()
        return {"cost_usd": round(float(cost), 6), "input_tokens": int(tin), "output_tokens": int(tout), "requests": int(n)}

    tomorrow = today + timedelta(days=1)
    day_col = func.date(SpendRecord.created_at)
    since = today - timedelta(days=SPEND_DAYS - 1)
    rows = (
        await session.execute(
            select(day_col, func.sum(SpendRecord.cost_usd), func.count(SpendRecord.id))
            .where(SpendRecord.created_at >= since)
            .group_by(day_col)
        )
    ).all()
    by_day = {str(d)[:10]: (float(c or 0), int(n)) for d, c, n in rows}
    daily = []
    for i in range(SPEND_DAYS):
        d = (since + timedelta(days=i)).date().isoformat()
        cost, n = by_day.get(d, (0.0, 0))
        daily.append({"date": d, "cost_usd": round(cost, 6), "requests": n})

    return {
        "today": await total(today, tomorrow),
        "yesterday": await total(today - timedelta(days=1), today),
        "month": await total(month, tomorrow),
        "prev_month_to_date": await total(prev_month, prev_month_cut),
        "daily": daily,
    }


async def _top_models(session: AsyncSession, month_start: datetime) -> list:
    rows = (
        await session.execute(
            select(
                SpendRecord.model,
                func.count(SpendRecord.id).label("requests"),
                func.sum(SpendRecord.input_tokens + SpendRecord.output_tokens),
                func.sum(SpendRecord.cost_usd),
            )
            .where(SpendRecord.created_at >= month_start)
            .group_by(SpendRecord.model)
            .order_by(func.count(SpendRecord.id).desc())
            .limit(5)
        )
    ).all()
    return [
        {"model": m, "requests": int(n), "tokens": int(t or 0), "cost_usd": round(float(c or 0), 6)}
        for m, n, t, c in rows
    ]


async def _alerts(session: AsyncSession, now: datetime, month_start: datetime) -> dict:
    month_cond = SpendRecord.created_at >= month_start

    owner_rows = (
        await session.execute(
            select(Owner.id, Owner.name, Owner.monthly_budget_usd, func.coalesce(func.sum(SpendRecord.cost_usd), 0.0))
            .join(SpendRecord, and_(SpendRecord.owner_id == Owner.id, month_cond), isouter=True)
            .where(Owner.monthly_budget_usd.is_not(None), Owner.monthly_budget_usd > 0)
            .group_by(Owner.id, Owner.name, Owner.monthly_budget_usd)
        )
    ).all()
    key_rows = (
        await session.execute(
            select(APIKey.prefix, APIKey.owner_id, APIKey.monthly_budget_usd, func.coalesce(func.sum(SpendRecord.cost_usd), 0.0))
            .join(SpendRecord, and_(SpendRecord.api_key_hash == APIKey.key_hash, month_cond), isouter=True)
            .where(APIKey.is_active.is_(True), APIKey.monthly_budget_usd.is_not(None), APIKey.monthly_budget_usd > 0)
            .group_by(APIKey.key_hash, APIKey.prefix, APIKey.owner_id, APIKey.monthly_budget_usd)
        )
    ).all()

    budgets = []
    for oid, name, budget, spent in owner_rows:
        ratio = float(spent) / float(budget)
        if ratio >= BUDGET_WARN_RATIO:
            budgets.append({"kind": "owner", "label": name or oid, "owner_id": oid,
                            "spent_usd": round(float(spent), 4), "budget_usd": float(budget), "ratio": round(ratio, 4)})
    for prefix, oid, budget, spent in key_rows:
        ratio = float(spent) / float(budget)
        if ratio >= BUDGET_WARN_RATIO:
            budgets.append({"kind": "key", "label": f"{prefix}…", "owner_id": oid,
                            "spent_usd": round(float(spent), 4), "budget_usd": float(budget), "ratio": round(ratio, 4)})
    budgets.sort(key=lambda b: b["ratio"], reverse=True)

    expiring = (
        await session.execute(
            select(APIKey.prefix, APIKey.owner_id, APIKey.expires_at)
            .where(APIKey.is_active.is_(True), APIKey.expires_at.is_not(None),
                   APIKey.expires_at >= now, APIKey.expires_at < now + EXPIRY_WINDOW)
            .order_by(APIKey.expires_at)
            .limit(10)
        )
    ).all()
    return {
        "budgets": budgets[:10],
        "expiring_keys": [{"label": f"{p}…", "owner_id": o, "expires_at": _iso(e)} for p, o, e in expiring],
    }


async def _cache(session: AsyncSession, now: datetime) -> dict:
    entries, hits, saved = (
        await session.execute(
            select(
                func.count(CachedResponse.cache_key),
                func.coalesce(func.sum(CachedResponse.hit_count), 0),
                func.coalesce(func.sum(CachedResponse.hit_count * (CachedResponse.input_tokens + CachedResponse.output_tokens)), 0),
            ).where(CachedResponse.expires_at > now)
        )
    ).one()
    return {"entries": int(entries), "hits": int(hits), "tokens_saved": int(saved)}


async def _security(session: AsyncSession, service: ConfigService, now: datetime) -> dict:
    enabled = str(await service.get_setting(session, "ENABLE_AUDIT_DB", "false")).lower() == "true"
    since = now - timedelta(hours=24)
    flagged = and_(AuditLog.timestamp >= since, AuditLog.decision.in_(["deny", "error"]))
    counts = dict(
        (await session.execute(select(AuditLog.decision, func.count(AuditLog.id)).where(flagged).group_by(AuditLog.decision))).all()
    )
    recent = (
        await session.execute(select(AuditLog).where(flagged).order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).limit(5))
    ).scalars().all()
    return {
        "audit_enabled": enabled,
        "denied_24h": int(counts.get("deny", 0)),
        "errors_24h": int(counts.get("error", 0)),
        "recent": [
            {"timestamp": _iso(r.timestamp), "event_type": r.event_type, "identity": r.identity,
             "resource": r.resource, "decision": r.decision}
            for r in recent
        ],
    }


async def _backends(session: AsyncSession) -> list:
    from ..proxy_helpers import get_backend_avg_latency, get_circuit_breaker

    circuits = get_circuit_breaker().get_status()
    rows = (await session.execute(select(LLMBackend).order_by(LLMBackend.name))).scalars().all()
    out = []
    for b in rows:
        # The circuit breaker only knows URLs that have carried traffic since
        # the process started; "idle" means no evidence either way.
        state = circuits.get(b.base_url, "idle")
        latency = get_backend_avg_latency(b.base_url)
        out.append({
            "name": b.name,
            "backend_type": b.backend_type.value if hasattr(b.backend_type, "value") else str(b.backend_type),
            "models": len(b.models or []),
            "state": state,
            "avg_latency_ms": round(latency * 1000) if latency else None,
        })
    return out


@router.get("/summary")
async def dashboard_summary(
    session: AsyncSession = Depends(get_session),
    service: ConfigService = Depends(get_config_service),
    user=Depends(require_manager),
) -> dict:
    now = _now()
    spend = await _spend(session, now)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        "generated_at": _iso(now),
        "spend": spend,
        "top_models": await _top_models(session, month_start),
        "alerts": await _alerts(session, now, month_start),
        "cache": await _cache(session, now),
        "security": await _security(session, service, now),
        "backends": await _backends(session),
    }
