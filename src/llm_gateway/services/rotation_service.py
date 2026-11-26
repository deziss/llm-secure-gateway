"""API key rotation automation (governance).

Two complementary mechanisms:

1. On-demand rotation (zero downtime): ``AuthService.rotate_key`` mints a
   replacement key that inherits quotas/owner and keeps the old key valid for
   a configurable grace window. Exposed as POST /keys/{prefix}/rotate.

2. Scheduled sweep (this module): keys created with ``rotate_interval_days``
   have their ``expires_at`` set to the interval, so the existing expiry and
   expiring-soon webhooks already fire. The sweep additionally emits a
   ``key_rotation_due`` webhook when an active, not-yet-rotated key reaches
   its interval, telling the owner to rotate before expiry. The gateway only
   stores hashes, so it never mints a replacement key behind the owner's back
   — the raw secret can only be delivered once, to a caller who proves
   ownership via the authenticated API.

Started from the app lifespan like ``retention_loop``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..models import APIKey
from ..webhooks import notify_key_rotation_due

logger = logging.getLogger(__name__)

_SWEEP_INTERVAL_SECONDS = 21600  # 6 hours


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def run_rotation_sweep(session) -> int:
    """Notify owners of keys that reached their auto-rotation interval.

    A key is rotation-due when it is active, has ``rotate_interval_days`` set,
    has not already been rotated, and its age is at least one interval. Owners
    are reminded at most once per day (``rotation_notified_at`` stamp), so a
    key left unrotated keeps surfacing in webhook alerts until a key_expiring
    / key_expired event replaces it.
    """
    now = _utcnow_naive()
    result = await session.execute(
        select(APIKey).where(APIKey.is_active == True)  # noqa: E712
        .where(APIKey.rotate_interval_days.isnot(None))
        .where(APIKey.rotated_at.is_(None))
    )
    due = 0
    for key in result.scalars().all():
        try:
            interval = int(key.rotate_interval_days or 0)
            if interval <= 0:
                continue
            if (now - key.created_at) < timedelta(days=interval):
                continue
            if key.rotation_notified_at and (now - key.rotation_notified_at) < timedelta(days=1):
                continue
            await notify_key_rotation_due(key.prefix, key.owner_id, interval)
            key.rotation_notified_at = now
            due += 1
        except Exception:
            logger.exception("rotation sweep: failed for key %s", key.prefix[:12])
    if due:
        try:
            await session.commit()
        except Exception:  # pragma: no cover - defensive
            logger.exception("rotation sweep: failed to persist notification stamps")
        logger.info("rotation sweep: %s key(s) flagged rotation-due", due)
    return due


async def rotation_loop() -> None:
    """Periodic API key rotation-due sweep (started from the app lifespan)."""
    logger.info("key rotation sweep started (interval=%ss)", _SWEEP_INTERVAL_SECONDS)
    # Small startup delay so migrations/connection pools settle first.
    await asyncio.sleep(30)
    while True:
        try:
            from ..database import get_session_context

            async with get_session_context() as session:
                await run_rotation_sweep(session)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("rotation: sweep run failed")
        await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
