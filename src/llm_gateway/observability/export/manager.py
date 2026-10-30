"""Runtime-managed observability integrations (configured from the admin UI).

Exporters in the SDK cannot be added to a running MeterProvider, so everything managed here
goes through two long-lived, always-attached pieces:

  * ``ManagedSpanProcessor``: one SpanProcessor on the global provider (and on tenant
    providers, via ``sinks.shared_span_processors``) that fans spans out to one
    ``BatchSpanProcessor`` per integration. Each has its own queue and worker thread, so a
    slow or dead backend delays nobody else.
  * ``DynamicMetricExporter``: one periodic reader whose exporter delegates to the current
    set of metric destinations.

``apply()`` reconciles those with the desired set of integrations: it builds new ones, rebuilds
changed ones (fingerprint of endpoint + headers) and tears down removed or disabled ones.
Credentials live Fernet-encrypted in the database (``ObsIntegration``); nothing here logs them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Mapping, Optional

from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult
from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from . import sinks
from .presets import META, PresetError, ResolvedPreset, resolve

logger = logging.getLogger(__name__)


class ManagedSpanProcessor(SpanProcessor):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._procs: dict[str, BatchSpanProcessor] = {}

    def set(self, name: str, proc: Optional[BatchSpanProcessor]) -> None:
        with self._lock:
            old = self._procs.pop(name, None)
            if proc is not None:
                self._procs[name] = proc
        if old is not None:
            try:
                old.shutdown()
            except Exception:
                logger.debug("shutdown of old span processor failed", exc_info=True)

    def names(self) -> list[str]:
        with self._lock:
            return list(self._procs)

    def _snapshot(self) -> list[BatchSpanProcessor]:
        with self._lock:
            return list(self._procs.values())

    def on_start(self, span, parent_context=None) -> None:
        for p in self._snapshot():
            p.on_start(span, parent_context=parent_context)

    def on_end(self, span) -> None:
        for p in self._snapshot():
            try:
                p.on_end(span)
            except Exception:  # an integration must never break the request path
                logger.debug("span processor on_end failed", exc_info=True)

    def shutdown(self) -> None:
        for p in self._snapshot():
            try:
                p.shutdown()
            except Exception:
                logger.debug("span processor shutdown failed", exc_info=True)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return all(p.force_flush(timeout_millis) for p in self._snapshot())


class DynamicMetricExporter(MetricExporter):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.RLock()
        self._targets: dict[str, MetricExporter] = {}

    def set(self, name: str, exporter: Optional[MetricExporter]) -> None:
        with self._lock:
            old = self._targets.pop(name, None)
            if exporter is not None:
                self._targets[name] = exporter
        if old is not None:
            try:
                old.shutdown()
            except Exception:
                logger.debug("shutdown of old metric exporter failed", exc_info=True)

    def export(self, metrics_data, timeout_millis: float = 10_000, **kwargs) -> MetricExportResult:
        with self._lock:
            targets = list(self._targets.values())
        ok = not targets
        for t in targets:  # sequential: metrics go out once per interval, so a slow target is tolerable
            try:
                if t.export(metrics_data, timeout_millis=timeout_millis, **kwargs) == MetricExportResult.SUCCESS:
                    ok = True
            except Exception:
                logger.debug("metric export failed", exc_info=True)
        return MetricExportResult.SUCCESS if ok else MetricExportResult.FAILURE

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True

    def shutdown(self, timeout_millis: float = 30_000, **kwargs) -> None:
        with self._lock:
            targets, self._targets = list(self._targets.values()), {}
        for t in targets:
            try:
                t.shutdown()
            except Exception:
                pass


_span_processor = ManagedSpanProcessor()
_shared_span_processor = sinks._SharedSpanProcessor(_span_processor)
_metric_exporter = DynamicMetricExporter()


def managed_span_processor() -> SpanProcessor:
    """The one processor to attach to every tracer provider (shutdown reaches it once)."""
    return _shared_span_processor


def managed_metric_exporter() -> DynamicMetricExporter:
    return _metric_exporter


@dataclass
class _Active:
    fingerprint: str
    signals: list
    status: sinks.SinkStatus
    mode: str


_LOCK = threading.RLock()
_ACTIVE: dict[str, _Active] = {}
_ERRORS: dict[str, str] = {}


def _fingerprint(preset: ResolvedPreset) -> str:
    body = json.dumps(
        {"t": [preset.traces.endpoint, sorted(preset.traces.headers.items())] if preset.traces else None,
         "m": [preset.metrics.endpoint, sorted(preset.metrics.headers.items())] if preset.metrics else None},
        sort_keys=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _teardown(name: str) -> None:
    _span_processor.set(name, None)
    _metric_exporter.set(name, None)
    _ACTIVE.pop(name, None)


def _build(name: str, preset: ResolvedPreset, fp: str) -> None:
    status = sinks.SinkStatus(name=name, signals=preset.signals(),
                              endpoint=(preset.traces or preset.metrics).endpoint)
    if preset.traces is not None:
        _span_processor.set(name, BatchSpanProcessor(
            sinks._TrackedSpanExporter(sinks._span_exporter(preset.traces), status)))
    if preset.metrics is not None:
        _metric_exporter.set(name, sinks._TrackedMetricExporter(sinks._metric_exporter(preset.metrics), status))
    _ACTIVE[name] = _Active(fingerprint=fp, signals=preset.signals(), status=status, mode=preset.mode)


def apply(desired: Mapping[str, Optional[Mapping[str, str]]]) -> None:
    """Reconcile running integrations with ``desired``: name -> env-style mapping, or None
    when the integration is disabled. Idempotent; unchanged integrations keep their counters."""
    with _LOCK:
        _ERRORS.clear()
        wanted: dict[str, tuple[ResolvedPreset, str]] = {}
        for name, env in desired.items():
            if env is None:
                continue
            try:
                preset = resolve(name, env)
            except PresetError as exc:
                _ERRORS[name] = sinks.redact_url(str(exc))
                continue
            if not preset.signals():
                continue
            wanted[name] = (preset, _fingerprint(preset))
        for name in list(_ACTIVE):
            if name not in wanted or _ACTIVE[name].fingerprint != wanted[name][1]:
                _teardown(name)
        for name, (preset, fp) in wanted.items():
            if name not in _ACTIVE:
                try:
                    _build(name, preset, fp)
                    logger.info("Integration '%s' active (%s)", name, ", ".join(preset.signals()))
                except Exception as exc:  # a bad exporter config must not stop the rest
                    _teardown(name)
                    _ERRORS[name] = sinks.redact_url(f"{type(exc).__name__}: {exc}")
                    logger.warning("Integration '%s' failed to start: %s", name, sinks.redact_url(str(exc)))


def runtime_status() -> dict[str, dict]:
    """name -> {active, mode, status dict (counters, last ok/error), error}."""
    with _LOCK:
        out = {n: {"active": True, "mode": a.mode, "status": a.status.as_dict(), "error": None}
               for n, a in _ACTIVE.items()}
        for n, err in _ERRORS.items():
            out.setdefault(n, {"active": False, "mode": None, "status": None, "error": err})
    return out


# ---------------------------------------------------------------- persistence
def _decrypt_secrets(token: Optional[str]) -> dict[str, str]:
    if not token:
        return {}
    from ...services.owner_service import owner_service
    try:
        data = json.loads(owner_service.decrypt(token))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        logger.warning("could not decrypt integration secrets (ENCRYPTION_KEY changed?)")
        return {}


def _encrypt_secrets(secrets: Mapping[str, str]) -> Optional[str]:
    if not secrets:
        return None
    from ...services.owner_service import owner_service
    return owner_service.encrypt(json.dumps(dict(secrets), sort_keys=True))


def row_env(row) -> dict[str, str]:
    """Merge a stored row's plain config and decrypted secrets into one env-style mapping."""
    env = {k: str(v) for k, v in (row.config or {}).items() if v not in (None, "")}
    env.update(_decrypt_secrets(row.secrets_enc))
    return env


def allowed_keys(name: str) -> dict[str, dict]:
    return {f["key"]: f for f in META.get(name, {}).get("fields", [])}


async def load_and_apply() -> None:
    """Read every stored integration and apply it. Closes its DB session before applying."""
    from sqlmodel import select
    from ...database import get_session_context
    from ...models import ObsIntegration

    async with get_session_context() as session:
        rows = (await session.execute(select(ObsIntegration))).scalars().all()
        desired = {r.name: (row_env(r) if r.enabled else None) for r in rows}
    apply(desired)


async def integrations_loop(interval: float = 15.0) -> None:
    """Pick up changes made through another replica: re-apply when the stored set changes."""
    import asyncio
    from sqlalchemy import func
    from sqlmodel import select
    from ...database import get_session_context
    from ...models import ObsIntegration

    last = None
    while True:
        try:
            async with get_session_context() as session:
                stamp = (await session.execute(
                    select(func.count(ObsIntegration.name), func.max(ObsIntegration.updated_at)))).one()
            stamp = (stamp[0], str(stamp[1]))
            if stamp != last:
                await load_and_apply()
                last = stamp
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("integrations reload failed; keeping current set", exc_info=True)
        await asyncio.sleep(interval)


def _reset_for_tests() -> None:
    with _LOCK:
        for n in list(_ACTIVE):
            _teardown(n)
        _ERRORS.clear()
