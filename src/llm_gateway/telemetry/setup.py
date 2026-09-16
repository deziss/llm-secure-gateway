import os
import logging

from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from phoenix.otel import register

from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider

from .metrics import setup_metrics


def setup_telemetry(app) -> None:
    """ Setup Phoenix OpenTelemetry for FastAPI app. """
    resource = Resource.create({
        "service.name": "llm-gateway",
        "service.version": os.getenv("APP_VERSION", "0.6.0"),
    })

    # Phoenix Tracing Setup (Default global tracer)
    tracer_provider = setup_phoenix_telemetry()

    # Add OTLP HTTP exporter if endpoint is set
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    # if otlp_endpoint:
    #     span_exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")
    #     tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))

    # Set the global tracer provider
    trace.set_tracer_provider(tracer_provider)

    # Metrics Setup with graceful fallback
    setup_metrics()

    # Try to use OTLP exporter if endpoint is configured
    metric_exporter = None
    if otlp_endpoint:
        try:
            # Test if endpoint is reachable before creating exporter
            import socket
            from urllib.parse import urlparse

            parsed = urlparse(otlp_endpoint)
            host = parsed.hostname or 'localhost'
            port = parsed.port or 4318

            # Quick connection test (timeout 2 seconds)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()

            if result == 0:
                # Endpoint is reachable, use OTLP exporter
                metric_exporter = OTLPMetricExporter(endpoint=f"{otlp_endpoint}/v1/metrics")
                logging.info(f"Metrics exporter connected to {otlp_endpoint}")
            else:
                # Endpoint unreachable, silent fallback
                logging.warning(f"OTLP metrics endpoint {otlp_endpoint} unreachable, disabling metrics export")
        except Exception as e:
            # Any error in setup, silent fallback
            logging.warning(f"Failed to setup OTLP metrics exporter: {e}, disabling metrics export")

    if metric_exporter:
        metric_reader = PeriodicExportingMetricReader(metric_exporter)
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    else:
        meter_provider = MeterProvider(resource=resource)

    metrics.set_meter_provider(meter_provider)

    # Instrument FastAPI - DISABLED for tenant project separation
    # FastAPI auto-instrumentation creates root spans with global project name.
    # Tenant tracers need to create root spans for proper project separation.
    # FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer_provider)


def setup_phoenix_telemetry() -> SDKTracerProvider:
    endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006")
    api_key = os.getenv("PHOENIX_API_KEY")

    # Ensure endpoint has /v1/traces for OTLP HTTP
    if not endpoint.endswith("/v1/traces"):
        otlp_endpoint = f"{endpoint}/v1/traces"
    else:
        otlp_endpoint = endpoint

    try:
        tracer_provider = register(
            project_name="llm-gateway",
            endpoint=otlp_endpoint,
            headers={"authorization": f"Bearer {api_key}"} if api_key else {},
            batch=True,
            auto_instrument=True,
            set_global_tracer_provider=False  # Changed to False for tenant project separation
        )
        return tracer_provider
    except Exception as e:
        logging.warning(
            f"Phoenix auto-instrumentation failed: {e}. Returning bare tracer."
        )
        return register(
            project_name="llm-gateway",
            endpoint=otlp_endpoint,
            headers={"authorization": f"Bearer {api_key}"} if api_key else {},
            batch=True,
            auto_instrument=False, # Disable auto-instrument on fallback
            set_global_tracer_provider=False
        )
