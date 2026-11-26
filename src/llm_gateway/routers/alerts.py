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
    name: str | None = None
    comparator: str | None = None
    threshold: float | None = None
    window_s: int | None = None
    severity: str | None = None
    cooldown_s: int | None = None
    channels: list[str] | None = None
    target_filter: list[str] | None = None
    description: str | None = None


class RuleCreate(BaseModel):
    name: str
    metric: str
    comparator: str = ">="
    threshold: float
    window_s: int = 60
    severity: str = "warning"
    cooldown_s: int = 300
    channels: list[str] | None = None
    target_filter: list[str] | None = None
    description: str | None = None


class TestRuleBody(BaseModel):
    send: bool = False


class TestChannelBody(BaseModel):
    channel: str = "webhook"


class AckBody(BaseModel):
    note: str | None = None


_VALID_CHANNEL_RE_MARKS = ("webhook", "email")


def _validate_rule_fields(*, metric: str | None = None, comparator: str | None = None,
                          severity: str | None = None, window_s: int | None = None,
                          cooldown_s: int | None = None, name: str | None = None,
                          channels: list[str] | None = None) -> None:
    from ..alerts.rules import COMPARATORS, METRIC_REGISTRY, SEVERITIES
    from ..webhooks import is_webhook_url

    if metric is not None and metric not in METRIC_REGISTRY:
        raise HTTPException(status_code=422,
                            detail=f"Unknown metric '{metric}'. Known metrics: {', '.join(sorted(METRIC_REGISTRY))}")
    if comparator is not None and comparator not in COMPARATORS:
        raise HTTPException(status_code=422, detail=f"Invalid comparator (use one of {', '.join(COMPARATORS)})")
    if severity is not None and severity not in SEVERITIES:
        raise HTTPException(status_code=422, detail=f"Invalid severity (use one of {', '.join(SEVERITIES)})")
    if window_s is not None and (window_s < 0 or window_s > 86400):
        raise HTTPException(status_code=422, detail="window_s must be between 0 and 86400")
    if cooldown_s is not None and (cooldown_s < 0 or cooldown_s > 604800):
        raise HTTPException(status_code=422, detail="cooldown_s must be between 0 and one week")
    if name is not None and not name.strip():
        raise HTTPException(status_code=422, detail="name is required")
    if channels is not None:
        for ch in channels:
            ch = str(ch).strip()
            if not ch or ch in _VALID_CHANNEL_RE_MARKS or is_webhook_url(ch):
                continue
            raise HTTPException(status_code=422,
                                detail=f"Invalid channel '{ch}' (use webhook, email, or an https:// URL)")


def _rule_dict(r: AlertRule, custom: bool | None = None) -> dict:
    from ..alerts.rules import is_default_rule

    return {
        "id": r.id, "name": r.name, "metric": r.metric, "comparator": r.comparator,
        "threshold": r.threshold, "window_s": r.window_s, "severity": r.severity,
        "cooldown_s": r.cooldown_s, "target_kind": r.target_kind, "enabled": bool(r.enabled),
        "channels": list(r.channels or []),
        "target_filter": [str(x) for x in (getattr(r, "target_filter", None) or [])],
        "description": getattr(r, "description", None) or "",
        "custom": (not is_default_rule(r.id)) if custom is None else custom,
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
    _validate_rule_fields(
        comparator=data.get("comparator"), severity=data.get("severity"),
        window_s=data.get("window_s"), cooldown_s=data.get("cooldown_s"),
        name=data.get("name"), channels=data.get("channels"),
    )
    if "channels" in data:
        data["channels"] = [str(c).strip() for c in data["channels"] if str(c).strip()][:8]
    if "target_filter" in data:
        from ..alerts.rules import normalize_filters

        data["target_filter"] = normalize_filters(data["target_filter"])
    if "description" in data:
        data["description"] = str(data["description"])[:500]
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


@router.get("/metrics")
async def metrics_catalog(user=Depends(require_admin)) -> dict:
    from ..alerts.rules import COMPARATORS, METRIC_REGISTRY, SEVERITIES

    metrics = [{"metric": m, "target_kind": kind, "unit": unit, "description": desc}
               for m, (kind, unit, desc) in sorted(METRIC_REGISTRY.items())]
    return {"metrics": metrics, "comparators": list(COMPARATORS),
            "severities": list(SEVERITIES),
            "channels_hint": ["webhook", "email", "https://hook.example.com/..."]}


@router.post("/rules", status_code=201)
async def create_rule(body: RuleCreate, session: AsyncSession = Depends(get_session),
                      user=Depends(require_admin)) -> dict:
    from ..alerts.rules import (METRIC_REGISTRY, is_default_rule, make_custom_rule_id,
                                normalize_filters)

    _validate_rule_fields(metric=body.metric, comparator=body.comparator,
                          severity=body.severity, window_s=body.window_s,
                          cooldown_s=body.cooldown_s, name=body.name,
                          channels=body.channels)
    if is_default_rule(make_custom_rule_id(body.name)):
        raise HTTPException(status_code=409, detail="Rule name collides with a built-in rule")
    rule_id = make_custom_rule_id(body.name)
    target_kind = METRIC_REGISTRY[body.metric][0]
    rule = AlertRule(
        id=rule_id, name=body.name.strip()[:128], metric=body.metric,
        comparator=body.comparator, threshold=float(body.threshold),
        window_s=int(body.window_s), severity=body.severity, cooldown_s=int(body.cooldown_s),
        target_kind=target_kind,
        enabled=True,
        channels=[str(c).strip() for c in (body.channels or ["webhook"]) if str(c).strip()][:8] or ["webhook"],
        target_filter=normalize_filters(body.target_filter),
        description=(body.description or "")[:500] or None,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return _rule_dict(rule, custom=True)


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str, session: AsyncSession = Depends(get_session),
                      user=Depends(require_admin)) -> dict:
    from sqlalchemy import update as sa_update

    from ..alerts.rules import is_default_rule
    from ..models import AlertIncident as Inc

    if is_default_rule(rule_id):
        raise HTTPException(status_code=405,
                            detail="Built-in rules cannot be deleted; disable or edit them instead")
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Unknown rule")
    closed = (await session.execute(
        sa_update(Inc).where(Inc.rule_id == rule_id, Inc.state == "firing")
        .values(state="resolved", ack_note="rule deleted")
    )).rowcount
    await session.delete(rule)
    await session.commit()
    return {"ok": True, "resolved_incidents": int(closed or 0)}


@router.post("/rules/{rule_id}/test")
async def test_rule(rule_id: str, body: TestRuleBody | None = None,
                    session: AsyncSession = Depends(get_session),
                    user=Depends(require_admin)) -> dict:
    """Dry-run a rule against current live telemetry; optionally fire a test notification."""
    return await _test_rule_impl(rule_id, bool(body and body.send), session)


async def _test_rule_impl(rule_id: str, send: bool, session) -> dict:
    import time as _t

    from ..alerts.engine import evaluate
    from ..alerts.incidents import live_series
    from ..alerts.rules import _from_row, target_matches

    row = await session.get(AlertRule, rule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown rule")
    rule = _from_row(row)
    series = live_series()
    now = _t.time()
    targets = [t for t in series.targets(rule.metric)
               if not rule.target_filter or target_matches(rule.target_filter, t)]
    findings = [f for f in evaluate([rule], series, now, None) if not f.resolve]
    would_fire = sorted({f.target for f in findings})
    sent_note = ""
    if send:
        from ..alerts.notify import notify_test_rule

        ok = await notify_test_rule(rule, would_fire or targets[:1])
        sent_note = "test notification sent" if ok else "notification failed (check webhook/email config)"
    message = (f"{len(would_fire)} target(s) currently violating"
               if would_fire else "no target currently violates this rule")
    return {"ok": True, "would_fire": would_fire, "fired_now": bool(would_fire),
            "live_targets": targets[:50], "message": message + (f"; {sent_note}" if sent_note else "")}


@router.post("/test-alert")
async def test_alert(body: TestChannelBody | None = None,
                     user=Depends(require_admin)) -> dict:
    channel = (body.channel if body else "webhook") or "webhook"
    if not (channel in ("webhook", "email") or channel.startswith(("http://", "https://"))):
        raise HTTPException(status_code=422, detail="channel must be webhook, email, or a URL")
    sent = await _send_test(channel)
    return {"ok": bool(sent),
            "message": "test alert dispatched" if sent else "delivery failed; check channel configuration"}


async def _send_test(channel: str) -> bool:
    from ..webhooks import is_webhook_url, send_webhook, send_webhook_url

    payload = {"event": "alert_test", "severity": "info", "rule": "Test alert",
               "summary": "DEZISS test alert: notification channel check",
               "target": "admin-test", "value": None, "incident_id": None}
    try:
        if channel == "email":
            from ..services.email_service import get_email_service

            await get_email_service().send_admin_alert(
                "[INFO] DEZISS test alert", payload["summary"])
            return True
        if channel == "webhook":
            await send_webhook("alert_test", payload)
            return True
        if is_webhook_url(channel):
            await send_webhook_url(channel, "alert_test", payload)
            return True
    except Exception:
        return False
    return False
