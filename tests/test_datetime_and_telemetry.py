"""
Regression tests for two production breakages.

1. Login returned HTTP 500.

   Every timestamp column in this database is TIMESTAMP WITHOUT TIME ZONE and
   all code writes naive UTC values.  sqlmodel 0.0.23+ started mapping a bare
   `datetime` annotation to UTCDateTime(timezone=True), which rejects naive
   values outright:

       ValueError: Datetime values must have timezone information.
       [SQL: UPDATE "user" SET last_login=$1::TIMESTAMP WITH TIME ZONE ...]

   Because pyproject pins only `sqlmodel>=0.0.22`, this arrived silently via a
   dependency upgrade and broke on_after_login -- so every successful password
   check ended in a 500.

2. Logs flooded with OTLP "Connection refused" retries.

   Telemetry had no off switch, and opentelemetry lazily auto-configures an
   exporter from OTEL_EXPORTER_OTLP_ENDPOINT the first time a meter is
   requested.
"""
import os
from datetime import datetime, timezone

import pytest
from sqlmodel import SQLModel


def _table_models():
    """Every SQLModel class backed by a real table."""
    return [
        m
        for m in SQLModel.__subclasses__()
        if getattr(m, "__tablename__", None) is not None and hasattr(m, "__table__")
    ]


def test_all_table_timestamp_columns_are_naive():
    """
    Table columns must map to DateTime(timezone=False) to match the schema.

    If this fails after a dependency bump, the symptom is a 500 on login.
    """
    import llm_gateway.models  # noqa: F401  (registers the tables)
    import llm_gateway.auth.models  # noqa: F401

    offenders = []
    for model in _table_models():
        for col in model.__table__.columns:
            if not isinstance(getattr(col.type, "timezone", None), bool):
                continue
            if col.type.timezone is not False:
                offenders.append(f"{model.__tablename__}.{col.name} -> {col.type!r}")

    assert not offenders, (
        "these columns are timezone-aware but the database stores naive values:\n  "
        + "\n  ".join(offenders)
        + "\nPin sa_type=DateTime(timezone=False) on the field."
    )


def test_naive_utcnow_helpers_stay_naive():
    """on_after_login and the model defaults must produce naive values."""
    from llm_gateway.models import _utcnow

    value = _utcnow()
    assert value.tzinfo is None, "_utcnow() must return a naive datetime"
    # Sanity: it really is UTC, not local time.
    delta = abs((value - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds())
    assert delta < 60, "_utcnow() does not look like UTC"


class TestTelemetrySwitch:
    def test_defaults_to_off(self, monkeypatch):
        from llm_gateway.telemetry.config import telemetry_enabled

        monkeypatch.delenv("ENABLE_TELEMETRY", raising=False)
        assert telemetry_enabled() is False, "telemetry must be opt-in"

    @pytest.mark.parametrize("value", ["true", "True", "1", "yes", "on", " TRUE "])
    def test_accepts_truthy_spellings(self, monkeypatch, value):
        from llm_gateway.telemetry.config import telemetry_enabled

        monkeypatch.setenv("ENABLE_TELEMETRY", value)
        assert telemetry_enabled() is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "off", ""])
    def test_rejects_falsy_spellings(self, monkeypatch, value):
        from llm_gateway.telemetry.config import telemetry_enabled

        monkeypatch.setenv("ENABLE_TELEMETRY", value)
        assert telemetry_enabled() is False

    def test_disabled_clears_otlp_endpoint_env(self, monkeypatch):
        """
        Clearing these is what makes the switch authoritative: otherwise
        opentelemetry auto-builds an exporter from them on first use and
        retries against it forever.
        """
        from llm_gateway.telemetry.config import neutralize_otlp_env

        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "http://localhost:4318")
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)

        neutralize_otlp_env()

        assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in os.environ
        assert "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT" not in os.environ
        assert os.environ.get("OTEL_SDK_DISABLED") == "true"
        os.environ.pop("OTEL_SDK_DISABLED", None)


class TestPhoenixProjectTracing:
    @pytest.fixture(autouse=True)
    def _clean_tracers(self, monkeypatch):
        from llm_gateway.telemetry.tracing import PhoenixTraceManager
        from llm_gateway.observability.export import manager

        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        manager._reset_for_tests()
        PhoenixTraceManager._reset_for_tests()
        yield
        manager._reset_for_tests()
        PhoenixTraceManager._reset_for_tests()

    def test_project_name_formatting_and_sanitization(self):
        from llm_gateway.telemetry.tracing import PhoenixTraceManager

        assert PhoenixTraceManager.get_project_name("alice", "openai") == "gateway-alice-openai"
        assert PhoenixTraceManager.get_project_name("alice") == "gateway-alice"

        # Special characters replaced and sanitized
        name = PhoenixTraceManager.get_project_name("org/team:alice", "vllm/server:1")
        assert name == "gateway-org-team-alice-vllm-server-1"

    def test_start_root_span_isolated_from_ambient_context(self):
        """Root span created by PhoenixTraceManager must not inherit an active parent span."""
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from llm_gateway.observability.export import manager
        from llm_gateway.telemetry.tracing import PhoenixTraceManager

        manager.apply({"phoenix": {"PHOENIX_COLLECTOR_ENDPOINT": "http://phoenix:6006", "PHOENIX_PROJECT_PER_TENANT": "true"}})

        ambient_provider = TracerProvider()
        ambient_tracer = ambient_provider.get_tracer("ambient_http")

        with ambient_tracer.start_as_current_span("parent_fastapi_request"):
            parent_span = trace.get_current_span()
            assert parent_span.is_recording()

            # Start tenant span: must be an isolated root span
            tenant_span = PhoenixTraceManager.start_root_span("tenant-a", "vllm", "llm_request")
            try:
                assert tenant_span.parent is None, "Tenant span must have no parent to serve as root span"
            finally:
                tenant_span.end()

    def test_child_spans_inherit_isolated_root_span(self):
        """Any child spans created under the tenant root span must correctly inherit the root span."""
        from opentelemetry import trace
        from llm_gateway.observability.export import manager
        from llm_gateway.telemetry.tracing import PhoenixTraceManager

        manager.apply({"phoenix": {"PHOENIX_COLLECTOR_ENDPOINT": "http://phoenix:6006", "PHOENIX_PROJECT_PER_TENANT": "true"}})

        tenant_tracer = PhoenixTraceManager.get_tracer("tenant-b", "ollama")
        root_span = PhoenixTraceManager.start_root_span("tenant-b", "ollama", "llm_request")
        try:
            with trace.use_span(root_span):
                child_span = tenant_tracer.start_span("tool_call")
                try:
                    assert child_span.parent is not None
                    assert child_span.parent.span_id == root_span.get_span_context().span_id
                finally:
                    child_span.end()
        finally:
            root_span.end()

    def test_phoenix_project_per_tenant_disabled_in_env(self, monkeypatch):
        """When PHOENIX_PROJECT_PER_TENANT is false, per-tenant projects are not created."""
        from llm_gateway.telemetry.tracing import PhoenixTraceManager

        monkeypatch.setenv("ENABLE_TELEMETRY", "true")
        monkeypatch.setenv("PHOENIX_PROJECT_PER_TENANT", "false")

        tracer = PhoenixTraceManager.get_tracer("tenant-c", "vllm")
        assert "gateway-tenant-c-vllm" not in PhoenixTraceManager._tracers
        assert PhoenixTraceManager.get_tracer_count() == 0

    def test_set_llm_attributes_records_input_output_and_mime_types(self):
        """Verify set_llm_attributes sets input.value, input.mime_type, output.value, and output.mime_type."""
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        from llm_gateway.observability.export.semconv import set_llm_attributes

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("chat test-model") as span:
            set_llm_attributes(
                span,
                model="test-model",
                provider="vllm",
                operation="chat",
                input_value='[{"role": "user", "content": "hi"}]',
                input_mime_type="application/json",
                output_value="Hello there!",
                output_mime_type="text/plain",
            )

        finished = exporter.get_finished_spans()
        assert len(finished) == 1
        attrs = finished[0].attributes
        assert attrs["openinference.span.kind"] == "LLM"
        assert attrs["llm.model_name"] == "test-model"
        assert attrs["input.value"] == '[{"role": "user", "content": "hi"}]'
        assert attrs["input.mime_type"] == "application/json"
        assert attrs["output.value"] == "Hello there!"
        assert attrs["output.mime_type"] == "text/plain"

    def test_fastapi_app_disables_internal_telemetry(self):
        """FastAPI app instance must have internal HTTP and operation tracing disabled."""
        from llm_gateway.main import app
        assert hasattr(app, "_telemetry")
        assert app._telemetry.get("tracing") is False
        assert app._telemetry.get("operation_spans") is False


