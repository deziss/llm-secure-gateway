"""Admin API for the alert engine: rules, incidents, acknowledgement, summary."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import func, select

try:
    from sqlalchemy.ext.asyncio import AsyncSession

    from ..database import get_session
    from ..models import AlertIncident, AlertRule, _utcnow
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    AsyncSession = None  # type: ignore
    get_session = None  # type: ignore
    AlertIncident = AlertRule = _utcnow = None  # type: ignore

from .admin import require_admin

router = APIRouter(prefix="/admin/alerts", tags=["admin-alerts"])


class RulePatch(BaseModel):
    enabled: bool | None = None
    threshold: float | None = None
    window_s: int | None = None
    severity: str | None = None
    cooldown_s: int | None = None
    channels: list[str] | None = None


class AckBody(BaseModel):
    note: str | None = None


def _rule_dict(r: AlertRule) -> dict:
    return {
        "id": r.id, "name": r.name, "metric": r.metric, "comparator": r.comparator,
        "threshold": r.threshold, "window_s": r.window_s, "severity": r.severity,
        "cooldown_s": r.cooldown_s, "target_kind": r.target_kind, "enabled": bool(r.enabled),
        "channels": list(r.channels or []),
    }


def _incident_dict(i: AlertIncident) -> dict:
    return {
        "id": i.id, "rule_id": i.rule_id, "rule_name": i.rule_name, "metric": i.metric,
        "target": i.target, "severity": i.severity, "state": i.state,
        "started_at": i.started_at.isoformat() if i.started_at else None,
        "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
        "ack_by": i.ack_by, "ack_at": i.ack_at.isoformat() if i.ack_at else None,
        "summary": i.summary, "detail": i.detail or {}, "value": i.value,
    }


@router.get("/rules")
async def list_rules(session: AsyncSession = Depends(get_session),
                     user=Depends(require_admin)) -> dict:
    from ..alerts.rules import ensure_default_rules

    await ensure_default_rules(session)
    rows = (await session.execute(select(AlertRule).order_by(AlertRule.id))).scalars().all()
    return {"rules": [_rule_dict(r) for r in rows]}


@router.patch("/rules/{rule_id}")
async def patch_rule(rule_id: str, body: RulePatch,
                     session: AsyncSession = Depends(get_session),
                     user=Depends(require_admin)) -> dict:
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Unknown rule")
    data = body.model_dump(exclude_unset=True, exclude_none=True)
    if "severity" in data and data["severity"] not in ("info", "warning", "critical"):
        raise HTTPException(status_code=422, detail="Invalid severity")
    if "window_s" in data and (data["window_s"] < 0 or data["window_s"] > 86400):
        raise HTTPException(status_code=422, detail="Invalid window_s")
    for k, v in data.items():
        setattr(rule, k, v)
    rule.updated_at = _utcnow()
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return _rule_dict(rule)


@router.get("/incidents")
async def list_incidents(
    state: str | None = Query(default=None, pattern="^(firing|resolved)$"),
    severity: str | None = Query(default=None, pattern="^(info|warning|critical)$"),
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    user=Depends(require_admin),
) -> dict:
    stmt = select(AlertIncident)
    if state:
        stmt = stmt.where(AlertIncident.state == state)
    if severity:
        stmt = stmt.where(AlertIncident.severity == severity)
    stmt = stmt.order_by(AlertIncident.started_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return {"incidents": [_incident_dict(i) for i in rows]}


@router.post("/incidents/{incident_id}/ack")
async def ack_incident(incident_id: int, body: AckBody | None = None,
                       session: AsyncSession = Depends(get_session),
                       user=Depends(require_admin)) -> dict:
    inc = await session.get(AlertIncident, incident_id)
    if inc is None:
        raise HTTPException(status_code=404, detail="Unknown incident")
    inc.ack_by = getattr(user, "email", None) or str(getattr(user, "id", "admin"))
    inc.ack_at = _utcnow()
    if body and body.note:
        inc.ack_note = body.note[:2000]
    session.add(inc)
    await session.commit()
    return {"ok": True}


@router.get("/summary")
async def summary(session: AsyncSession = Depends(get_session),
                  user=Depends(require_admin)) -> dict:
    firing = (await session.execute(
        select(func.count()).select_from(AlertIncident).where(AlertIncident.state == "firing")
    )).scalar() or 0
    since = _utcnow() - timedelta(hours=24)
    resolved = (await session.execute(
        select(func.count()).select_from(AlertIncident)
        .where(AlertIncident.state == "resolved", AlertIncident.resolved_at >= since)
    )).scalar() or 0
    rules_enabled = (await session.execute(
        select(func.count()).select_from(AlertRule).where(AlertRule.enabled == True)  # noqa: E712
    )).scalar() or 0
    return {"firing": int(firing), "resolved_24h": int(resolved),
            "rules_enabled": int(rules_enabled)}
