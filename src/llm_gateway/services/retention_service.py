"""Retention cleanup service (governance).

Deletes rows older than the configured windows:

* RETENTION_DAYS_AUDIT         -- AuditLog rows (0 = keep forever, the default).
* RETENTION_DAYS_TRACES        -- AgentRun / AgentStep rows (default 90 days).
* RETENTION_DAYS_SPEND_METRICS -- SpendRecord / ModelTelemetryRollup rows
  (0 = keep forever, the default).

Deletes happen in batches of 1000 ids at a time (SELECT ids ... LIMIT 1000 ->
DELETE ... WHERE id IN (...)), which keeps transactions small and works on
SQLite as well as Postgres (SQLite has no DELETE ... LIMIT).

Config values are read from the config module at call time so tests can
monkeypatch them. Timestamps in these tables are naive UTC.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config
from ..models import AgentRun, AgentStep, AuditLog, ModelTelemetryRollup, SpendRecord

logger = logging.getLogger(__name__)

BATCH_SIZE = 1000


def _utcnow_naive() -> datetime:
    """Naive UTC now, matching the _utcnow convention used by the models."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _days(name: str) -> int:
    try:
        return max(0, int(getattr(config, name, 0) or 0))
    except (TypeError, ValueError):
        return 0


async def _delete_older_than(
    session: AsyncSession,
    model: Any,
    ts_col: Any,
    cutoff: datetime,
    batch_size: int = BATCH_SIZE,
) -> int:
    """Delete rows with ts_col < cutoff in id batches. Returns rows deleted."""
    pk = list(model.__table__.primary_key.columns)[0]
    deleted = 0
    while True:
        result = await session.execute(select(pk).where(ts_col < cutoff).limit(batch_size))
        ids = [row[0] for row in result.all()]
        if not ids:
            break
        result = await session.execute(delete(model).where(pk.in_(ids)))
        deleted += int(result.rowcount or 0)
        await session.commit()
        if len(ids) < batch_size:
            break
    return deleted


def _table_targets() -> list[tuple[str, Any, Any, int]]:
    """(name, model, timestamp column, retention days) for configured tables.

    Tables whose window is 0 (keep forever) are omitted."""
    targets: list[tuple[str, Any, Any, int]] = []
    audit_days = _days("RETENTION_DAYS_AUDIT")
    if audit_days > 0:
        targets.append(("audit_log", AuditLog, AuditLog.timestamp, audit_days))
    trace_days = _days("RETENTION_DAYS_TRACES")
    if trace_days > 0:
        targets.append(("agent_run", AgentRun, AgentRun.last_ts, trace_days))
        targets.append(("agent_step", AgentStep, AgentStep.ts, trace_days))
    spend_days = _days("RETENTION_DAYS_SPEND_METRICS")
    if spend_days > 0:
        targets.append(("spend_record", SpendRecord, SpendRecord.created_at, spend_days))
        targets.append(("model_telemetry_rollup", ModelTelemetryRollup,
                        ModelTelemetryRollup.bucket_time, spend_days))
    return targets


async def run_retention_cleanup(
    session_factory: Optional[Callable[[], AsyncSession]] = None,
    *,
    session: Optional[AsyncSession] = None,
) -> dict[str, int]:
    """Delete rows older than the configured retention windows.

    Pass ``session`` to run inside an existing session (tests, the admin
    endpoint), or ``session_factory`` (e.g. ``get_session_context``) to open a
    short-lived session per run. Returns a mapping of table name -> rows
    deleted, and emits a ``retention_cleanup`` audit event with the counts.
    """
    now = _utcnow_naive()
    counts: dict[str, int] = {}
    try:
        if session is not None:
            counts = await _cleanup_with_session(session, now)
        else:
            factory = session_factory
            if factory is None:
                from ..database import get_session_context

                factory = get_session_context
            async with factory() as sess:
                counts = await _cleanup_with_session(sess, now)
    finally:
        _emit_audit(counts)
    return counts


async def _cleanup_with_session(session: AsyncSession, now: datetime) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, model, ts_col, days in _table_targets():
        cutoff = now - timedelta(days=days)
        try:
            counts[name] = await _delete_older_than(session, model, ts_col, cutoff)
        except Exception:  # one bad table shouldn't stop the others
            logger.exception("retention: cleanup failed for %s", name)
            counts[name] = 0
    return counts


def _emit_audit(counts: dict[str, int]) -> None:
    """Log + governance audit event. Must never raise."""
    total = sum(counts.values())
    logger.info("retention cleanup: %d rows deleted (%s)", total, counts)
    if total == 0:
        return
    try:
        from ..audit import get_audit_logger

        get_audit_logger().log(
            "retention_cleanup",
            "system",
            "retention_cleanup",
            "allow",
            metadata={"deleted": counts, "total": total},
        )
    except Exception:  # pragma: no cover - audit must never break cleanup
        logger.debug("retention: audit emit failed", exc_info=True)


async def retention_loop() -> None:
    """Periodic retention cleanup task (started from the app lifespan)."""
    interval = max(3600, int(getattr(config, "RETENTION_INTERVAL_SECONDS", 21600)))
    logger.info("retention loop started (interval=%ss, audit=%sd, traces=%sd, spend=%sd)",
                interval, _days("RETENTION_DAYS_AUDIT"), _days("RETENTION_DAYS_TRACES"),
                _days("RETENTION_DAYS_SPEND_METRICS"))
    while True:
        try:
            await run_retention_cleanup()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("retention: cleanup run failed")
        await asyncio.sleep(interval)
