"""Audit logger with optional persistent DB trail.

Always writes structured JSON to stdout. When ENABLE_AUDIT_DB is true
(via env var or SystemSetting), also writes to the audit_log DB table
using a fire-and-forget async task — never blocks the request.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


class AuditLogger:
    def __init__(self) -> None:
        self._log = logging.getLogger("audit")
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._log.addHandler(handler)
        self._log.setLevel(logging.INFO)

    def log(
        self,
        event_type: str,
        user: Any,
        resource: str,
        decision: str,
        metadata: dict | None = None,
        ip_address: str | None = None,
    ) -> None:
        identity = "unknown"
        if hasattr(user, "owner_id"):
            identity = f"apikey:{user.owner_id}"
        elif isinstance(user, dict) and user.get("type") == "workload":
            identity = f"spiffe:{user.get('spiffe_id')}"

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "identity": identity,
            "resource": resource,
            "decision": decision,
            "metadata": metadata or {},
        }

        # Always write to stdout
        self._log.info(json.dumps(entry))

        # Fire-and-forget DB persistence (if enabled)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._persist_to_db(entry, ip_address))
        except RuntimeError:
            pass  # No event loop (e.g., during tests or sync context)

    async def _persist_to_db(self, entry: dict, ip_address: Optional[str]) -> None:
        """Write audit entry to DB if ENABLE_AUDIT_DB is true. Never raises."""
        try:
            # Lazy imports to avoid circular dependencies
            from .database import _async_session
            from .services import get_config_service
            from .models import AuditLog

            async with _async_session() as session:
                config = get_config_service()
                enabled = await config.get_setting(session, "ENABLE_AUDIT_DB",
                                                   os.getenv("ENABLE_AUDIT_DB", "false"))
                if str(enabled).lower() != "true":
                    return

                record = AuditLog(
                    timestamp=datetime.fromisoformat(entry["timestamp"]),
                    event_type=entry["event"],
                    identity=entry["identity"],
                    resource=entry["resource"],
                    decision=entry["decision"],
                    ip_address=ip_address,
                    metadata_json=entry.get("metadata", {}),
                )
                session.add(record)
                await session.commit()
        except Exception as exc:
            logger.warning("Audit DB write failed (non-fatal): %s", exc)


audit_logger = AuditLogger()


def get_audit_logger() -> AuditLogger:
    return audit_logger
