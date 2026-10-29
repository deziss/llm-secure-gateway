"""Each preset resolves to the exact endpoint and headers; ENABLE_TELEMETRY-only backward compat."""
import base64

import pytest

from llm_gateway.observability.export.presets import resolve, PresetError, basic_auth
from llm_gateway.observability.export.sinks import configured_names


def _b64(user, pw):
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def test_phoenix_self_hosted_bearer_and_path():
    p = resolve("phoenix", {"PHOENIX_COLLECTOR_ENDPOINT": "http://phoenix:6006", "PHOENIX_API_KEY": "k"})
    assert p.mode == "self-hosted"
    assert p.traces.endpoint == "http://phoenix:6006/v1/traces"
    assert p.traces.headers == {"authorization": "Bearer k"}
    assert p.metrics is None
    assert p.resource["openinference.project.name"] == "llm-gateway"


def test_phoenix_self_hosted_without_key_has_no_auth_header():
    p = resolve("phoenix", {"PHOENIX_COLLECTOR_ENDPOINT": "http://phoenix:6006/v1/traces"})
    assert p.traces.endpoint == "http://phoenix:6006/v1/traces"
    assert p.traces.headers == {}


def test_phoenix_cloud_with_space_path():
    p = resolve("phoenix", {"PHOENIX_COLLECTOR_ENDPOINT": "https://app.phoenix.arize.com/s/myspace",
                            "PHOENIX_API_KEY": "pk"})
    assert p.mode == "phoenix-cloud"
    assert p.traces.endpoint == "https://app.phoenix.arize.com/s/myspace/v1/traces"
    assert p.traces.headers == {"authorization": "Bearer pk"}


def test_phoenix_cloud_requires_key():
    with pytest.raises(PresetError):
        resolve("phoenix", {"PHOENIX_COLLECTOR_ENDPOINT": "https://app.phoenix.arize.com"})


def test_arize_ax_selected_by_space_id():
    p = resolve("phoenix", {"ARIZE_SPACE_ID": "sp1", "ARIZE_API_KEY": "ak", "PHOENIX_PROJECT_NAME": "proj"})
    assert p.mode == "arize-ax"
    assert p.traces.endpoint == "https://otlp.arize.com/v1/traces"
    assert p.traces.headers == {"arize-space-id": "sp1", "arize-api-key": "ak"}
    assert p.resource["openinference.project.name"] == "proj"


def test_langfuse_basic_b64_and_path():
    p = resolve("langfuse", {"LANGFUSE_HOST": "https://lf.example.com/", "LANGFUSE_PUBLIC_KEY": "pk-lf-1",
                             "LANGFUSE_SECRET_KEY": "sk-lf-2"})
    assert p.traces.endpoint == "https://lf.example.com/api/public/otel/v1/traces"
    assert p.traces.headers == {"Authorization": _b64("pk-lf-1", "sk-lf-2")}
    assert p.metrics is None


def test_langsmith_headers():
    p = resolve("langsmith", {"LANGSMITH_API_KEY": "ls-key", "LANGSMITH_PROJECT": "p1"})
    assert p.traces.endpoint == "https://api.smith.langchain.com/otel/v1/traces"
    assert p.traces.headers == {"x-api-key": "ls-key", "Langsmith-Project": "p1"}


def test_openobserve_basic_and_stream_name_both_signals():
    p = resolve("openobserve", {"ZO_URL": "http://zo:5080", "ZO_USER": "u@x", "ZO_TOKEN": "t",
                                "ZO_ORG": "acme", "ZO_STREAM": "llm"})
    assert p.traces.endpoint == "http://zo:5080/api/acme/v1/traces"
    assert p.metrics.endpoint == "http://zo:5080/api/acme/v1/metrics"
    assert p.traces.headers == {"Authorization": _b64("u@x", "t"), "stream-name": "llm"}


def test_signoz_self_hosted_and_cloud_key():
    s = resolve("signoz", {"SIGNOZ_OTLP_ENDPOINT": "http://sz:4318"})
    assert s.traces.endpoint == "http://sz:4318/v1/traces"
    assert s.metrics.endpoint == "http://sz:4318/v1/metrics"
    assert s.traces.headers == {}
    c = resolve("signoz", {"SIGNOZ_INGESTION_KEY": "key123", "SIGNOZ_REGION": "eu"})
    assert c.traces.endpoint == "https://ingest.eu.signoz.cloud:443/v1/traces"
    assert c.traces.headers == {"signoz-ingestion-key": "key123"}


def test_grafana_cloud_basic_auth_and_alloy_default():
    g = resolve("grafana", {"GRAFANA_OTLP_ENDPOINT": "https://otlp-gateway-prod.grafana.net/otlp",
                            "GRAFANA_OTLP_INSTANCE_ID": "123", "GRAFANA_OTLP_TOKEN": "glc"})
    assert g.traces.endpoint == "https://otlp-gateway-prod.grafana.net/otlp/v1/traces"
    assert g.traces.headers == {"Authorization": _b64("123", "glc")}
    a = resolve("grafana", {})
    assert a.traces.endpoint == "http://localhost:4318/v1/traces"


def test_honeycomb_team_header_required():
    h = resolve("honeycomb", {"HONEYCOMB_API_KEY": "hk"})
    assert h.traces.endpoint == "https://api.honeycomb.io/v1/traces"
    assert h.traces.headers == {"x-honeycomb-team": "hk"}
    with pytest.raises(PresetError):
        resolve("honeycomb", {})


def test_newrelic_api_key_header():
    n = resolve("newrelic", {"NEW_RELIC_LICENSE_KEY": "nr"})
    assert n.metrics.endpoint == "https://otlp.nr-data.net/v1/metrics"
    assert n.traces.headers == {"api-key": "nr"}


def test_elastic_apikey_header():
    e = resolve("elastic", {"ELASTIC_OTLP_ENDPOINT": "https://es.example:443", "ELASTIC_API_KEY": "b64key"})
    assert e.traces.endpoint == "https://es.example:443/v1/traces"
    assert e.traces.headers == {"Authorization": "ApiKey b64key"}


def test_datadog_local_agent_default():
    d = resolve("datadog", {})
    assert d.traces.endpoint == "http://localhost:4318/v1/traces"
    assert d.metrics.endpoint == "http://localhost:4318/v1/metrics"


def test_generic_otlp_honours_standard_env_and_headers():
    o = resolve("otlp", {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://jaeger:4318",
                         "OTEL_EXPORTER_OTLP_HEADERS": "x-a=1, x-b=2"})
    assert o.traces.endpoint == "http://jaeger:4318/v1/traces"
    assert o.traces.headers == {"x-a": "1", "x-b": "2"}
    full = resolve("otlp", {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://t/custom"})
    assert full.traces.endpoint == "http://t/custom"
    with pytest.raises(PresetError):
        resolve("otlp", {})


def test_unknown_preset_raises():
    with pytest.raises(PresetError):
        resolve("nope", {})


def test_missing_required_env_names_the_variable():
    with pytest.raises(PresetError, match="LANGFUSE_SECRET_KEY"):
        resolve("langfuse", {"LANGFUSE_HOST": "https://x", "LANGFUSE_PUBLIC_KEY": "p"})


def test_basic_auth_helper():
    assert basic_auth("a", "b") == _b64("a", "b")


def test_backward_compat_enable_telemetry_only_means_phoenix_and_otlp():
    assert configured_names({"ENABLE_TELEMETRY": "true"}) == ["phoenix", "otlp"]
    assert configured_names({}) == []
    assert configured_names({"ENABLE_TELEMETRY": "true", "OBS_EXPORTERS": "langfuse, signoz"}) == ["langfuse", "signoz"]
