"""
Unit tests for AuthMiddleware, CSRFMiddleware, PolicyMiddleware,
SecurityHeadersMiddleware, and LoginRateLimitMiddleware.
"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch


class TestAuthMiddleware:
    def _make_request(self, path="/v1/chat/completions", method="POST", headers=None, cookies=None):
        request = MagicMock()
        request.url.path = path
        request.method = method
        request.headers = headers or {}
        request.cookies = cookies or {}
        request.state = MagicMock(spec=[])
        return request

    def _make_middleware(self):
        from llm_gateway.middleware import AuthMiddleware
        return AuthMiddleware(app=MagicMock())

    def test_skips_auth_for_root(self):
        middleware = self._make_middleware()
        request = self._make_request(path="/")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        result = asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once_with(request)

    def test_skips_auth_for_health(self):
        middleware = self._make_middleware()
        request = self._make_request(path="/health")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once_with(request)

    def test_skips_auth_for_docs(self):
        middleware = self._make_middleware()
        request = self._make_request(path="/docs")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once_with(request)

    def test_skips_auth_for_admin_routes(self):
        middleware = self._make_middleware()
        request = self._make_request(path="/admin/dashboard")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once_with(request)

    def test_skips_auth_for_auth_routes(self):
        middleware = self._make_middleware()
        request = self._make_request(path="/auth/login")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once_with(request)

    @patch("llm_gateway.middleware._async_session")
    @patch("llm_gateway.middleware.get_auth_service")
    def test_valid_api_key_header(self, mock_get_auth, mock_session_ctx):
        middleware = self._make_middleware()
        request = self._make_request(headers={"x-api-key": "sk-gateway-test123"})
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        mock_key = MagicMock()
        mock_auth = MagicMock()
        mock_auth.validate_key = AsyncMock(return_value=mock_key)
        mock_get_auth.return_value = mock_auth

        session = AsyncMock()
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        assert request.state.user == mock_key
        assert request.state.auth_method == "api_key"
        call_next.assert_awaited_once()

    @patch("llm_gateway.middleware._async_session")
    @patch("llm_gateway.middleware.get_auth_service")
    def test_valid_bearer_token(self, mock_get_auth, mock_session_ctx):
        middleware = self._make_middleware()
        request = self._make_request(headers={"Authorization": "Bearer sk-gateway-test456"})
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        mock_key = MagicMock()
        mock_auth = MagicMock()
        mock_auth.validate_key = AsyncMock(return_value=mock_key)
        mock_get_auth.return_value = mock_auth

        session = AsyncMock()
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        assert request.state.user == mock_key
        assert request.state.auth_method == "api_key"

    @patch("llm_gateway.middleware._async_session")
    @patch("llm_gateway.middleware.get_auth_service")
    def test_invalid_api_key_returns_401(self, mock_get_auth, mock_session_ctx):
        middleware = self._make_middleware()
        request = self._make_request(headers={"x-api-key": "invalid-key"})
        call_next = AsyncMock()

        mock_auth = MagicMock()
        mock_auth.validate_key = AsyncMock(return_value=None)
        mock_get_auth.return_value = mock_auth

        session = AsyncMock()
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        assert result.status_code == 401
        call_next.assert_not_awaited()

    def test_cookie_auth_passes_through(self):
        middleware = self._make_middleware()
        request = self._make_request(cookies={"fastapiusersauth": "session-token-xyz"})
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once_with(request)

    def test_spiffe_id_sets_workload_user(self):
        middleware = self._make_middleware()
        request = self._make_request(
            headers={"X-Spiffe-ID": "spiffe://cluster/ns/default/sa/my-workload"}
        )
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        assert request.state.user["type"] == "workload"
        assert request.state.user["spiffe_id"] == "spiffe://cluster/ns/default/sa/my-workload"
        assert request.state.auth_method == "spiffe"

    def test_no_credentials_returns_401(self):
        middleware = self._make_middleware()
        request = self._make_request()
        call_next = AsyncMock()

        result = asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        assert result.status_code == 401
        call_next.assert_not_awaited()

    def test_skips_auth_for_favicon(self):
        middleware = self._make_middleware()
        request = self._make_request(path="/favicon.ico")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once_with(request)

    def test_skips_auth_for_static(self):
        middleware = self._make_middleware()
        request = self._make_request(path="/static/js/globals.js")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once_with(request)


class TestCSRFMiddleware:
    def _make_request(self, method="GET", path="/admin/backends", headers=None, cookies=None):
        request = MagicMock()
        request.method = method
        request.url.path = path
        request.headers = headers or {}
        request.cookies = cookies or {}
        return request

    def _make_middleware(self):
        from llm_gateway.middleware import CSRFMiddleware
        return CSRFMiddleware(app=MagicMock())

    def test_get_request_passes_through(self):
        middleware = self._make_middleware()
        request = self._make_request(method="GET")
        response = MagicMock()
        response.set_cookie = MagicMock()
        call_next = AsyncMock(return_value=response)

        result = asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once()

    def test_get_sets_csrf_cookie_when_missing(self):
        middleware = self._make_middleware()
        request = self._make_request(method="GET", cookies={})
        response = MagicMock()
        response.set_cookie = MagicMock()
        call_next = AsyncMock(return_value=response)

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        response.set_cookie.assert_called_once()
        args = response.set_cookie.call_args
        assert args[0][0] == "csrf_token" or args[1].get("key") == "csrf_token" or args.args[0] == "csrf_token"

    def test_get_skips_cookie_when_present(self):
        middleware = self._make_middleware()
        request = self._make_request(method="GET", cookies={"csrf_token": "existing"})
        response = MagicMock()
        response.set_cookie = MagicMock()
        call_next = AsyncMock(return_value=response)

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        response.set_cookie.assert_not_called()

    def test_post_with_api_key_skips_csrf(self):
        middleware = self._make_middleware()
        request = self._make_request(
            method="POST",
            path="/admin/backends",
            headers={"x-api-key": "sk-gateway-test"},
        )
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        result = asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once()

    def test_post_with_bearer_skips_csrf(self):
        middleware = self._make_middleware()
        request = self._make_request(
            method="POST",
            path="/admin/backends",
            headers={"authorization": "Bearer jwt-token-here"},
        )
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once()

    def test_post_to_proxy_path_skips_csrf(self):
        middleware = self._make_middleware()
        request = self._make_request(method="POST", path="/v1/chat/completions")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once()

    def test_post_with_valid_csrf_token_passes(self):
        middleware = self._make_middleware()
        token = "valid-csrf-token-abc"
        request = self._make_request(
            method="POST",
            path="/admin/backends",
            headers={"x-csrf-token": token},
            cookies={"csrf_token": token},
        )
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        result = asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once()

    def test_post_with_missing_csrf_cookie_returns_403(self):
        middleware = self._make_middleware()
        request = self._make_request(
            method="POST",
            path="/admin/backends",
            headers={"x-csrf-token": "some-token"},
            cookies={},
        )
        call_next = AsyncMock()

        result = asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        assert result.status_code == 403
        call_next.assert_not_awaited()

    def test_post_with_mismatched_csrf_tokens_returns_403(self):
        middleware = self._make_middleware()
        request = self._make_request(
            method="POST",
            path="/admin/backends",
            headers={"x-csrf-token": "header-token"},
            cookies={"csrf_token": "cookie-token"},
        )
        call_next = AsyncMock()

        result = asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        assert result.status_code == 403
        call_next.assert_not_awaited()

    def test_post_with_no_csrf_header_returns_403(self):
        middleware = self._make_middleware()
        request = self._make_request(
            method="POST",
            path="/auth/register",
            cookies={"csrf_token": "cookie-token"},
        )
        call_next = AsyncMock()

        result = asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        assert result.status_code == 403

    def test_head_request_passes_through(self):
        middleware = self._make_middleware()
        request = self._make_request(method="HEAD")
        response = MagicMock()
        response.set_cookie = MagicMock()
        call_next = AsyncMock(return_value=response)

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once()

    def test_options_request_passes_through(self):
        middleware = self._make_middleware()
        request = self._make_request(method="OPTIONS")
        response = MagicMock()
        response.set_cookie = MagicMock()
        call_next = AsyncMock(return_value=response)

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once()


class TestPolicyMiddleware:
    def _make_request(self, path="/v1/chat/completions", method="POST"):
        request = MagicMock()
        request.url.path = path
        request.method = method
        request.client.host = "127.0.0.1"
        request.state = MagicMock()
        request.state.user = {"scopes": ["chat"]}
        return request

    def _make_middleware(self):
        from llm_gateway.middleware import PolicyMiddleware
        return PolicyMiddleware(app=MagicMock())

    def test_skips_health_endpoint(self):
        middleware = self._make_middleware()
        request = self._make_request(path="/health")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once()

    def test_skips_admin_routes(self):
        middleware = self._make_middleware()
        request = self._make_request(path="/admin/metrics")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once()

    def test_skips_docs_route(self):
        middleware = self._make_middleware()
        request = self._make_request(path="/docs")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once()

    @patch("llm_gateway.audit.get_audit_logger")
    @patch("llm_gateway.policy.get_policy_engine")
    def test_allowed_request_passes(self, mock_engine_fn, mock_audit_fn):
        middleware = self._make_middleware()
        request = self._make_request()
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        mock_engine = MagicMock()
        mock_engine.evaluate = AsyncMock(return_value=True)
        mock_engine_fn.return_value = mock_engine

        mock_audit = MagicMock()
        mock_audit.log = MagicMock()
        mock_audit_fn.return_value = mock_audit

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once()
        mock_audit.log.assert_called_once()
        assert mock_audit.log.call_args[1]["decision"] == "allow"

    def test_skips_auth_routes(self):
        middleware = self._make_middleware()
        request = self._make_request(path="/auth/login")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once()

    def test_skips_static_routes(self):
        middleware = self._make_middleware()
        request = self._make_request(path="/static/css/tailwind.min.css")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once()

    def test_skips_favicon(self):
        middleware = self._make_middleware()
        request = self._make_request(path="/favicon.ico")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once()

    @patch("llm_gateway.audit.get_audit_logger")
    @patch("llm_gateway.policy.get_policy_engine")
    def test_denied_request_returns_403(self, mock_engine_fn, mock_audit_fn):
        middleware = self._make_middleware()
        request = self._make_request()
        call_next = AsyncMock()

        mock_engine = MagicMock()
        mock_engine.evaluate = AsyncMock(return_value=False)
        mock_engine_fn.return_value = mock_engine

        mock_audit = MagicMock()
        mock_audit.log = MagicMock()
        mock_audit_fn.return_value = mock_audit

        result = asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        assert result.status_code == 403
        call_next.assert_not_awaited()
        assert mock_audit.log.call_args[1]["decision"] == "deny"


class TestSecurityHeadersMiddleware:
    def _make_middleware(self):
        from llm_gateway.middleware import SecurityHeadersMiddleware
        return SecurityHeadersMiddleware(app=MagicMock())

    def test_injects_security_headers(self):
        middleware = self._make_middleware()
        request = MagicMock()
        request.url.path = "/admin/dashboard"
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        assert "X-Content-Type-Options" in result.headers
        assert result.headers["X-Content-Type-Options"] == "nosniff"
        assert "X-Frame-Options" in result.headers
        assert result.headers["X-Frame-Options"] == "DENY"
        assert "Referrer-Policy" in result.headers
        assert "Content-Security-Policy" in result.headers
        assert "Permissions-Policy" in result.headers

    def test_no_hsts_without_https(self):
        middleware = self._make_middleware()
        request = MagicMock()
        request.url.path = "/"
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        assert "Strict-Transport-Security" not in result.headers


class TestLoginRateLimitMiddleware:
    def _make_middleware(self):
        from llm_gateway.middleware import LoginRateLimitMiddleware
        return LoginRateLimitMiddleware(app=MagicMock())

    def _make_request(self, path="/auth/cookie/login", method="POST", ip="1.2.3.4"):
        request = MagicMock()
        request.url.path = path
        request.method = method
        request.client.host = ip
        return request

    def test_non_login_passes_through(self):
        middleware = self._make_middleware()
        request = self._make_request(path="/api/chat")
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        result = asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        call_next.assert_awaited_once()

    def test_successful_login_not_counted(self):
        middleware = self._make_middleware()
        request = self._make_request()
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        result = asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        assert "1.2.3.4" not in middleware._attempts

    def test_failed_login_counted(self):
        middleware = self._make_middleware()
        request = self._make_request()
        call_next = AsyncMock(return_value=MagicMock(status_code=400))

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        assert len(middleware._attempts["1.2.3.4"]) == 1

    def test_lockout_after_max_attempts(self):
        middleware = self._make_middleware()
        middleware.MAX_ATTEMPTS = 3
        call_next = AsyncMock(return_value=MagicMock(status_code=400))

        for _ in range(3):
            request = self._make_request()
            asyncio.get_event_loop().run_until_complete(
                middleware.dispatch(request, call_next)
            )

        # 4th attempt should be blocked
        request = self._make_request()
        result = asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(request, call_next)
        )
        assert result.status_code == 429
        assert "Retry-After" in result.headers

    def test_different_ips_not_affected(self):
        middleware = self._make_middleware()
        middleware.MAX_ATTEMPTS = 2
        call_next = AsyncMock(return_value=MagicMock(status_code=400))

        # Exhaust IP 1
        for _ in range(2):
            asyncio.get_event_loop().run_until_complete(
                middleware.dispatch(self._make_request(ip="10.0.0.1"), call_next)
            )

        # Different IP should still work
        call_next_ok = AsyncMock(return_value=MagicMock(status_code=200))
        result = asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(self._make_request(ip="10.0.0.2"), call_next_ok)
        )
        call_next_ok.assert_awaited_once()
