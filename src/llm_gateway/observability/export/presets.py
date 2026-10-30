"""Named OTLP destinations, configured from each vendor's own environment variable names.

Config is env-only (``OBS_EXPORTERS=phoenix,langfuse,...``), so no credentials are stored in
the database. Adding a tool means adding one function and one entry in ``PRESETS``.

Each preset resolves to ``ResolvedPreset`` with a full OTLP/HTTP URL and headers per
signal. Endpoint paths and header names follow the vendor docs; the ones marked
"verify" are the least certain and should be confirmed against a live collector.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional
from urllib.parse import urlparse

TRACES = "traces"
METRICS = "metrics"

DEFAULT_PHOENIX_ENDPOINT = "http://localhost:6006"
DEFAULT_PROJECT = "llm-gateway"


class PresetError(ValueError):
    """Raised when a preset is selected but its required env vars are missing."""


@dataclass(frozen=True)
class SignalTarget:
    endpoint: str
    headers: dict = field(default_factory=dict)


@dataclass
class ResolvedPreset:
    name: str
    mode: str = "default"
    traces: Optional[SignalTarget] = None
    metrics: Optional[SignalTarget] = None
    resource: dict = field(default_factory=dict)

    def signals(self) -> list[str]:
        out = []
        if self.traces is not None:
            out.append(TRACES)
        if self.metrics is not None:
            out.append(METRICS)
        return out


def basic_auth(user: str, secret: str) -> str:
    raw = f"{user}:{secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _need(env: Mapping[str, str], *names: str) -> list[str]:
    values = []
    for n in names:
        v = (env.get(n) or "").strip()
        if not v:
            raise PresetError(f"missing required env var {n}")
        values.append(v)
    return values


def _base(url: str) -> str:
    return url.strip().rstrip("/")


def _with_path(base: str, path: str) -> str:
    base = _base(base)
    return base if base.endswith(path) else f"{base}{path}"


# ---------------------------------------------------------------- Phoenix family
def _phoenix(env: Mapping[str, str]) -> ResolvedPreset:
    project = (env.get("PHOENIX_PROJECT_NAME") or DEFAULT_PROJECT).strip() or DEFAULT_PROJECT
    resource = {"openinference.project.name": project}

    # Arize AX: a Space ID is the switch. Same OpenInference spans, different endpoint and auth.
    if (env.get("ARIZE_SPACE_ID") or "").strip():
        space, key = _need(env, "ARIZE_SPACE_ID", "ARIZE_API_KEY")
        endpoint = (env.get("ARIZE_OTLP_ENDPOINT") or "https://otlp.arize.com/v1/traces").strip()
        return ResolvedPreset(
            name="phoenix", mode="arize-ax",
            traces=SignalTarget(_with_path(endpoint, "/v1/traces"),
                                {"arize-space-id": space, "arize-api-key": key}),
            resource=resource,
        )

    endpoint = (env.get("PHOENIX_COLLECTOR_ENDPOINT") or DEFAULT_PHOENIX_ENDPOINT).strip()
    key = (env.get("PHOENIX_API_KEY") or "").strip()
    host = urlparse(endpoint).hostname or ""
    headers = {"authorization": f"Bearer {key}"} if key else {}

    # Phoenix Cloud: app.phoenix.arize.com, optionally /s/<space> in the base URL.
    if host.endswith("phoenix.arize.com"):
        if not key:
            raise PresetError("Phoenix Cloud needs PHOENIX_API_KEY")
        return ResolvedPreset(
            name="phoenix", mode="phoenix-cloud",
            traces=SignalTarget(_with_path(endpoint, "/v1/traces"), headers),
            resource=resource,
        )

    return ResolvedPreset(
        name="phoenix", mode="self-hosted",
        traces=SignalTarget(_with_path(endpoint, "/v1/traces"), headers),
        resource=resource,
    )


# ---------------------------------------------------------------- traces-only tools
def _langfuse(env: Mapping[str, str]) -> ResolvedPreset:
    host, pub, sec = _need(env, "LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
    return ResolvedPreset(
        name="langfuse",
        traces=SignalTarget(_with_path(host, "/api/public/otel/v1/traces"),
                            {"Authorization": basic_auth(pub, sec)}),
    )


def _langsmith(env: Mapping[str, str]) -> ResolvedPreset:
    (key,) = _need(env, "LANGSMITH_API_KEY")
    base = (env.get("LANGSMITH_ENDPOINT") or "https://api.smith.langchain.com").strip()
    project = (env.get("LANGSMITH_PROJECT") or DEFAULT_PROJECT).strip()
    return ResolvedPreset(
        name="langsmith",
        traces=SignalTarget(_with_path(base, "/otel/v1/traces"),
                            {"x-api-key": key, "Langsmith-Project": project}),
    )


# ---------------------------------------------------------------- traces + metrics tools
def _both(base: str, headers: dict, *, name: str, mode: str = "default", resource: Optional[dict] = None) -> ResolvedPreset:
    return ResolvedPreset(
        name=name, mode=mode,
        traces=SignalTarget(_with_path(base, "/v1/traces"), dict(headers)),
        metrics=SignalTarget(_with_path(base, "/v1/metrics"), dict(headers)),
        resource=resource or {},
    )


def _openobserve(env: Mapping[str, str]) -> ResolvedPreset:
    zo_url, user, token = _need(env, "ZO_URL", "ZO_USER", "ZO_TOKEN")
    org = (env.get("ZO_ORG") or "default").strip()
    stream = (env.get("ZO_STREAM") or "default").strip()
    base = _with_path(zo_url, f"/api/{org}")
    headers = {"Authorization": basic_auth(user, token), "stream-name": stream}
    return ResolvedPreset(
        name="openobserve",
        traces=SignalTarget(_with_path(base, "/v1/traces"), headers),
        metrics=SignalTarget(_with_path(base, "/v1/metrics"), headers),
    )


def _signoz(env: Mapping[str, str]) -> ResolvedPreset:
    key = (env.get("SIGNOZ_INGESTION_KEY") or "").strip()
    if key:
        region = (env.get("SIGNOZ_REGION") or "us").strip()
        base = f"https://ingest.{region}.signoz.cloud:443"
        return _both(base, {"signoz-ingestion-key": key}, name="signoz", mode="cloud")
    base = (env.get("SIGNOZ_OTLP_ENDPOINT") or "http://localhost:4318").strip()
    return _both(base, {}, name="signoz", mode="self-hosted")


def _grafana(env: Mapping[str, str]) -> ResolvedPreset:
    instance = (env.get("GRAFANA_OTLP_INSTANCE_ID") or "").strip()
    token = (env.get("GRAFANA_OTLP_TOKEN") or "").strip()
    if instance and token:
        base = _need(env, "GRAFANA_OTLP_ENDPOINT")[0]
        return _both(base, {"Authorization": basic_auth(instance, token)}, name="grafana", mode="cloud")
    base = (env.get("GRAFANA_OTLP_ENDPOINT") or "http://localhost:4318").strip()
    return _both(base, {}, name="grafana", mode="alloy")


def _datadog(env: Mapping[str, str]) -> ResolvedPreset:
    base = (env.get("DD_OTLP_ENDPOINT") or "http://localhost:4318").strip()
    return _both(base, {}, name="datadog", mode="local-agent")


def _honeycomb(env: Mapping[str, str]) -> ResolvedPreset:
    (key,) = _need(env, "HONEYCOMB_API_KEY")
    base = (env.get("HONEYCOMB_ENDPOINT") or "https://api.honeycomb.io").strip()
    return _both(base, {"x-honeycomb-team": key}, name="honeycomb")


def _newrelic(env: Mapping[str, str]) -> ResolvedPreset:
    (key,) = _need(env, "NEW_RELIC_LICENSE_KEY")
    base = (env.get("NEW_RELIC_OTLP_ENDPOINT") or "https://otlp.nr-data.net").strip()
    return _both(base, {"api-key": key}, name="newrelic")


def _elastic(env: Mapping[str, str]) -> ResolvedPreset:
    base, key = _need(env, "ELASTIC_OTLP_ENDPOINT", "ELASTIC_API_KEY")
    return _both(base, {"Authorization": f"ApiKey {key}"}, name="elastic")


def _otlp(env: Mapping[str, str]) -> ResolvedPreset:
    """Generic OTLP/HTTP, honouring the standard OTEL_EXPORTER_OTLP_* variables."""
    headers: dict = {}
    raw = (env.get("OTEL_EXPORTER_OTLP_HEADERS") or "").strip()
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                headers[k.strip()] = v.strip()

    traces_full = (env.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or "").strip()
    metrics_full = (env.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT") or "").strip()
    base = (env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    if not (base or traces_full or metrics_full):
        raise PresetError("missing OTEL_EXPORTER_OTLP_ENDPOINT")
    return ResolvedPreset(
        name="otlp",
        traces=SignalTarget(traces_full or _with_path(base, "/v1/traces"), dict(headers)) if (traces_full or base) else None,
        metrics=SignalTarget(metrics_full or _with_path(base, "/v1/metrics"), dict(headers)) if (metrics_full or base) else None,
    )


Builder = Callable[[Mapping[str, str]], ResolvedPreset]

PRESETS: dict[str, Builder] = {
    "phoenix": _phoenix,
    "langfuse": _langfuse,
    "langsmith": _langsmith,
    "openobserve": _openobserve,
    "signoz": _signoz,
    "grafana": _grafana,
    "datadog": _datadog,
    "honeycomb": _honeycomb,
    "newrelic": _newrelic,
    "elastic": _elastic,
    "otlp": _otlp,
}


def preset_names() -> list[str]:
    return sorted(PRESETS)


def resolve(name: str, env: Optional[Mapping[str, str]] = None) -> ResolvedPreset:
    """Resolve one preset from env. Raises PresetError on unknown name or missing config."""
    key = (name or "").strip().lower()
    if key not in PRESETS:
        raise PresetError(f"unknown exporter preset '{name}' (known: {', '.join(preset_names())})")
    return PRESETS[key](os.environ if env is None else env)


# ---------------------------------------------------------------------------
# UI metadata: what the Integrations tab shows and asks for. Field keys are the env var
# names the builders above read, so UI-stored config goes through the same resolve().
# F(key, label, secret=False, required=False, placeholder="", help="")
# ---------------------------------------------------------------------------
def _f(key, label, secret=False, required=False, placeholder="", help="", kind="text"):
    return {"key": key, "label": label, "secret": secret, "required": required,
            "placeholder": placeholder, "help": help, "type": kind}


META: dict[str, dict] = {
    "phoenix": {
        "title": "Arize Phoenix / Arize AX",
        "description": "LLM traces in OpenInference format. Self-hosted Phoenix, Phoenix Cloud, or Arize AX.",
        "docs": "https://arize.com/docs/phoenix",
        "howto": [
            "Self-hosted Phoenix: set the collector endpoint to your Phoenix URL (for example http://phoenix:6006). The API key is only needed when Phoenix auth is on.",
            "Phoenix Cloud: use https://app.phoenix.arize.com (add /s/<space> for a named space) and an API key from Settings.",
            "Arize AX: fill Space ID and API key instead; the endpoint defaults to https://otlp.arize.com.",
            "Press Test to send one span, then open the project in Phoenix/Arize to see it.",
        ],
        "fields": [
            _f("PHOENIX_COLLECTOR_ENDPOINT", "Phoenix endpoint", placeholder="http://phoenix:6006"),
            _f("PHOENIX_API_KEY", "Phoenix API key", secret=True, help="Phoenix Cloud or auth-enabled self-hosted"),
            _f("PHOENIX_PROJECT_NAME", "Project name", placeholder="llm-gateway"),
            _f("PHOENIX_PROJECT_PER_TENANT", "Separate project per gateway project and provider", kind="bool",
               help="Traces go to gateway-<project>-<provider> instead of the single project name"),
            _f("ARIZE_SPACE_ID", "Arize Space ID", help="Setting this switches to Arize AX"),
            _f("ARIZE_API_KEY", "Arize API key", secret=True),
            _f("ARIZE_OTLP_ENDPOINT", "Arize OTLP endpoint", placeholder="https://otlp.arize.com/v1/traces"),
        ],
    },
    "langfuse": {
        "title": "Langfuse",
        "description": "LLM tracing, cost and quality tracking over OpenTelemetry.",
        "docs": "https://langfuse.com/docs/opentelemetry/get-started",
        "howto": [
            "In Langfuse open Project settings → API keys and create a key pair.",
            "Host is https://cloud.langfuse.com, https://us.cloud.langfuse.com, or your self-hosted URL.",
            "Press Test; traces appear under Tracing in the project.",
        ],
        "fields": [
            _f("LANGFUSE_HOST", "Host", required=True, placeholder="https://cloud.langfuse.com"),
            _f("LANGFUSE_PUBLIC_KEY", "Public key", required=True, placeholder="pk-lf-…"),
            _f("LANGFUSE_SECRET_KEY", "Secret key", secret=True, required=True),
        ],
    },
    "langsmith": {
        "title": "LangSmith",
        "description": "LangChain's tracing and evaluation platform (OTLP traces).",
        "docs": "https://docs.smith.langchain.com",
        "howto": [
            "Create an API key under Settings → API keys.",
            "EU workspaces: set the endpoint to https://eu.api.smith.langchain.com.",
        ],
        "fields": [
            _f("LANGSMITH_API_KEY", "API key", secret=True, required=True),
            _f("LANGSMITH_ENDPOINT", "Endpoint", placeholder="https://api.smith.langchain.com"),
            _f("LANGSMITH_PROJECT", "Project", placeholder="llm-gateway"),
        ],
    },
    "openobserve": {
        "title": "OpenObserve",
        "description": "Logs, metrics and traces in one open-source backend (traces + metrics here).",
        "docs": "https://openobserve.ai/docs",
        "howto": [
            "URL is your OpenObserve base, for example http://openobserve:5080 or https://api.openobserve.ai.",
            "User is the login email; Token is the password or an ingestion token.",
            "Organization defaults to 'default'. Data lands in the stream you name (default 'default').",
        ],
        "fields": [
            _f("ZO_URL", "URL", required=True, placeholder="http://openobserve:5080"),
            _f("ZO_USER", "User (email)", required=True),
            _f("ZO_TOKEN", "Token / password", secret=True, required=True),
            _f("ZO_ORG", "Organization", placeholder="default"),
            _f("ZO_STREAM", "Stream", placeholder="default"),
        ],
    },
    "signoz": {
        "title": "SigNoz",
        "description": "Open-source APM with OTLP traces and metrics. Self-hosted or SigNoz Cloud.",
        "docs": "https://signoz.io/docs/opentelemetry-collection-agents/",
        "howto": [
            "Self-hosted: point at the collector's OTLP HTTP port, for example http://signoz-otel-collector:4318.",
            "SigNoz Cloud: fill the ingestion key and the region (us, eu, in) instead.",
        ],
        "fields": [
            _f("SIGNOZ_OTLP_ENDPOINT", "OTLP endpoint (self-hosted)", placeholder="http://signoz-otel-collector:4318"),
            _f("SIGNOZ_INGESTION_KEY", "Ingestion key (cloud)", secret=True, help="Setting this switches to SigNoz Cloud"),
            _f("SIGNOZ_REGION", "Cloud region", placeholder="us"),
        ],
    },
    "grafana": {
        "title": "Grafana (Alloy / Cloud)",
        "description": "OTLP into Grafana Alloy or an OpenTelemetry collector, or Grafana Cloud directly.",
        "docs": "https://grafana.com/docs/grafana-cloud/send-data/otlp/",
        "howto": [
            "Self-managed: set the OTLP endpoint of Alloy or your collector (port 4318).",
            "Grafana Cloud: copy endpoint, instance ID and token from Connections → OpenTelemetry.",
            "Metrics also reach Grafana by Prometheus scraping /metrics (see the Prometheus card).",
        ],
        "fields": [
            _f("GRAFANA_OTLP_ENDPOINT", "OTLP endpoint", placeholder="http://alloy:4318"),
            _f("GRAFANA_OTLP_INSTANCE_ID", "Cloud instance ID"),
            _f("GRAFANA_OTLP_TOKEN", "Cloud token", secret=True),
        ],
    },
    "datadog": {
        "title": "Datadog",
        "description": "Through a Datadog Agent with OTLP ingest enabled.",
        "docs": "https://docs.datadoghq.com/opentelemetry/",
        "howto": [
            "Enable OTLP ingest on the Agent (otlp_config.receiver.protocols.http) and point this at it.",
        ],
        "fields": [_f("DD_OTLP_ENDPOINT", "Agent OTLP endpoint", required=True, placeholder="http://datadog-agent:4318")],
    },
    "honeycomb": {
        "title": "Honeycomb",
        "description": "Traces and metrics to Honeycomb.",
        "docs": "https://docs.honeycomb.io/send-data/opentelemetry/",
        "howto": ["Create an Ingest API key in the environment settings. EU: https://api.eu1.honeycomb.io."],
        "fields": [
            _f("HONEYCOMB_API_KEY", "Ingest API key", secret=True, required=True),
            _f("HONEYCOMB_ENDPOINT", "Endpoint", placeholder="https://api.honeycomb.io"),
        ],
    },
    "newrelic": {
        "title": "New Relic",
        "description": "OTLP into New Relic.",
        "docs": "https://docs.newrelic.com/docs/opentelemetry/",
        "howto": ["Use an ingest license key. EU accounts: https://otlp.eu01.nr-data.net."],
        "fields": [
            _f("NEW_RELIC_LICENSE_KEY", "License key", secret=True, required=True),
            _f("NEW_RELIC_OTLP_ENDPOINT", "Endpoint", placeholder="https://otlp.nr-data.net"),
        ],
    },
    "elastic": {
        "title": "Elastic APM",
        "description": "OTLP into Elastic (APM Server or the managed OTLP endpoint).",
        "docs": "https://www.elastic.co/docs/solutions/observability/apm/opentelemetry",
        "howto": ["Use the APM/OTLP endpoint and an API key (sent as 'Authorization: ApiKey …')."],
        "fields": [
            _f("ELASTIC_OTLP_ENDPOINT", "OTLP endpoint", required=True, placeholder="https://my-deployment.apm.example.com"),
            _f("ELASTIC_API_KEY", "API key", secret=True, required=True),
        ],
    },
    "otlp": {
        "title": "Generic OTLP/HTTP",
        "description": "Any OpenTelemetry collector or backend: Jaeger, Tempo, Uptrace, a vendor-neutral collector, …",
        "docs": "https://opentelemetry.io/docs/specs/otlp/",
        "howto": [
            "Endpoint is the OTLP/HTTP base URL; '/v1/traces' and '/v1/metrics' are appended.",
            "Headers are 'key=value' pairs separated by commas, for example 'Authorization=Bearer abc,X-Tenant=a'.",
        ],
        "fields": [
            _f("OTEL_EXPORTER_OTLP_ENDPOINT", "Endpoint", required=True, placeholder="http://collector:4318"),
            _f("OTEL_EXPORTER_OTLP_HEADERS", "Headers", secret=True, help="key=value,key2=value2"),
        ],
    },
}
