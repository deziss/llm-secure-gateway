import asyncio
import logging
from typing import Optional
from .client import get_licencia_client

logger = logging.getLogger(__name__)

async def start_licensing() -> None:
    """Validate license on gateway startup."""
    client = get_licencia_client()
    try:
        state = await client.validate()
        logger.info(
            "Licencia initialized (tier: %s, valid: %s, community: %s)",
            state.tier.value,
            state.is_valid,
            state.is_community,
        )
    except Exception as exc:
        logger.warning("Licencia initial validation error: %s", exc)

async def stop_licensing() -> None:
    """Deactivate license seat on gateway shutdown."""
    client = get_licencia_client()
    try:
        await client.deactivate()
        logger.info("Licencia seat released on shutdown")
    except Exception as exc:
        logger.debug("Licencia shutdown deactivation error: %s", exc)

async def licencia_checkin_loop(interval_hours: Optional[int] = 6) -> None:
    """Periodic background task refreshing lease every check_in_interval hours."""
    client = get_licencia_client()
    seconds = max(60, int(interval_hours or 6) * 3600)
    logger.info("Starting Licencia check-in loop (interval: %dh)", interval_hours or 6)
    while True:
        try:
            await asyncio.sleep(seconds)
            logger.info("Executing periodic Licencia check-in...")
            await client.validate()
        except asyncio.CancelledError:
            logger.info("Licencia check-in loop cancelled")
            break
        except Exception as exc:
            logger.warning("Periodic Licencia check-in failed: %s", exc)
