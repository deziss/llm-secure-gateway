"""Single switch controlling all OpenTelemetry / Phoenix export.

Telemetry is **opt-in**.  When it is off the gateway creates no exporters at
all, so nothing retries against an unreachable collector.

Why this exists: with no switch, `setup_phoenix_telemetry()` always ran and
defaulted to `http://localhost:6006`, and docker-compose additionally set
`OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318`.  Inside a container
`localhost` is the container itself, so nothing was ever listening and the OTLP
exporters retried forever, flooding the logs with:

    Transient error HTTPConnectionPool(host='localhost', port=4318):
    ... [Errno 111] Connection refused ... retrying in 3.71s.
    Failed to export metrics batch due to timeout, max retries or shutdown.

Set ENABLE_TELEMETRY=true (plus a reachable collector endpoint) to turn it on.
"""

import os

_TRUTHY = {"1", "true", "yes", "on", "enabled"}

#: Env var name, so callers and docs agree on the spelling.
ENABLE_TELEMETRY_ENV = "ENABLE_TELEMETRY"


def neutralize_otlp_env() -> None:
    """Remove OTLP endpoint hints so no library can auto-configure an exporter.

    opentelemetry auto-configures from these on first use, so clearing them is
    what makes the switch authoritative rather than advisory.
    """
    for var in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    ):
        os.environ.pop(var, None)
    # Standard OpenTelemetry kill switch, honoured by the SDK itself.
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")


def telemetry_enabled() -> bool:
    """True only when telemetry export has been explicitly switched on."""
    return os.getenv(ENABLE_TELEMETRY_ENV, "false").strip().lower() in _TRUTHY


def silence_otlp_exporter_logs() -> None:
    """Quieten the OTLP exporters' own retry chatter.

    Even with telemetry enabled, a collector that goes away mid-run makes the
    exporter log a WARNING per retry and an ERROR per dropped batch.  That is
    noise about a side-channel, not about the gateway, so cap it at ERROR and
    let the gateway's own startup log state the collector's address.
    """
    import logging

    for name in (
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        "opentelemetry.exporter.otlp.proto.http.metric_exporter",
        "opentelemetry.exporter.otlp.proto.grpc.trace_exporter",
        "opentelemetry.exporter.otlp.proto.grpc.metric_exporter",
        "opentelemetry.sdk.metrics._internal.export",
    ):
        logging.getLogger(name).setLevel(logging.ERROR)
