"""Build OTLP span processors and metric readers from ``OBS_EXPORTERS``, and track their health.

``OBS_EXPORTERS`` is a comma list of preset names (see presets.py). When it is unset:
  * ENABLE_TELEMETRY=true  -> ``phoenix,otlp`` (today's behaviour)
  * ENABLE_TELEMETRY unset -> nothing is created: no exporter, no connection attempts

Exporters are created only when telemetry is on. The Prometheus pull endpoint is separate
(export/prometheus.py) and is not a sink here.

Each trace sink gets one ``BatchSpanProcessor`` that is shared by the global provider and
every per-tenant Phoenix provider. The wrapper's ``shutdown`` runs once, so the processor is
not shut down (and its queue lost) when the first tenant provider goes away.
"""
from __future__ import annotations

import logging
import os
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult, PeriodicExportingMetricReader

from .presets import METRICS, TRACES, PresetError, ResolvedPreset, resolve

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on", "enabled"}
_URL_RE = re.compile(r"(\w+://)(?:[^/@\s]*@)?([^?#\s]*)(?:[?#]\S*)?")


def redact_url(text):
    """Drop userinfo and query string from every URL in ``text`` (they can carry credentials)."""
    if not text:
        return text
    return _URL_RE.sub(lambda m: m.group(1) + m.group(2), text)


PHOENIX = "phoenix"
OTLP = "otlp"


def _telemetry_on() -> bool:
    from ...telemetry.config import telemetry_enabled  # local import: avoids a cycle at import time
    return telemetry_enabled()


def configured_names(env=None) -> list[str]:
    env = os.environ if env is None else env
    raw = (env.get("OBS_EXPORTERS") or "").strip()
    if raw:
        return [n.strip().lower() for n in raw.split(",") if n.strip()]
    if (env.get("ENABLE_TELEMETRY") or "").strip().lower() in _TRUTHY:
        return [PHOENIX, OTLP]
    return []


@dataclass
class SinkStatus:
    name: str
    signals: list = field(default_factory=list)
    endpoint: str = ""
    last_ok: Optional[str] = None
    last_error: Optional[str] = None
    exported: int = 0
    failed: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def ok(self, n: int = 1) -> None:
        with self._lock:
            self.exported += n
            self.last_ok = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def fail(self, n: int, err: str) -> None:
        with self._lock:
            self.failed += n
            self.last_error = redact_url(err)[:500]

    def as_dict(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "signals": list(self.signals),
                "endpoint": redact_url(self.endpoint),
                "last_ok": self.last_ok,
                "last_error": redact_url(self.last_error),
                "exported": self.exported,
                "failed": self.failed,
            }


_STATUS: dict[str, SinkStatus] = {}
_SHARED_PROCESSORS: Optional[list] = None
_PHOENIX_PROCESSOR = None
_READERS: list = []
_PLAN_LOCK = threading.Lock()


def _status_for(name: str) -> SinkStatus:
    st = _STATUS.get(name)
    if st is None:
        st = SinkStatus(name=name)
        _STATUS[name] = st
    return st


class _TrackedSpanExporter(SpanExporter):
    def __init__(self, inner: SpanExporter, status: SinkStatus) -> None:
        self._inner = inner
        self._status = status

    def export(self, spans):
        try:
            result = self._inner.export(spans)
        except Exception as exc:
            self._status.fail(len(spans), f"{type(exc).__name__}: {exc}")
            return SpanExportResult.FAILURE
        if result == SpanExportResult.SUCCESS:
            self._status.ok(len(spans))
        else:
            self._status.fail(len(spans), "export returned FAILURE")
        return result

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


class _TrackedMetricExporter(MetricExporter):
    """Counts exports per batch (metrics are exported as batches, not per point)."""

    def __init__(self, inner: MetricExporter, status: SinkStatus) -> None:
        super().__init__(
            preferred_temporality=inner._preferred_temporality,
            preferred_aggregation=inner._preferred_aggregation,
        )
        self._inner = inner
        self._status = status

    def export(self, metrics_data, timeout_millis: float = 10_000, **kwargs):
        try:
            result = self._inner.export(metrics_data, timeout_millis=timeout_millis, **kwargs)
        except Exception as exc:
            self._status.fail(1, f"{type(exc).__name__}: {exc}")
            return MetricExportResult.FAILURE
        if result == MetricExportResult.SUCCESS:
            self._status.ok(1)
        else:
            self._status.fail(1, "export returned FAILURE")
        return result

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return self._inner.force_flush(timeout_millis)

    def shutdown(self, timeout_millis: float = 30_000, **kwargs) -> None:
        self._inner.shutdown(timeout_millis, **kwargs)


class _SharedSpanProcessor(SpanProcessor):
    """Forwards to one real processor; ``shutdown`` reaches it only once."""

    def __init__(self, inner: SpanProcessor) -> None:
        self._inner = inner
        self._shut = False
        self._lock = threading.Lock()

    def on_start(self, span, parent_context=None) -> None:
        self._inner.on_start(span, parent_context=parent_context)

    def on_end(self, span) -> None:
        self._inner.on_end(span)

    def shutdown(self) -> None:
        with self._lock:
            if self._shut:
                return
            self._shut = True
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


def _reachable(endpoint: str, timeout: float = 2.0) -> bool:
    """Startup probe kept from the original OTLP setup: skip a dead collector at boot."""
    parsed = urlparse(endpoint)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 4318)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def _span_exporter(target) -> SpanExporter:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    return OTLPSpanExporter(endpoint=target.endpoint, headers=dict(target.headers), timeout=10)


def _metric_exporter(target) -> MetricExporter:
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    return OTLPMetricExporter(endpoint=target.endpoint, headers=dict(target.headers), timeout=10)


def _metrics_interval_ms() -> int:
    try:
        return max(1000, int(float(os.getenv("OBS_METRICS_INTERVAL_S", "60")) * 1000))
    except ValueError:
        return 60_000


def init_sinks(env=None) -> dict:
    """Build every configured sink once. Idempotent.

    Returns ``{"phoenix_processor": proc|None, "phoenix_project": str|None,
    "shared_processors": [...], "readers": [...]}``. Creates no exporter when telemetry
    is switched off.
    """
    global _SHARED_PROCESSORS, _PHOENIX_PROCESSOR, _READERS
    env = os.environ if env is None else env
    with _PLAN_LOCK:
        if _SHARED_PROCESSORS is not None:
            return _plan()

        _SHARED_PROCESSORS = []
        _READERS = []
        if not _telemetry_on():
            return _plan()

        for name in configured_names(env):
            status = _status_for(name)
            try:
                preset: ResolvedPreset = resolve(name, env)
            except PresetError as exc:
                status.last_error = redact_url(str(exc))
                logger.warning("Exporter '%s' not configured: %s", name, redact_url(str(exc)))
                continue
            status.signals = preset.signals()
            status.endpoint = (preset.traces or preset.metrics).endpoint if (preset.traces or preset.metrics) else ""

            if preset.traces is not None:
                if name == PHOENIX:
                    _PHOENIX_PROCESSOR = _SharedSpanProcessor(
                        BatchSpanProcessor(_TrackedSpanExporter(_span_exporter(preset.traces), status))
                    )
                    _PHOENIX_PROCESSOR_PROJECT[0] = preset.resource.get("openinference.project.name")
                else:
                    _SHARED_PROCESSORS.append(_SharedSpanProcessor(
                        BatchSpanProcessor(_TrackedSpanExporter(_span_exporter(preset.traces), status))
                    ))

            if preset.metrics is not None:
                if name == OTLP and not _reachable(preset.metrics.endpoint):
                    status.last_error = "endpoint unreachable at startup; metrics export disabled"
                    logger.warning("OTLP metrics endpoint %s unreachable, disabling metrics export",
                                   redact_url(preset.metrics.endpoint))
                    continue
                reader = PeriodicExportingMetricReader(
                    _TrackedMetricExporter(_metric_exporter(preset.metrics), status),
                    export_interval_millis=_metrics_interval_ms(),
                )
                _READERS.append(reader)
            logger.info("Exporter '%s' enabled (%s)", name, ", ".join(status.signals))
        return _plan()


_PHOENIX_PROCESSOR_PROJECT: list = [None]


def _plan() -> dict:
    return {
        "phoenix_processor": _PHOENIX_PROCESSOR,
        "phoenix_project": _PHOENIX_PROCESSOR_PROJECT[0],
        "shared_processors": list(_SHARED_PROCESSORS or []),
        "readers": list(_READERS),
    }


def shared_span_processors() -> list:
    """Non-Phoenix span processors, for per-tenant Phoenix providers to attach."""
    return list(_SHARED_PROCESSORS or [])


def sinks_status() -> list[dict]:
    """Health of every configured sink, plus the Prometheus pull endpoint when enabled."""
    out = [st.as_dict() for st in list(_STATUS.values())]
    try:
        from .prometheus import prometheus_status
        pm = prometheus_status()
        if pm is not None:
            out.append(pm)
    except Exception:  # never let status reporting break the admin page
        logger.debug("prometheus status unavailable", exc_info=True)
    return out


def _reset_for_tests() -> None:
    global _SHARED_PROCESSORS, _PHOENIX_PROCESSOR, _READERS
    _SHARED_PROCESSORS = None
    _PHOENIX_PROCESSOR = None
    _READERS = []
    _STATUS.clear()
    _PHOENIX_PROCESSOR_PROJECT[0] = None
