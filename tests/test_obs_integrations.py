"""UI-managed integrations: reconcile at runtime, encrypted storage, validation, test button."""
import json
from types import SimpleNamespace

import httpx
import pytest

from llm_gateway.auth.models import Role
from llm_gateway.observability import api
from llm_gateway.observability.export import manager, presets

from tests.test_observability_api import FakeSession, _app, _client  # noqa: F401


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    async def no_db_reload():
        reloaded.append(1)

    reloaded.clear()
    monkeypatch.setattr(manager, "load_and_apply", no_db_reload)   # production re-reads the DB; tests use a fake session
    manager._reset_for_tests()
    yield
    manager._reset_for_tests()


reloaded: list = []


LANGFUSE = {"LANGFUSE_HOST": "https://lf.example.com", "LANGFUSE_PUBLIC_KEY": "pk-1", "LANGFUSE_SECRET_KEY": "sk-1"}


def test_apply_builds_rebuilds_and_tears_down():
    manager.apply({"langfuse": LANGFUSE})
    rt = manager.runtime_status()
    assert rt["langfuse"]["active"] and rt["langfuse"]["status"]["signals"] == ["traces"]
    assert "langfuse" in manager._span_processor.names()

    counters = manager._ACTIVE["langfuse"].status
    manager.apply({"langfuse": LANGFUSE})                       # unchanged: same status object, no rebuild
    assert manager._ACTIVE["langfuse"].status is counters

    manager.apply({"langfuse": {**LANGFUSE, "LANGFUSE_HOST": "https://other.example.com"}})
    assert manager._ACTIVE["langfuse"].status is not counters   # endpoint changed: rebuilt

    manager.apply({"langfuse": None})                           # disabled
    assert "langfuse" not in manager.runtime_status() and manager._span_processor.names() == []


def test_incomplete_config_is_reported_not_raised():
    manager.apply({"langfuse": {"LANGFUSE_HOST": "https://lf.example.com"}, "otlp": {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://c:4318"}})
    rt = manager.runtime_status()
    assert not rt["langfuse"]["active"] and "LANGFUSE_PUBLIC_KEY" in rt["langfuse"]["error"]
    assert rt["otlp"]["active"] and rt["otlp"]["status"]["signals"] == ["traces", "metrics"]
    assert "otlp" in manager._metric_exporter._targets          # metrics go through the dynamic reader


def test_every_preset_has_ui_metadata_matching_its_builder():
    assert set(presets.META) == set(presets.PRESETS)
    for name, meta in presets.META.items():
        assert meta["fields"] and meta["howto"] and meta["docs"].startswith("https://")
        required = {f["key"]: "x" for f in meta["fields"] if f["required"]}
        if required:                                           # filling the required fields must resolve
            presets.resolve(name, required if name != "grafana" else {**required, "GRAFANA_OTLP_ENDPOINT": "http://a:4318"})


class _FakeUser(SimpleNamespace):
    email = "admin@example.com"


async def _save(c, name, payload):
    return await c.put(f"/admin/observability/integrations/{name}", json=payload)


async def test_save_encrypts_secrets_and_never_returns_them(monkeypatch):
    sess = FakeSession()
    app = _app(Role.ADMIN, sess)
    async with _client(app) as c:
        r = await _save(c, "langfuse", {"enabled": True, "config": {"LANGFUSE_HOST": "https://lf.example.com", "LANGFUSE_PUBLIC_KEY": "pk-1"},
                                        "secrets": {"LANGFUSE_SECRET_KEY": "sk-very-secret"}})
        assert r.status_code == 200, r.text
        row = sess.rows["langfuse"]
        assert "sk-very-secret" not in json.dumps(row.config) and "sk-very-secret" not in (row.secrets_enc or "")
        assert manager.row_env(row)["LANGFUSE_SECRET_KEY"] == "sk-very-secret"
        assert reloaded, "saving must re-apply the integrations without a restart"

        # a second save with a blank secret keeps the stored one
        r = await _save(c, "langfuse", {"enabled": True, "config": {"LANGFUSE_HOST": "https://lf2.example.com", "LANGFUSE_PUBLIC_KEY": "pk-1"}, "secrets": {}})
        assert r.status_code == 200 and manager.row_env(sess.rows["langfuse"])["LANGFUSE_SECRET_KEY"] == "sk-very-secret"


@pytest.mark.parametrize("payload,code", [
    ({"config": {"NOPE": "x"}}, 422),                                                        # unknown field
    ({"config": {"LANGFUSE_SECRET_KEY": "x"}}, 422),                                          # secret sent as plain config
    ({"secrets": {"LANGFUSE_HOST": "x"}}, 422),                                               # plain sent as secret
    ({"config": {"LANGFUSE_HOST": "javascript:alert(1)", "LANGFUSE_PUBLIC_KEY": "p"}, "secrets": {"LANGFUSE_SECRET_KEY": "s"}}, 422),
    ({"config": {"LANGFUSE_HOST": "https://x.example.com"}}, 422),                           # incomplete: resolve() rejects it
])
async def test_save_validation(payload, code):
    async with _client(_app(Role.ADMIN)) as c:
        r = await _save(c, "langfuse", {"enabled": True, **payload})
    assert r.status_code == code, r.text


async def test_unknown_integration_and_roles():
    async with _client(_app(Role.ADMIN)) as c:
        assert (await _save(c, "nope", {"enabled": True})).status_code == 404
    async with _client(_app(Role.MANAGER)) as c:
        assert (await _save(c, "langfuse", {"enabled": True})).status_code == 403
        assert (await c.post("/admin/observability/integrations/langfuse/test", json={})).status_code == 403
    async with _client(_app(Role.VIEWER)) as c:
        assert (await c.get("/admin/observability/integrations")).status_code == 403


_REAL_POST = httpx.AsyncClient.post


def _intercept(monkeypatch, handler):
    """Fake only calls to the integration endpoints; the test client's own requests go through."""
    async def post(self, url, *a, **kw):
        if not str(url).startswith("http") or str(url).startswith("http://test"):
            return await _REAL_POST(self, url, *a, **kw)
        return await handler(self, url, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)


async def test_test_button_reports_backend_answer(monkeypatch):
    seen = {}

    async def fake_post(self, url, content=None, headers=None, **kw):
        seen.update(url=str(url), headers=headers, size=len(content))
        return httpx.Response(401, text="bad key", request=httpx.Request("POST", url))

    _intercept(monkeypatch, fake_post)
    body = {"enabled": True, "config": {"LANGFUSE_HOST": "https://lf.example.com", "LANGFUSE_PUBLIC_KEY": "pk"}, "secrets": {"LANGFUSE_SECRET_KEY": "sk"}}
    async with _client(_app(Role.ADMIN)) as c:
        r = (await c.post("/admin/observability/integrations/langfuse/test", json=body)).json()
    assert r["ok"] is False and r["status_code"] == 401 and "credentials rejected" in r["message"]
    assert seen["url"] == "https://lf.example.com/api/public/otel/v1/traces"
    assert seen["headers"]["Authorization"].startswith("Basic ") and seen["headers"]["Content-Type"] == "application/x-protobuf"
    assert seen["size"] > 100                                  # a real encoded span was sent
    assert "sk" not in json.dumps(r)


async def test_test_button_network_failure(monkeypatch):
    async def boom(self, *a, **k):
        raise httpx.ConnectError("connection refused for https://user:pw@h.example.com/x?key=SECRET")

    _intercept(monkeypatch, boom)
    async with _client(_app(Role.ADMIN)) as c:
        r = (await c.post("/admin/observability/integrations/otlp/test",
                          json={"enabled": True, "config": {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}})).json()
    assert r["ok"] is False and r["stage"] == "network"
    assert "SECRET" not in json.dumps(r) and "pw@" not in json.dumps(r)


def test_ui_config_pauses_the_same_tool_from_env():
    """A tool set both in OBS_EXPORTERS and in the UI must not export twice: the env copy pauses."""
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
    from llm_gateway.observability.export import sinks

    class Inner(SpanExporter):
        calls = 0
        def export(self, spans):
            Inner.calls += 1
            return SpanExportResult.SUCCESS

    env_copy = sinks._TrackedSpanExporter(Inner(), sinks.SinkStatus(name="langfuse"), suppressible=True)
    env_copy.export([object()])
    assert Inner.calls == 1

    manager.apply({"langfuse": LANGFUSE})          # now also managed in the UI
    assert sinks.is_superseded("langfuse")
    assert env_copy.export([object()]) == SpanExportResult.SUCCESS
    assert Inner.calls == 1                         # dropped, not sent twice
    assert env_copy._status.as_dict()["superseded"] is True

    manager.apply({"langfuse": None})               # turned off in the UI: env copy resumes
    env_copy.export([object()])
    assert Inner.calls == 2


def test_managed_phoenix_per_tenant_projects():
    from llm_gateway.telemetry.tracing import PhoenixTraceManager

    base = {"PHOENIX_COLLECTOR_ENDPOINT": "http://phoenix:6006"}
    manager.apply({"phoenix": base})
    assert manager.phoenix_mode() == "single"

    manager.apply({"phoenix": {**base, "PHOENIX_PROJECT_PER_TENANT": "true"}})
    assert manager.phoenix_mode() == "per_tenant"
    PhoenixTraceManager._managed.clear()
    PhoenixTraceManager.get_tracer("acme", "vllm")
    provider = PhoenixTraceManager._managed["gateway-acme-vllm"]
    assert provider.resource.attributes["openinference.project.name"] == "gateway-acme-vllm"
    assert provider.resource.attributes["service.name"] == "llm-gateway"
    assert manager.managed_span_processor() in provider._active_span_processor._span_processors
    PhoenixTraceManager._managed.clear()


def test_bool_field_is_described_for_the_ui():
    f = {x["key"]: x for x in presets.META["phoenix"]["fields"]}["PHOENIX_PROJECT_PER_TENANT"]
    assert f["type"] == "bool" and not f["secret"]
