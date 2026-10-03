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
