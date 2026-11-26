"""Alert notification delivery.

Channels: "webhook" (HMAC-signed, reuses webhooks.send_webhook), "email"
(services.email_service admin alert). Unknown channel names are ignored with
a debug log so operators can pre-configure future channels (slack, discord,
telegram, pagerduty) without breaking delivery of the rest. Per-rule
cooldown suppression lives here (query the last notification for the same
rule/target episode history).
"""
from __future__ import annotations

import logging

from sqlalchemy import desc
from sqlmodel import select

from ..database import get_session_context
from ..models import AlertIncident, _utcnow

logger = logging.getLogger("deziss.alerts.notify")


async def _rule_channels(rule_id: str) -> tuple[list[str], int]:
    """(channels, cooldown_s) from the rule row; defaults when rule missing."""
    from ..models import AlertRule

    async with get_session_context() as session:
        rule = await session.get(AlertRule, rule_id)
    if rule is None:
        return ["webhook"], 300
    return list(rule.channels or ["webhook"]), int(rule.cooldown_s or 0)


async def _in_cooldown(inc: AlertIncident, cooldown_s: int) -> bool:
    if cooldown_s <= 0:
        return False
    async with get_session_context() as session:
        rows = (await session.execute(
            select(AlertIncident)
            .where(AlertIncident.rule_id == inc.rule_id, AlertIncident.target == inc.target,
                   AlertIncident.last_notified_at != None)  # noqa: E711
            .order_by(desc(AlertIncident.last_notified_at))
            .limit(1)
        )).scalars().all()
    if not rows:
        return False
    last = rows[0].last_notified_at
    if last is None:
        return False
    return (_utcnow() - last).total_seconds() < cooldown_s


def _payload(inc: AlertIncident, kind: str) -> dict:
    return {
        "event": f"alert_{kind}",
        "rule_id": inc.rule_id,
        "rule": inc.rule_name,
        "metric": inc.metric,
        "target": inc.target,
        "severity": inc.severity,
        "value": inc.value,
        "summary": inc.summary,
        "incident_id": inc.id,
        "started_at": inc.started_at.isoformat() if inc.started_at else None,
        "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
    }


async def _deliver(inc: AlertIncident, channels: list[str], kind: str) -> bool:
    from ..webhooks import send_webhook, send_webhook_url

    payload = _payload(inc, kind)
    text = payload["summary"] or f"{inc.rule_name} on {inc.target}"
    sent_any = False
    for ch in channels or ["webhook"]:
        try:
            if ch == "webhook":
                await send_webhook(payload["event"], payload)
                sent_any = True
            elif ch == "email":
                from ..services.email_service import get_email_service

                await get_email_service().send_admin_alert(
                    f"[{inc.severity.upper()}] {inc.rule_name}", text
                )
                sent_any = True
            elif ch.startswith(("http://", "https://")):
                await send_webhook_url(ch, payload["event"], payload)
                sent_any = True
            else:
                logger.debug("alerts: channel %r not implemented yet; skipped", ch)
        except Exception as e:
            logger.warning("alerts: delivery via %s failed for %s: %s", ch, inc.rule_id, e)
    return sent_any


async def notify_firing(inc: AlertIncident) -> bool:
    channels, cooldown = await _rule_channels(inc.rule_id)
    if await _in_cooldown(inc, cooldown):
        return False
    return await _deliver(inc, channels, "fired")


async def notify_recovery(inc: AlertIncident, finding) -> bool:
    channels, _cooldown = await _rule_channels(inc.rule_id)
    return await _deliver(inc, channels, "resolved")


async def notify_test_rule(rule, targets: list[str]) -> bool:
    """Send a test/fired-style notification for a rule without creating an incident."""
    from ..models import AlertIncident as _Inc

    fake = _Inc(
        rule_id=rule.id, rule_name=f"[TEST] {rule.name}", metric=rule.metric,
        target=", ".join(str(t) for t in targets[:3]) or "test", severity=rule.severity,
        state="firing", summary=f"[TEST] {rule.name}: would fire on {', '.join(str(t) for t in targets[:3]) or 'no live target'}",
        detail={"test": True}, value=None,
    )
    fake.started_at = _utcnow()
    fake.id = 0
    return await _deliver(fake, list(rule.channels or ["webhook"]), "fired")
