"""Webhook notification service.

Fires HTTP POST to configured WEBHOOK_URL on events like:
- backend_down: a backend's circuit breaker tripped open
- key_expiring: an API key expires within 7 days
- key_expired: an API key has expired

Configurable via:
  - Env var: WEBHOOK_URL (empty = disabled)
  - SystemSetting: WEBHOOK_URL (overrides env)

Webhook payload is JSON with HMAC-SHA256 signature in X-Webhook-Signature
header (signed with AUTH_SECRET) for replay-attack prevention.
"""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_WEBHOOK_CLIENT: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _WEBHOOK_CLIENT
    if _WEBHOOK_CLIENT is None or _WEBHOOK_CLIENT.is_closed:
        _WEBHOOK_CLIENT = httpx.AsyncClient(timeout=10.0)
    return _WEBHOOK_CLIENT


def _sign_payload(payload: str) -> str:
    """HMAC-SHA256 signature using AUTH_SECRET."""
    secret = os.getenv("AUTH_SECRET", "").encode()
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


async def _get_webhook_url() -> str:
    """Read WEBHOOK_URL from SystemSetting (cached 30s) with env fallback."""
    try:
        from .database import _async_session
        from .services import get_config_service
        async with _async_session() as session:
            config = get_config_service()
            return await config.get_setting(
                session, "WEBHOOK_URL",
                os.getenv("WEBHOOK_URL", ""),
            )
    except Exception:
        return os.getenv("WEBHOOK_URL", "")


async def send_webhook(event: str, data: dict) -> None:
    """Fire a webhook notification. Never raises — logs warnings on failure."""
    url = await _get_webhook_url()
    if not url:
        return  # Webhooks not configured

    payload = json.dumps({
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    })

    signature = _sign_payload(payload)

    try:
        client = _get_client()
        r = await client.post(
            url,
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": f"sha256={signature}",
                "X-Webhook-Event": event,
            },
        )
        if r.status_code >= 400:
            logger.warning("Webhook %s returned %d for event %s", url, r.status_code, event)
    except Exception as exc:
        logger.warning("Webhook delivery failed for %s: %s", event, exc)


async def notify_backend_down(backend_name: str, url: str) -> None:
    """Notify that a backend's circuit breaker tripped open."""
    await send_webhook("backend_down", {
        "backend": backend_name,
        "url": url,
        "message": f"Circuit breaker OPEN for backend '{backend_name}' at {url}",
    })


async def notify_key_expiring(prefix: str, owner_id: str, days_left: int) -> None:
    """Notify that an API key is expiring soon."""
    await send_webhook("key_expiring", {
        "key_prefix": prefix,
        "owner": owner_id,
        "days_left": days_left,
        "message": f"API key {prefix} for owner '{owner_id}' expires in {days_left} days",
    })


async def notify_key_expired(prefix: str, owner_id: str) -> None:
    """Notify that an API key has expired."""
    await send_webhook("key_expired", {
        "key_prefix": prefix,
        "owner": owner_id,
        "message": f"API key {prefix} for owner '{owner_id}' has expired",
    })
