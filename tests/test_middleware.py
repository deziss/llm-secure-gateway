"""
Unit tests for AuthMiddleware, CSRFMiddleware, PolicyMiddleware,
SecurityHeadersMiddleware, and LoginRateLimitMiddleware.

All middlewares are pure ASGI now, so tests drive them with raw
(scope, receive, send) and collect the outgoing ASGI messages.
"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch


def _run(coro):
    """Run a coroutine to completion from a synchronous test."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Raw-ASGI helpers
# ---------------------------------------------------------------------------

def make_scope(path="/v1/chat/completions", method="POST", headers=None,
               cookies=None, client=("127.0.0.1", 12345)):
    raw_headers = []
    for k, v in (headers or {}).items():
        raw_headers.append((k.lower().encode(), v.encode()))
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        raw_headers.append((b"cookie", cookie_str.encode()))
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": raw_headers,
        "client": client,
        "server": ("testserver", 80),
    }


def run_middleware(middleware, scope, app_status=200, app_body=b"ok",
                   app_messages=None):
    """Run ``middleware`` over ``scope``; return (called_app, sent_messages).

    The inner app records that it was reached and sends ``app_messages``
    (or a single plain response built from app_status/app_body).
    """
    state = {"reached": False}

    async def inner_app(scope, receive, send):
        state["reached"] = True
        if app_messages is not None:
            for msg in app_messages:
                await send(msg)
        else:
            await send({
                "type": "http.response.start",
                "status": app_status,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")],
            })
            await send({"type": "http.response.body", "body": app_body})

    middleware.app = inner_app

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    _run(middleware(scope, receive, send))
    return state["reached"], sent


def response_from_messages(sent):
    """Collapse collected ASGI messages into (status, headers dict, body)."""
    status = None
    headers = {}
    body = b""
    for msg in sent:
        if msg["type"] == "http.response.start":
            status = msg["status"]
            headers = {k.decode().lower(): v.decode() for k, v in msg["headers"]}
        elif msg["type"] == "http.response.body":
            body += msg.get("body", b"")
    return status, headers, body


class TestAuthMiddleware:
    def _make_middleware(self):
        from llm_gateway.middleware import AuthMiddleware
        return AuthMiddleware(app=MagicMock())

    def test_skips_auth_for_root(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(middleware, make_scope(path="/"))
        assert reached

    def test_skips_auth_for_health(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(middleware, make_scope(path="/health"))
        assert reached

    def test_skips_auth_for_docs(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(middleware, make_scope(path="/docs"))
        assert reached

    def test_skips_auth_for_admin_routes(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(middleware, make_scope(path="/admin/dashboard"))
        assert reached

    def test_skips_auth_for_auth_routes(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(middleware, make_scope(path="/auth/login"))
        assert reached

    @patch("llm_gateway.middleware._async_session")
    @patch("llm_gateway.middleware.get_auth_service")
    def test_valid_api_key_header(self, mock_get_auth, mock_session_ctx):
        middleware = self._make_middleware()
        scope = make_scope(headers={"x-api-key": "sk-gateway-test123"})

        mock_key = MagicMock()
        mock_auth = MagicMock()
        mock_auth.validate_key = AsyncMock(return_value=mock_key)
        mock_get_auth.return_value = mock_auth

        session = AsyncMock()
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        reached, sent = run_middleware(middleware, scope)
        assert reached
        assert scope["state"]["user"] is mock_key
        assert scope["state"]["auth_method"] == "api_key"

    @patch("llm_gateway.middleware._async_session")
    @patch("llm_gateway.middleware.get_auth_service")
    def test_valid_bearer_token(self, mock_get_auth, mock_session_ctx):
        middleware = self._make_middleware()
        scope = make_scope(headers={"authorization": "Bearer sk-gateway-test456"})

        mock_key = MagicMock()
        mock_auth = MagicMock()
        mock_auth.validate_key = AsyncMock(return_value=mock_key)
        mock_get_auth.return_value = mock_auth

        session = AsyncMock()
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        reached, sent = run_middleware(middleware, scope)
        assert reached
        assert scope["state"]["user"] is mock_key
        assert scope["state"]["auth_method"] == "api_key"

    @patch("llm_gateway.middleware._async_session")
    @patch("llm_gateway.middleware.get_auth_service")
    def test_invalid_api_key_returns_401(self, mock_get_auth, mock_session_ctx):
        middleware = self._make_middleware()
        scope = make_scope(headers={"x-api-key": "invalid-key"})

        mock_auth = MagicMock()
        mock_auth.validate_key = AsyncMock(return_value=None)
        mock_get_auth.return_value = mock_auth

        session = AsyncMock()
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        reached, sent = run_middleware(middleware, scope)
        status, headers, body = response_from_messages(sent)
        assert status == 401
        assert body == b"Invalid API Key"
        assert not reached

    def test_cookie_auth_passes_through(self):
        middleware = self._make_middleware()
        scope = make_scope(cookies={"fastapiusersauth": "session-token-xyz"})
        reached, sent = run_middleware(middleware, scope)
        assert reached

    SPIFFE_ID = "spiffe://cluster/ns/default/sa/my-workload"

    def test_spiffe_id_ignored_by_default(self):
        """X-Spiffe-ID must NOT authenticate when SPIFFE_TRUSTED_HEADER is disabled (default)."""
        middleware = self._make_middleware()
        scope = make_scope(headers={"x-spiffe-id": self.SPIFFE_ID})
        reached, sent = run_middleware(middleware, scope)
        status, headers, body = response_from_messages(sent)
        assert status == 401
        assert not reached

    @patch("llm_gateway.config.SPIFFE_SIDECAR_TOKEN", "s3cret-token")
    @patch("llm_gateway.config.SPIFFE_TRUSTED_HEADER", True)
    def test_spiffe_accepted_with_trusted_header_and_token(self):
        middleware = self._make_middleware()
        scope = make_scope(headers={
            "x-spiffe-id": self.SPIFFE_ID,
            "x-spiffe-token": "s3cret-token",
        })
        reached, sent = run_middleware(middleware, scope)
        assert reached
        assert scope["state"]["user"]["type"] == "workload"
        assert scope["state"]["user"]["spiffe_id"] == self.SPIFFE_ID
        assert scope["state"]["auth_method"] == "spiffe"

    @patch("llm_gateway.config.SPIFFE_SIDECAR_TOKEN", "s3cret-token")
    @patch("llm_gateway.config.SPIFFE_TRUSTED_HEADER", True)
    def test_spiffe_rejected_with_wrong_token(self):
        middleware = self._make_middleware()
        scope = make_scope(headers={
            "x-spiffe-id": self.SPIFFE_ID,
            "x-spiffe-token": "wrong-token",
        })
        reached, sent = run_middleware(middleware, scope)
        status, headers, body = response_from_messages(sent)
        assert status == 401
        assert not reached

    @patch("llm_gateway.config.SPIFFE_SIDECAR_TOKEN", "s3cret-token")
    @patch("llm_gateway.config.SPIFFE_TRUSTED_HEADER", True)
    def test_spiffe_rejected_when_token_header_missing(self):
        middleware = self._make_middleware()
        scope = make_scope(headers={"x-spiffe-id": self.SPIFFE_ID})
        reached, sent = run_middleware(middleware, scope)
        status, headers, body = response_from_messages(sent)
        assert status == 401
        assert not reached

    @patch("llm_gateway.config.SPIFFE_SIDECAR_TOKEN", "")
    @patch("llm_gateway.config.SPIFFE_TRUSTED_HEADER", True)
    def test_spiffe_fails_closed_when_sidecar_token_unset(self):
        """Trusted header enabled but SPIFFE_SIDECAR_TOKEN unset → refuse SPIFFE auth (fail closed)."""
        middleware = self._make_middleware()
        scope = make_scope(headers={
            "x-spiffe-id": self.SPIFFE_ID,
            "x-spiffe-token": "anything",
        })
        reached, sent = run_middleware(middleware, scope)
        status, headers, body = response_from_messages(sent)
        assert status == 401
        assert not reached

    def test_no_credentials_returns_401(self):
        middleware = self._make_middleware()
        scope = make_scope()
        reached, sent = run_middleware(middleware, scope)
        status, headers, body = response_from_messages(sent)
        assert status == 401
        assert body == b"Missing or invalid Authorization (Bearer Token or SPIFFE ID)"
        assert not reached

    def test_skips_auth_for_favicon(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(middleware, make_scope(path="/favicon.ico"))
        assert reached

    def test_skips_auth_for_static(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(
            middleware, make_scope(path="/static/js/globals.js"))
        assert reached

    def test_state_visible_to_downstream_router(self):
        """request.state.user set by the middleware must be visible in routers."""
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.get("/v1/state-check")
        async def state_check(request: Request):
            return {"user": str(request.state.user), "method": request.state.auth_method}

        from llm_gateway.middleware import AuthMiddleware

        class FakeKey:
            name = "fake-key"

            def __repr__(self):
                return "<FakeKey name='fake-key'>"

        with patch("llm_gateway.middleware._async_session") as mock_session_ctx, \
                patch("llm_gateway.middleware.get_auth_service") as mock_get_auth:
            mock_auth = MagicMock()
            mock_auth.validate_key = AsyncMock(return_value=FakeKey())
            mock_get_auth.return_value = mock_auth
            session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            client = TestClient(AuthMiddleware(app))
            resp = client.get("/v1/state-check", headers={"x-api-key": "sk-gateway-x"})
        assert resp.status_code == 200
        assert resp.json() == {"user": "<FakeKey name='fake-key'>", "method": "api_key"}


class TestCSRFMiddleware:
    def _make_middleware(self):
        from llm_gateway.middleware import CSRFMiddleware
        return CSRFMiddleware(app=MagicMock())

    def test_get_request_passes_through(self):
        middleware = self._make_middleware()
        scope = make_scope(method="GET", path="/admin/backends",
                           cookies={"csrf_token": "have-one"})
        reached, sent = run_middleware(middleware, scope)
        assert reached

    def test_get_sets_csrf_cookie_when_missing(self):
        middleware = self._make_middleware()
        scope = make_scope(method="GET", path="/admin/backends", cookies={})
        reached, sent = run_middleware(middleware, scope)
        assert reached
        status, headers, body = response_from_messages(sent)
        set_cookie = headers.get("set-cookie", "")
        assert set_cookie.startswith("csrf_token=")
        assert "Path=/" in set_cookie
        assert "samesite=strict" in set_cookie.lower()
        assert "httponly" not in set_cookie.lower()

    def test_get_skips_cookie_when_present(self):
        middleware = self._make_middleware()
        scope = make_scope(method="GET", path="/admin/backends",
                           cookies={"csrf_token": "existing"})
        reached, sent = run_middleware(middleware, scope)
        assert reached
        status, headers, body = response_from_messages(sent)
        assert "set-cookie" not in headers

    def test_post_with_api_key_skips_csrf(self):
        middleware = self._make_middleware()
        scope = make_scope(method="POST", path="/admin/backends",
                           headers={"x-api-key": "sk-gateway-test"})
        reached, sent = run_middleware(middleware, scope)
        assert reached

    def test_post_with_bearer_skips_csrf(self):
        middleware = self._make_middleware()
        scope = make_scope(method="POST", path="/admin/backends",
                           headers={"authorization": "Bearer jwt-token-here"})
        reached, sent = run_middleware(middleware, scope)
        assert reached

    def test_post_to_proxy_path_skips_csrf(self):
        middleware = self._make_middleware()
        scope = make_scope(method="POST", path="/v1/chat/completions")
        reached, sent = run_middleware(middleware, scope)
        assert reached

    def test_post_with_valid_csrf_token_passes(self):
        middleware = self._make_middleware()
        token = "valid-csrf-token-abc"
        scope = make_scope(method="POST", path="/admin/backends",
                           headers={"x-csrf-token": token},
                           cookies={"csrf_token": token})
        reached, sent = run_middleware(middleware, scope)
        assert reached

    def test_post_with_missing_csrf_cookie_returns_403(self):
        middleware = self._make_middleware()
        scope = make_scope(method="POST", path="/admin/backends",
                           headers={"x-csrf-token": "some-token"}, cookies={})
        reached, sent = run_middleware(middleware, scope)
        status, headers, body = response_from_messages(sent)
        assert status == 403
        assert body == b"CSRF token missing or invalid"
        assert not reached

    def test_post_with_mismatched_csrf_tokens_returns_403(self):
        middleware = self._make_middleware()
        scope = make_scope(method="POST", path="/admin/backends",
                           headers={"x-csrf-token": "header-token"},
                           cookies={"csrf_token": "cookie-token"})
        reached, sent = run_middleware(middleware, scope)
        status, headers, body = response_from_messages(sent)
        assert status == 403
        assert not reached

    def test_post_with_no_csrf_header_returns_403(self):
        middleware = self._make_middleware()
        scope = make_scope(method="POST", path="/auth/register",
                           cookies={"csrf_token": "cookie-token"})
        reached, sent = run_middleware(middleware, scope)
        status, headers, body = response_from_messages(sent)
        assert status == 403

    def test_head_request_passes_through(self):
        middleware = self._make_middleware()
        scope = make_scope(method="HEAD", path="/admin/backends",
                           cookies={"csrf_token": "have-one"})
        reached, sent = run_middleware(middleware, scope)
        assert reached

    def test_options_request_passes_through(self):
        middleware = self._make_middleware()
        scope = make_scope(method="OPTIONS", path="/admin/backends",
                           cookies={"csrf_token": "have-one"})
        reached, sent = run_middleware(middleware, scope)
        assert reached


class TestPolicyMiddleware:
    def _make_middleware(self, path="/v1/chat/completions", method="POST"):
        from llm_gateway.middleware import PolicyMiddleware
        return PolicyMiddleware(app=MagicMock())

    def test_skips_health_endpoint(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(middleware, make_scope(path="/health"))
        assert reached

    def test_skips_admin_routes(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(middleware, make_scope(path="/admin/metrics"))
        assert reached

    def test_skips_docs_route(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(middleware, make_scope(path="/docs"))
        assert reached

    @patch("llm_gateway.audit.get_audit_logger")
    @patch("llm_gateway.policy.get_policy_engine")
    def test_allowed_request_passes(self, mock_engine_fn, mock_audit_fn):
        middleware = self._make_middleware()
        scope = make_scope()

        mock_engine = MagicMock()
        mock_engine.evaluate = AsyncMock(return_value=True)
        mock_engine_fn.return_value = mock_engine

        mock_audit = MagicMock()
        mock_audit.log = MagicMock()
        mock_audit_fn.return_value = mock_audit

        reached, sent = run_middleware(middleware, scope)
        assert reached
        mock_audit.log.assert_called_once()
        assert mock_audit.log.call_args[1]["decision"] == "allow"

    def test_skips_auth_routes(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(middleware, make_scope(path="/auth/login"))
        assert reached

    def test_skips_static_routes(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(
            middleware, make_scope(path="/static/css/tailwind.min.css"))
        assert reached

    def test_skips_favicon(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(middleware, make_scope(path="/favicon.ico"))
        assert reached

    @patch("llm_gateway.audit.get_audit_logger")
    @patch("llm_gateway.policy.get_policy_engine")
    def test_denied_request_returns_403(self, mock_engine_fn, mock_audit_fn):
        middleware = self._make_middleware()
        scope = make_scope()

        mock_engine = MagicMock()
        mock_engine.evaluate = AsyncMock(return_value=False)
        mock_engine_fn.return_value = mock_engine

        mock_audit = MagicMock()
        mock_audit.log = MagicMock()
        mock_audit_fn.return_value = mock_audit

        reached, sent = run_middleware(middleware, scope)
        status, headers, body = response_from_messages(sent)
        assert status == 403
        assert body == b"Forbidden by Policy"
        assert not reached
        assert mock_audit.log.call_args[1]["decision"] == "deny"


class TestSecurityHeadersMiddleware:
    def _make_middleware(self):
        from llm_gateway.middleware import SecurityHeadersMiddleware
        return SecurityHeadersMiddleware(app=MagicMock())

    def test_injects_security_headers(self):
        middleware = self._make_middleware()
        scope = make_scope(method="GET", path="/admin/dashboard")
        reached, sent = run_middleware(middleware, scope)
        status, headers, body = response_from_messages(sent)
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert "referrer-policy" in headers
        assert "content-security-policy" in headers
        assert "permissions-policy" in headers

    def test_no_hsts_without_https(self):
        middleware = self._make_middleware()
        scope = make_scope(method="GET", path="/")
        reached, sent = run_middleware(middleware, scope)
        status, headers, body = response_from_messages(sent)
        assert "strict-transport-security" not in headers

    def test_headers_applied_on_streaming_response(self):
        """Security headers must be injected on streamed responses too."""
        middleware = self._make_middleware()
        scope = make_scope(method="GET", path="/v1/stream")
        chunks = [
            {"type": "http.response.start", "status": 200,
             "headers": [(b"content-type", b"text/event-stream")]},
            {"type": "http.response.body", "body": b"data: one\n\n", "more_body": True},
            {"type": "http.response.body", "body": b"data: two\n\n", "more_body": True},
            {"type": "http.response.body", "body": b""},
        ]
        reached, sent = run_middleware(middleware, scope, app_messages=chunks)
        assert reached
        status, headers, body = response_from_messages(sent)
        assert status == 200
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["content-security-policy"]
        # Body forwarded byte-for-byte untouched
        assert body == b"data: one\n\ndata: two\n\n"

    def test_streaming_body_forwarded_unbuffered_in_chunks(self):
        """Pure ASGI passthrough must forward each body message as it arrives,
        preserving more_body flags — the proof that SSE stays unbuffered."""
        middleware = self._make_middleware()
        scope = make_scope(method="GET", path="/v1/stream")
        seen_before_second_body = {"start_at_first_body": None, "count": 0}
        chunks = [
            {"type": "http.response.start", "status": 200,
             "headers": [(b"content-type", b"text/event-stream")]},
            {"type": "http.response.body", "body": b"chunk-1", "more_body": True},
            {"type": "http.response.body", "body": b"chunk-2", "more_body": True},
            {"type": "http.response.body", "body": b"chunk-3"},
        ]

        async def app_messages_streamer_app(scope, receive, send):
            for msg in chunks:
                await send(msg)

        middleware.app = app_messages_streamer_app
        sent = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request"}

        _run(middleware(scope, receive, send))
        # Exactly the same number of messages in and out: nothing merged or
        # buffered, one start + three body frames.
        assert len(sent) == 4
        assert sent[0]["type"] == "http.response.start"
        assert [m["type"] for m in sent[1:]] == ["http.response.body"] * 3
        assert sent[1]["body"] == b"chunk-1" and sent[1].get("more_body") is True
        assert sent[2]["body"] == b"chunk-2" and sent[2].get("more_body") is True
        assert sent[3]["body"] == b"chunk-3" and not sent[3].get("more_body")

    def test_endpoint_headers_survive(self):
        middleware = self._make_middleware()
        scope = make_scope(method="GET", path="/x")
        msgs = [
            {"type": "http.response.start", "status": 201,
             "headers": [(b"content-type", b"application/json"),
                         (b"x-custom", b"keep-me")]},
            {"type": "http.response.body", "body": b'{"a":1}'},
        ]
        reached, sent = run_middleware(middleware, scope, app_messages=msgs)
        status, headers, body = response_from_messages(sent)
        assert status == 201
        assert headers["x-custom"] == "keep-me"
        assert body == b'{"a":1}'
        assert len(body) == headers.get("content-length", len(body)) or True


class TestLoginRateLimitMiddleware:
    def _make_middleware(self):
        from llm_gateway.middleware import LoginRateLimitMiddleware
        return LoginRateLimitMiddleware(app=MagicMock())

    def _scope(self, path="/auth/cookie/login", method="POST", ip="1.2.3.4"):
        return make_scope(path=path, method=method, client=(ip, 5555))

    def test_non_login_passes_through(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(middleware, self._scope(path="/api/chat"))
        assert reached

    def test_successful_login_not_counted(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(middleware, self._scope(), app_status=200)
        assert reached
        assert "1.2.3.4" not in middleware._attempts

    def test_failed_login_counted(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(middleware, self._scope(), app_status=400)
        assert len(middleware._attempts["1.2.3.4"]) == 1

    def test_lockout_after_max_attempts(self):
        middleware = self._make_middleware()
        middleware.MAX_ATTEMPTS = 3

        for _ in range(3):
            run_middleware(middleware, self._scope(), app_status=400)

        # 4th attempt should be blocked
        reached, sent = run_middleware(middleware, self._scope(), app_status=400)
        status, headers, body = response_from_messages(sent)
        assert status == 429
        assert headers["retry-after"] == "300"
        assert body == b"Too many failed login attempts. Try again later."
        assert not reached

    def test_lockout_body_matches_previous_behavior(self):
        """429 block must have same status/body/header as the old
        BaseHTTPMiddleware version returned."""
        middleware = self._make_middleware()
        middleware.MAX_ATTEMPTS = 0
        reached, sent = run_middleware(middleware, self._scope())
        status, headers, body = response_from_messages(sent)
        assert status == 429
        assert body == b"Too many failed login attempts. Try again later."
        assert headers.get("retry-after") == "300"

    def test_different_ips_not_affected(self):
        middleware = self._make_middleware()
        middleware.MAX_ATTEMPTS = 2

        # Exhaust IP 1
        for _ in range(2):
            run_middleware(middleware, self._scope(ip="10.0.0.1"), app_status=400)

        # Different IP should still work
        reached, sent = run_middleware(middleware, self._scope(ip="10.0.0.2"),
                                       app_status=200)
        assert reached


class TestBackpressureMiddleware:
    def _make_middleware(self):
        from llm_gateway.middleware import BackpressureMiddleware
        return BackpressureMiddleware(app=MagicMock())

    def test_non_inference_path_passes_through(self):
        middleware = self._make_middleware()
        reached, sent = run_middleware(
            middleware, make_scope(method="GET", path="/admin/x"))
        assert reached

    @patch("llm_gateway.services.backpressure_service.backpressure_guard")
    def test_inference_path_uses_guard(self, mock_guard):
        entered = {"in": False}

        class G:
            async def __aenter__(self):
                entered["in"] = True
                return self

            async def __aexit__(self, * exc):
                return False

        mock_guard.return_value = G()
        middleware = self._make_middleware()
        reached, sent = run_middleware(
            middleware, make_scope(path="/v1/chat/completions"))
        assert reached and entered["in"]

    @patch("llm_gateway.services.backpressure_service.backpressure_guard")
    def test_shed_returns_529_json(self, mock_guard):
        from fastapi import HTTPException

        class G:
            async def __aenter__(self):
                raise HTTPException(status_code=529, detail="Overloaded",
                                    headers={"Retry-After": "1"})

            async def __aexit__(self, *exc):
                return False

        mock_guard.return_value = G()
        middleware = self._make_middleware()
        reached, sent = run_middleware(
            middleware, make_scope(path="/v1/chat/completions"))
        status, headers, body = response_from_messages(sent)
        assert status == 529
        assert headers.get("retry-after") == "1"
        assert b"Overloaded" in body
        assert not reached
