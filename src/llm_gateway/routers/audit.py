"""Read API for the persistent audit trail (AuditLog), used by the Audit history page."""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import AuditLog
from .admin import require_manager

router = APIRouter(prefix="/admin/audit", tags=["admin-audit"])


@router.get("")
async def list_audit_events(
    event_type: Optional[str] = Query(None, description="Exact event type, e.g. key_created"),
    decision: Optional[str] = Query(None, description="allow | deny | error"),
    q: Optional[str] = Query(None, max_length=200, description="Search identity, resource or IP"),
    hours: Optional[int] = Query(None, ge=1, le=24 * 365, description="Only the last N hours"),
    hide_allowed_requests: bool = Query(
        True,
        description="Hide routine allowed policy_eval rows (one per request) so admin actions stand out",
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    user=Depends(require_manager),
) -> dict:
    conds = []
    if event_type:
        conds.append(AuditLog.event_type == event_type)
    if decision:
        conds.append(AuditLog.decision == decision)
    if q:
        like = f"%{q.strip()}%"
        conds.append(or_(AuditLog.identity.ilike(like), AuditLog.resource.ilike(like), AuditLog.ip_address.ilike(like)))
    if hours:
        conds.append(AuditLog.timestamp >= datetime.utcnow() - timedelta(hours=hours))
    if hide_allowed_requests and not event_type:
        conds.append(or_(AuditLog.event_type != "policy_eval", AuditLog.decision != "allow"))

    total = (await session.execute(select(func.count()).select_from(AuditLog).where(*conds))).scalar_one()
    rows = (
        await session.execute(
            select(AuditLog).where(*conds).order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).offset(skip).limit(limit)
        )
    ).scalars().all()
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": [
            {
                "id": r.id,
                "timestamp": r.timestamp.isoformat() + "Z" if r.timestamp else None,
                "event_type": r.event_type,
                "identity": r.identity,
                "resource": r.resource,
                "decision": r.decision,
                "ip_address": r.ip_address,
                "metadata": r.metadata_json or {},
            }
            for r in rows
        ],
    }


@router.get("/event-types")
async def audit_event_types(session: AsyncSession = Depends(get_session), user=Depends(require_manager)) -> list[str]:
    """Distinct event types present, for the filter dropdown."""
    res = await session.execute(select(AuditLog.event_type).distinct().order_by(AuditLog.event_type))
    return [r for (r,) in res.all()]


@router.get("/compliance-report")
async def compliance_report(
    days: int = Query(30, ge=1, le=365, description="Reporting window in days"),
    session: AsyncSession = Depends(get_session),
    user=Depends(require_manager),
) -> dict:
    """Tenant compliance summary: denials, governance events and spend per owner.

    A compact, exportable governance snapshot (who used what, what was denied,
    budget pressure) built entirely from the audit + spend tables.
    """
    from ..models import SpendRecord

    since = datetime.utcnow() - timedelta(days=days)

    denials = (
        await session.execute(
            select(AuditLog.event_type, AuditLog.decision, func.count())
            .where(AuditLog.timestamp >= since, AuditLog.decision != "allow")
            .group_by(AuditLog.event_type, AuditLog.decision)
            .order_by(func.count().desc())
            .limit(50)
        )
    ).all()

    governance_events = (
        await session.execute(
            select(AuditLog.event_type, func.count())
            .where(
                AuditLog.timestamp >= since,
                AuditLog.event_type.in_(["budget_warning", "guardrail_block", "pii_detected", "backend_down", "backend_up"]),
            )
            .group_by(AuditLog.event_type)
        )
    ).all()

    top_denied = (
        await session.execute(
            select(AuditLog.identity, func.count().label("c"))
            .where(AuditLog.timestamp >= since, AuditLog.decision == "deny")
            .group_by(AuditLog.identity)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()

    spend_rows = (
        await session.execute(
            select(
                SpendRecord.owner_id,
                func.count(SpendRecord.id),
                func.coalesce(func.sum(SpendRecord.cost_usd), 0.0),
                func.coalesce(func.sum(SpendRecord.input_tokens + SpendRecord.output_tokens), 0),
            )
            .where(SpendRecord.created_at >= since)
            .group_by(SpendRecord.owner_id)
            .order_by(func.coalesce(func.sum(SpendRecord.cost_usd), 0.0).desc())
            .limit(50)
        )
    ).all()

    return {
        "window_days": days,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "denials": [{"event_type": e, "decision": d, "count": c} for e, d, c in denials],
        "governance_events": [{"event_type": e, "count": c} for e, c in governance_events],
        "top_denied_identities": [{"identity": i, "denials": c} for i, c in top_denied],
        "spend_by_owner": [
            {"owner_id": o, "requests": n, "cost_usd": round(float(cost), 4), "tokens": int(tok)}
            for o, n, cost, tok in spend_rows
        ],
    }
