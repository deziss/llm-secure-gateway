"""
Tests for ALLOW_INSECURE_HTTP (dev mode): login over plain http://<lan-ip>.

Browsers drop `Secure` cookies on non-localhost HTTP origins, so dev mode turns
the flag off (and HSTS with it). With the setting off, behaviour must be
exactly what it was before the setting existed.
"""
import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_gateway import config, middleware
from llm_gateway.auth.users import build_cookie_transport


@pytest.fixture
def gateway_env(monkeypatch):
    """Re-read config + middleware module globals under the given env vars."""

    def _apply(**env):
        for key in ("ALLOW_INSECURE_HTTP", "FORCE_HTTPS"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        importlib.reload(config)
        importlib.reload(middleware)
        return middleware

    yield _apply

    monkeypatch.undo()
    importlib.reload(config)
    importlib.reload(middleware)


def _client(mw) -> TestClient:
    app = FastAPI()

    @app.get("/admin/ping")
    async def ping():
        return {"ok": True}

    app.add_middleware(mw.SecurityHeadersMiddleware)
    app.add_middleware(mw.CSRFMiddleware)
    return TestClient(app)


async def _session_set_cookie() -> str:
    response = await build_cookie_transport().get_login_response("token")
    return response.headers["set-cookie"].lower()


class TestSetting:
    def test_off_by_default(self, gateway_env):
        gateway_env()
        assert config.ALLOW_INSECURE_HTTP is False

    @pytest.mark.parametrize("value", ["true", "TRUE", "True"])
    def test_truthy_values(self, gateway_env, value):
        gateway_env(ALLOW_INSECURE_HTTP=value)
        assert config.ALLOW_INSECURE_HTTP is True

    @pytest.mark.parametrize("value", ["false", "1", "yes", ""])
    def test_only_true_enables_it(self, gateway_env, value):
        gateway_env(ALLOW_INSECURE_HTTP=value)
        assert config.ALLOW_INSECURE_HTTP is False


class TestSessionCookie:
    async def test_secure_by_default(self, gateway_env):
        gateway_env()
        cookie = await _session_set_cookie()
        assert "; secure" in cookie
        assert "httponly" in cookie
        assert "samesite=lax" in cookie

    async def test_not_secure_in_dev_mode(self, gateway_env):
        gateway_env(ALLOW_INSECURE_HTTP="true")
        cookie = await _session_set_cookie()
        assert "secure" not in cookie
        assert "httponly" in cookie
        assert "samesite=lax" in cookie

    async def test_logout_cookie_matches(self, gateway_env):
        # The logout cookie must carry the same flags or the browser won't clear it.
        gateway_env(ALLOW_INSECURE_HTTP="true")
        response = await build_cookie_transport().get_logout_response()
        assert "secure" not in response.headers["set-cookie"].lower()


class TestCsrfCookieAndHeaders:
    def test_default_unchanged(self, gateway_env):
        # FORCE_HTTPS unset: CSRF cookie was never Secure and no HSTS.
        resp = _client(gateway_env()).get("/admin/ping")
        assert "secure" not in resp.headers["set-cookie"].lower()
        assert "strict-transport-security" not in resp.headers

    def test_force_https_unchanged(self, gateway_env):
        resp = _client(gateway_env(FORCE_HTTPS="true")).get("/admin/ping")
        assert "; secure" in resp.headers["set-cookie"].lower()
        assert resp.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"

    def test_dev_mode_overrides_force_https(self, gateway_env):
        mw = gateway_env(FORCE_HTTPS="true", ALLOW_INSECURE_HTTP="true")
        resp = _client(mw).get("/admin/ping")
        assert "secure" not in resp.headers["set-cookie"].lower()
        assert "samesite=strict" in resp.headers["set-cookie"].lower()
        assert "strict-transport-security" not in resp.headers

    def test_csp_identical_in_both_modes(self, gateway_env):
        default_csp = _client(gateway_env()).get("/admin/ping").headers["content-security-policy"]
        dev_csp = _client(gateway_env(ALLOW_INSECURE_HTTP="true")).get(
            "/admin/ping"
        ).headers["content-security-policy"]
        assert dev_csp == default_csp
        assert "upgrade-insecure-requests" not in default_csp
        assert "default-src 'self'" in default_csp
