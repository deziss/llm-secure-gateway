"""Wire OpenTelemetry providers for the FastAPI app.

Startup order:
  1. Resolve sinks from OBS_EXPORTERS (export/sinks.py). Nothing is created when
     ENABLE_TELEMETRY is off.
  2. Trace provider: a Phoenix processor (if configured) plus the shared processors.
  3. Metric readers: the Prometheus pull reader (OBS_PROMETHEUS, default on, independent of
     ENABLE_TELEMETRY) plus one periodic reader per metric sink.
  4. Instruments are created on the meter provider.
"""
import logging
import os

from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider

from .config import (
    ENABLE_TELEMETRY_ENV,
    neutralize_otlp_env,
    silence_otlp_exporter_logs,
    telemetry_enabled,
)
from .metrics import setup_metrics
from ..observability import VERSION, instance_id

logger = logging.getLogger(__name__)


def _interval_ms() -> int:
    try:
        return max(1000, int(float(os.getenv("OBS_METRICS_INTERVAL_S", "60")) * 1000))
    except ValueError:
        return 60_000


def build_resource(extra: dict | None = None) -> Resource:
    attrs = {
        "service.name": "llm-gateway",
        "service.version": VERSION,
        "service.instance.id": instance_id(),
        # Phoenix/Arize read the project from this resource attribute; harmless for other tools.
        "openinference.project.name": (os.getenv("PHOENIX_PROJECT_NAME") or "llm-gateway").strip() or "llm-gateway",
    }
    if extra:
        attrs.update(extra)
    return Resource.create(attrs)


def setup_telemetry(app) -> None:
    """Set up tracing, metrics and exporters for the FastAPI app.

    ENABLE_TELEMETRY off: no exporter is created, nothing can retry against a collector.
    The Prometheus pull reader is still attached when OBS_PROMETHEUS is on.
    """
    from ..observability.collect.process import set_tracemalloc
    from ..observability.export.prometheus import build_prometheus_reader, prometheus_enabled
    from ..observability.export import sinks
    from ..observability.export.manager import managed_metric_exporter, managed_span_processor
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

    if os.getenv("OBS_TRACEMALLOC", "false").strip().lower() in {"1", "true", "yes", "on"}:
        set_tracemalloc(True)

    on = telemetry_enabled()
    if not on:
        logging.info(
            "Telemetry export disabled (set %s=true to enable OpenTelemetry/Phoenix export).",
            ENABLE_TELEMETRY_ENV,
        )
        # Explicitly neutralise OTLP env hints: opentelemetry lazily auto-configures an
        # exporter from OTEL_EXPORTER_OTLP_ENDPOINT on first use, which is what kept an
        # OTLP exporter alive and retrying with export "off".
        # keep_sdk: integrations configured in the admin UI export through the SDK at runtime,
        # so it must stay on. With no integration configured nothing leaves the process.
        neutralize_otlp_env(keep_sdk=True)
    else:
        silence_otlp_exporter_logs()

    plan = sinks.init_sinks() if on else {"phoenix_processor": None, "phoenix_project": None,
                                          "shared_processors": [], "readers": []}

    # Traces: exporter-free when off; otherwise the configured processors.
    extra = {"openinference.project.name": plan["phoenix_project"]} if plan["phoenix_project"] else None
    tracer_provider = SDKTracerProvider(resource=build_resource(extra))
    if plan["phoenix_processor"] is not None:
        tracer_provider.add_span_processor(plan["phoenix_processor"])
    for proc in plan["shared_processors"]:
        tracer_provider.add_span_processor(proc)
    # UI-managed integrations: attached once, always; it fans out to whatever is enabled.
    tracer_provider.add_span_processor(managed_span_processor())
    trace.set_tracer_provider(tracer_provider)

    # Metrics: Prometheus pull reader is independent of ENABLE_TELEMETRY.
    readers = list(plan["readers"])
    readers.append(PeriodicExportingMetricReader(managed_metric_exporter(), export_interval_millis=_interval_ms()))
    if prometheus_enabled():
        reader = build_prometheus_reader()
        if reader is not None:
            readers.append(reader)
    metrics.set_meter_provider(MeterProvider(resource=build_resource(), metric_readers=readers))
    setup_metrics()

    # Instrument FastAPI is intentionally NOT enabled: tenant tracers create their own root spans.


def setup_phoenix_telemetry():
    """Phoenix tenant-tracer setup (kept for callers that want a Phoenix-registered provider).

    Returns a plain, exporter-free provider when telemetry is off.
    """
    if not telemetry_enabled():
        return SDKTracerProvider()
    from phoenix.otel import register
    from ..observability.export.presets import resolve, PresetError

    try:
        endpoint = resolve("phoenix").traces.endpoint
        headers = dict(resolve("phoenix").traces.headers)
    except PresetError as exc:
        logging.warning("Phoenix preset not configured: %s", exc)
        return SDKTracerProvider()
    try:
        return register(
            project_name="llm-gateway",
            endpoint=endpoint,
            headers=headers or None,
            batch=True,
            auto_instrument=True,
            set_global_tracer_provider=False,
        )
    except Exception as e:
        logging.warning(f"Phoenix auto-instrumentation failed: {e}. Returning bare tracer.")
        return register(
            project_name="llm-gateway",
            endpoint=endpoint,
            headers=headers or None,
            batch=True,
            auto_instrument=False,
            set_global_tracer_provider=False,
        )
