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
