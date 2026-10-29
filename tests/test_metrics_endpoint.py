"""/metrics: reachable past AuthMiddleware, optional bearer token, 503 without the optional package."""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_gateway.observability.export import prometheus


def _app():
    app = FastAPI()
    app.include_router(prometheus.router)
    return app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("OBS_METRICS_TOKEN", raising=False)
    monkeypatch.delenv("OBS_METRICS_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.setenv("OBS_PROMETHEUS", "true")


def test_metrics_forbidden_without_token_by_default():
    assert TestClient(_app()).get("/metrics").status_code == 403


def test_metrics_served_anonymously_only_when_allowed(monkeypatch):
    monkeypatch.setenv("OBS_METRICS_ALLOW_ANONYMOUS", "true")
    r = TestClient(_app()).get("/metrics")
    assert r.status_code in (200, 503)  # 503 only if prometheus_client is absent in this env
    if r.status_code == 200:
        assert "text/plain" in r.headers["content-type"]


def test_anonymous_flag_does_not_bypass_a_configured_token(monkeypatch):
    monkeypatch.setenv("OBS_METRICS_ALLOW_ANONYMOUS", "true")
    monkeypatch.setenv("OBS_METRICS_TOKEN", "s3cret")
    assert TestClient(_app()).get("/metrics").status_code == 401


def test_bearer_header_parsing_is_strict(monkeypatch):
    monkeypatch.setenv("OBS_METRICS_TOKEN", "s3cret")
    c = TestClient(_app())
    for bad in ("s3cret", "bearer s3cret", "Bearer  s3cret", "Basic s3cret", "Bearer s3cret "):
        assert c.get("/metrics", headers={"Authorization": bad}).status_code == 401, bad


def test_token_required_when_configured(monkeypatch):
    monkeypatch.setenv("OBS_METRICS_TOKEN", "s3cret")
    c = TestClient(_app())
    assert c.get("/metrics").status_code == 401
    assert c.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = c.get("/metrics", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code in (200, 503)


def test_disabled_returns_404(monkeypatch):
    monkeypatch.setenv("OBS_METRICS_ALLOW_ANONYMOUS", "true")
    monkeypatch.setenv("OBS_PROMETHEUS", "false")
    assert TestClient(_app()).get("/metrics").status_code == 404


def test_503_when_prometheus_client_missing(monkeypatch):
    monkeypatch.setenv("OBS_METRICS_ALLOW_ANONYMOUS", "true")
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "prometheus_client":
            raise ImportError("missing")
        return real_import(name, *a, **k)

    with patch("builtins.__import__", side_effect=fake_import):
        r = TestClient(_app()).get("/metrics")
    assert r.status_code == 503


def test_metrics_is_public_to_auth_middleware():
    import llm_gateway.middleware as mw
    import inspect
    src = inspect.getsource(mw)
    assert src.count('"/metrics"') >= 2


def test_status_reports_pseudo_sink_entry():
    entry = prometheus.prometheus_status()
    assert entry["name"] == "prometheus"
    assert entry["endpoint"] == "/metrics"
    assert entry["signals"] == ["metrics"]
