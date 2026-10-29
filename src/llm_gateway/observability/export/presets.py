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
