"""Aggregates for the dashboard's summary cards.

One read-only endpoint so the page makes a single request every 30 s instead
of one per card. Everything here is a database aggregate or in-process state
(circuit breaker, latency tracker): no network I/O, so a dead backend can't
slow it down. The live traffic numbers stay on the SSE stream in admin.py.
"""
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import APIKey, AuditLog, CachedResponse, LLMBackend, ModelTelemetryRollup, Owner, SpendRecord
from ..services import ConfigService, get_config_service
from .admin import require_manager

router = APIRouter(prefix="/admin/dashboard", tags=["admin-dashboard"])

BUDGET_WARN_RATIO = 0.8
EXPIRY_WINDOW = timedelta(days=7)
SPEND_DAYS = 14

# Process start, used for the dashboard uptime tile. The module is imported by
# the app at boot, so this is close enough to "gateway started".
_PROCESS_START = time.monotonic()

# Global time-range selector values accepted by the summary/traffic endpoints.
RANGE_HOURS = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}

# Admin JSON is per-user session data: never cache it in a shared proxy.
_CACHE_CTRL = "private, max-age=10, stale-while-revalidate=45"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _cache_headers(payload: dict, response: Optional[Response], request: Optional[Request]):
    """Attach ETag + Cache-Control; return a 304 Response when the client's
    ETag still matches. ``request``/``response`` are None when the endpoint
    functions are called directly from tests, in which case the plain dict is
    returned untouched."""
    if request is None or response is None:
        return None
    etag = '"' + hashlib.sha1(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24] + '"'
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = _CACHE_CTRL
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": _CACHE_CTRL})
    return None


def _bucket_granularity(hours: int):
    """(bucket timedelta, granularity name, bucket count) for the traffic series."""
    if hours <= 2:
        return timedelta(minutes=1), "minute", 60
    if hours <= 48:
        return timedelta(hours=1), "hour", hours
    return timedelta(days=1), "day", max(1, hours // 24)


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


async def _rollup_rows(session: AsyncSession, since: datetime, server: Optional[str], model: Optional[str]):
    from ..observability.collect.poller import flush_telemetry_rollups

    try:
        await flush_telemetry_rollups(session)
    except Exception:
        pass

    stmt = select(ModelTelemetryRollup).where(ModelTelemetryRollup.bucket_time >= since)
    if server:
        stmt = stmt.where(ModelTelemetryRollup.server == server)
    if model:
        stmt = stmt.where(ModelTelemetryRollup.model == model)
    stmt = stmt.order_by(ModelTelemetryRollup.bucket_time.asc())
    return (await session.execute(stmt)).scalars().all()


def _ttft_weighted(rows, field: str) -> Optional[float]:
    """Count-weighted mean of a per-bucket percentile field. Bucket p50/p90
    values can't be re-percentiled exactly, so the UI shows these as
    approximations ("~p50"). Weighting by ttft_count keeps quiet minutes from
    skewing the window average."""
    num = den = 0.0
    for r in rows:
        v = getattr(r, field, None)
        if v is None or r.ttft_count <= 0:
            continue
        num += float(v) * r.ttft_count
        den += r.ttft_count
    return round(num / den, 1) if den else None


async def _traffic_range(session: AsyncSession, now: datetime, range_key: str,
                         server: Optional[str] = None, model: Optional[str] = None) -> dict:
    """Bucketed telemetry series for the selected range (1h/24h/7d/30d)."""
    hours = RANGE_HOURS.get(range_key, 24)
    bucket, gran, n_buckets = _bucket_granularity(hours)
    # The grid ends in the bucket that contains `now` and holds a fixed number
    # of buckets, aligned so labels stay readable (minute/hour boundaries, or
    # midnight for days). The query range starts with the first bucket.
    end = now
    if gran == "minute":
        end = now.replace(second=0, microsecond=0)
    elif gran == "hour":
        end = now.replace(minute=0, second=0, microsecond=0)
    else:
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - bucket * (n_buckets - 1)
    since = start

    rows = await _rollup_rows(session, since, server, model)

    step_s = bucket.total_seconds()
    acc: dict[int, dict] = {}
    for r in rows:
        idx = int((r.bucket_time - start).total_seconds() // step_s)
        if idx < 0:
            continue
        b = acc.setdefault(idx, {"requests": 0, "tokens": 0, "errors": 0,
                                "ttft_sum": 0.0, "ttft_count": 0, "p50": [], "p90": []})
        b["requests"] += r.requests
        b["tokens"] += r.total_tokens
        b["errors"] += r.errors
        b["ttft_sum"] += r.ttft_sum_ms
        b["ttft_count"] += r.ttft_count
        if r.ttft_p50_ms is not None and r.ttft_count > 0:
            b["p50"].append((r.ttft_p50_ms, r.ttft_count))
        if r.ttft_p90_ms is not None and r.ttft_count > 0:
            b["p90"].append((r.ttft_p90_ms, r.ttft_count))

    def wmean(pairs):
        den = sum(c for _, c in pairs)
        return round(sum(v * c for v, c in pairs) / den, 1) if den else None

    series = []
    for i in range(n_buckets):
        t = start + bucket * i
        b = acc.get(i)
        series.append({
            "hour": t.isoformat() + "Z",  # kept for backwards compatibility
            "timestamp": t.isoformat() + "Z",
            "granularity": gran,
            "requests": b["requests"] if b else 0,
            "tokens": b["tokens"] if b else 0,
            "total_tokens": b["tokens"] if b else 0,
            "errors": b["errors"] if b else 0,
            "avg_ttft_ms": round(b["ttft_sum"] / b["ttft_count"], 1) if b and b["ttft_count"] else None,
            "ttft_p50_ms": wmean(b["p50"]) if b else None,
            "ttft_p90_ms": wmean(b["p90"]) if b else None,
        })

    tot_reqs = sum(r.requests for r in rows)
    tot_toks = sum(r.total_tokens for r in rows)
    tot_errors = sum(r.errors for r in rows)
    tot_ttft_cnt = sum(r.ttft_count for r in rows)
    overall_ttft = round(sum(r.ttft_sum_ms for r in rows) / tot_ttft_cnt, 1) if tot_ttft_cnt > 0 else None
    window_s = (now - start).total_seconds()

    return {
        "range": range_key,
        "granularity": gran,
        "start_time": start.isoformat() + "Z",
        "end_time": now.isoformat() + "Z",
        "total_requests": tot_reqs,
        "total_tokens": tot_toks,
        "total_errors": tot_errors,
        "error_rate": round(tot_errors / tot_reqs, 4) if tot_reqs else 0.0,
        "avg_rps": round(tot_reqs / window_s, 4) if window_s > 0 else 0.0,
        "peak_rps": max(((b["requests"] / step_s) for b in acc.values()), default=0.0),
        "avg_ttft_ms": overall_ttft,
        "ttft_p50_ms": _ttft_weighted(rows, "ttft_p50_ms"),
        "ttft_p90_ms": _ttft_weighted(rows, "ttft_p90_ms"),
        "hourly": series,
    }


async def _headline(session: AsyncSession, now: datetime, range_key: str) -> dict:
    """Above-the-fold KPIs for the selected range, from telemetry rollups."""
    hours = RANGE_HOURS.get(range_key, 24)
    since = now - timedelta(hours=hours)
    prev = since - timedelta(hours=hours)
    cur_rows = await _rollup_rows(session, since, None, None)
    prev_rows = (await session.execute(
        select(ModelTelemetryRollup).where(
            ModelTelemetryRollup.bucket_time >= prev, ModelTelemetryRollup.bucket_time < since)
    )).scalars().all()

    def totals(rows_):
        reqs = sum(r.requests for r in rows_)
        errs = sum(r.errors for r in rows_)
        toks = sum(r.total_tokens for r in rows_)
        return reqs, errs, toks

    reqs, errs, toks = totals(cur_rows)
    preq, perr, ptoks = totals(prev_rows)
    window_s = hours * 3600

    def delta_pct(cur, prev_):
        if prev_ <= 0:
            return None
        return round((cur - prev_) / prev_ * 100, 1)

    return {
        "range": range_key,
        "requests": reqs,
        "errors": errs,
        "error_rate": round(errs / reqs, 4) if reqs else 0.0,
        "tokens": toks,
        "avg_rps": round(reqs / window_s, 4),
        "requests_delta_pct": delta_pct(reqs, preq),
        "tokens_delta_pct": delta_pct(toks, ptoks),
        "ttft_p50_ms": _ttft_weighted(cur_rows, "ttft_p50_ms"),
        "ttft_p90_ms": _ttft_weighted(cur_rows, "ttft_p90_ms"),
        "avg_ttft_ms": (round(sum(r.ttft_sum_ms for r in cur_rows) / sum(r.ttft_count for r in cur_rows), 1)
                        if sum(r.ttft_count for r in cur_rows) else None),
    }


@router.get("/traffic-24h")
async def dashboard_traffic_24h(
    request: Request = None,
    response: Response = None,
    range: Optional[str] = None,
    server: Optional[str] = None,
    model: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_manager),
):
    """Bucketed traffic series. ``range`` is 1h/24h/7d/30d (default 24h);
    the path keeps its historical name for compatibility."""
    now = _now()
    payload = await _traffic_range(session, now, range or "24h", server, model)
    cached = _cache_headers(payload, response, request)
    return cached if cached is not None else payload


@router.get("/summary")
async def dashboard_summary(
    request: Request = None,
    response: Response = None,
    range: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    service: ConfigService = Depends(get_config_service),
    user=Depends(require_manager),
):
    range_key = range if range in RANGE_HOURS else "24h"
    now = _now()
    spend = await _spend(session, now)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    traffic_24h = await _traffic_range(session, now, "24h")
    payload = {
        "generated_at": _iso(now),
        "uptime_seconds": int(time.monotonic() - _PROCESS_START),
        "range": range_key,
        "headline": await _headline(session, now, range_key),
        "spend": spend,
        "top_models": await _top_models(session, month_start),
        "alerts": await _alerts(session, now, month_start),
        "cache": await _cache(session, now),
        "security": await _security(session, service, now),
        "backends": await _backends(session),
        "traffic_24h": traffic_24h,
    }
    cached = _cache_headers(payload, response, request)
    return cached if cached is not None else payload
