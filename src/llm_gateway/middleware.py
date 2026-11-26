import os
import secrets
import time
from collections import defaultdict
from http.cookies import SimpleCookie

from fastapi import Request, Response
from starlette.datastructures import MutableHeaders
from .services import get_auth_service
from .database import _async_session
from . import config
import logging

logger = logging.getLogger("uvicorn")

# ALLOW_INSECURE_HTTP (dev mode) overrides FORCE_HTTPS: no HSTS, no Secure cookie.
_IS_HTTPS = (
    os.getenv("FORCE_HTTPS", "false").lower() == "true"
    and not config.ALLOW_INSECURE_HTTP
)


def _set_cookie_header(name: str, value: str, *, secure: bool) -> bytes:
    """Build a raw Set-Cookie header byte-compatible with

    ``Response.set_cookie(name, value, httponly=False, samesite="strict", secure=...)``.

    Starlette formats cookies via ``http.cookies.SimpleCookie``; we mirror that
    exactly (``Path=/; SameSite=strict``, ``; Secure`` only when secure, and no
    HttpOnly attribute since it is False here).
    """
    cookie = SimpleCookie()
    cookie[name] = value
    cookie[name]["path"] = "/"
    cookie[name]["samesite"] = "strict"
    if secure:
        cookie[name]["secure"] = True
    return cookie.output(header="").strip().encode("latin-1")


class SecurityHeadersMiddleware:
    """Inject security headers on every response.

    Pure ASGI: intercepts the ``http.response.start`` message and mutates its
    raw headers in place, so responses (including streamed ones) are forwarded
    unbuffered.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                # Every front-end library is vendored under /static, so the policy is
                # 'self'-only.  Keeping it strict is what prevents a CDN dependency from
                # silently creeping back in: any re-added <script src="https://..."> is
                # blocked by the browser during development instead of shipping.
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'; "
                    "font-src 'self' data:; "
                    "img-src 'self' data:; "
                    "connect-src 'self' ws: wss:; "
                    "frame-ancestors 'none'; "
                    "base-uri 'self'; "
                    "form-action 'self'"
                )
                if _IS_HTTPS:
                    headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

                # Authenticated HTML must never be reusable from the browser cache.
                # Without this, the browser re-displays a fully rendered admin page
                # after the session cookie has expired (or after logout); the page
                # then fires XHRs that the server rejects with 401.
                content_type = headers.get("content-type", "")
                if content_type.startswith("text/html"):
                    headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
                    headers["Pragma"] = "no-cache"
                    headers["Expires"] = "0"
            await send(message)

        await self.app(scope, receive, send_wrapper)


class LoginRateLimitMiddleware:
    """Rate-limit login attempts per IP to prevent brute-force attacks.

    After MAX_ATTEMPTS failed logins within WINDOW_SECONDS, block the IP
    for LOCKOUT_SECONDS.  Only applies to the login endpoints (cookie, remember-me
    cookie and JWT).

    Pure ASGI: on the gated paths it only inspects the status code in the
    ``http.response.start`` message - the body is forwarded untouched.
    """

    MAX_ATTEMPTS = 10
    WINDOW_SECONDS = 300   # 5 minutes
    LOCKOUT_SECONDS = 300  # 5 minute lockout

    def __init__(self, app):
        self.app = app
        # IP -> list of failed-attempt timestamps
        self._attempts: dict[str, list[float]] = defaultdict(list)

    def _cleanup(self, ip: str) -> None:
        cutoff = time.time() - self.WINDOW_SECONDS
        self._attempts[ip] = [t for t in self._attempts[ip] if t > cutoff]
        if not self._attempts[ip]:
            del self._attempts[ip]

    async def dispatch(self, scope, receive, send):
        method = scope["method"]
        path = scope["path"]
        is_login = (
            method == "POST"
            and path in ("/auth/cookie/login", "/auth/cookie-remember/login", "/auth/jwt/login")
        )
        if not is_login:
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        ip = client[0] if client else "unknown"
        self._cleanup(ip)

        # Check if locked out
        if len(self._attempts.get(ip, [])) >= self.MAX_ATTEMPTS:
            retry_after = self.LOCKOUT_SECONDS
            response = Response(
                content="Too many failed login attempts. Try again later.",
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        status_holder = {}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        await self.app(scope, receive, send_wrapper)

        # Record failed attempts (status 400 = bad credentials from FastAPI Users)
        if status_holder.get("status") in (400, 401, 422):
            self._attempts[ip].append(time.time())

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        await self.dispatch(scope, receive, send)


class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # ``Request`` is a thin view over the scope - constructing one does not
        # buffer anything. State assigned below lands in scope["state"], so
        # downstream routers see request.state.user exactly as before.
        request = Request(scope)

        # Skip auth for docs, health, root, and admin
        if request.url.path in ["/", "/health", "/metrics", "/docs", "/openapi.json", "/favicon.ico"] or request.url.path.startswith(("/admin", "/auth", "/static", "/api/v1/bots", "/api/client-snippets")):
            await self.app(scope, receive, send)
            return

        auth_header = request.headers.get("Authorization")
        api_key_header = request.headers.get("x-api-key")
        cookie_token = request.cookies.get("fastapiusersauth")

        token = None
        if api_key_header:
            token = api_key_header
        elif auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

        if token:
            auth_service = get_auth_service()
            async with _async_session() as session:
                api_key = await auth_service.validate_key(session, token)

            if not api_key:
                response = Response(content="Invalid API Key", status_code=401)
                await response(scope, receive, send)
                return

            request.state.user = api_key
            request.state.auth_method = "api_key"
            await self.app(scope, receive, send)
            return

        if cookie_token:
            # UI user authenticated via cookie, defer to router dependencies
            await self.app(scope, receive, send)
            return

        # Method 2: Check for SPIFFE ID (mTLS/Sidecar).
        # A raw X-Spiffe-ID header is spoofable, so it is IGNORED unless
        # SPIFFE_TRUSTED_HEADER is explicitly enabled (service-mesh deployments
        # where a trusted sidecar overwrites the header on every request).
        # Even then, the sidecar must present the shared secret
        # X-Spiffe-Token == SPIFFE_SIDECAR_TOKEN; fail closed if unset.
        spiffe_id = request.headers.get("X-Spiffe-ID")
        if spiffe_id and config.SPIFFE_TRUSTED_HEADER:
            if not config.SPIFFE_SIDECAR_TOKEN:
                logger.error(
                    "SPIFFE_TRUSTED_HEADER is enabled but SPIFFE_SIDECAR_TOKEN is "
                    "unset; refusing SPIFFE authentication (fail closed)."
                )
            elif request.headers.get("X-Spiffe-Token") == config.SPIFFE_SIDECAR_TOKEN:
                request.state.user = {"type": "workload", "spiffe_id": spiffe_id}
                request.state.auth_method = "spiffe"
                await self.app(scope, receive, send)
                return

        response = Response(content="Missing or invalid Authorization (Bearer Token or SPIFFE ID)", status_code=401)
        await response(scope, receive, send)


class CSRFMiddleware:
    """Double-submit cookie CSRF protection for cookie-authenticated requests.

    Pure ASGI. For safe methods the response is passed through untouched; when
    the csrf_token cookie is absent we append a raw ``set-cookie`` header to the
    ``http.response.start`` message, formatted exactly like the previous
    ``Response.set_cookie("csrf_token", token, httponly=False,
    samesite="strict", secure=_IS_HTTPS)``.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)

        if request.method in ("GET", "HEAD", "OPTIONS"):
            if "csrf_token" in request.cookies:
                await self.app(scope, receive, send)
                return

            token = secrets.token_urlsafe(32)
            cookie_header = _set_cookie_header("csrf_token", token, secure=_IS_HTTPS)

            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    message = dict(message)
                    message["headers"] = list(message.get("headers") or [])
                    message["headers"].append((b"set-cookie", cookie_header))
                await send(message)

            await self.app(scope, receive, send_wrapper)
            return

        # Skip CSRF for API key auth (non-cookie auth)
        if request.headers.get("x-api-key") or (
            request.headers.get("authorization", "").startswith("Bearer ")
        ):
            await self.app(scope, receive, send)
            return

        # Skip CSRF for non-browser paths (proxy endpoints)
        if not request.url.path.startswith(("/admin", "/auth")):
            await self.app(scope, receive, send)
            return

        cookie_token = request.cookies.get("csrf_token")
        header_token = request.headers.get("x-csrf-token")

        if not cookie_token or cookie_token != header_token:
            response = Response(content="CSRF token missing or invalid", status_code=403)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class PolicyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)

        # Skip for health/admin/docs/auth/static
        if request.url.path in ["/health", "/metrics", "/docs", "/openapi.json", "/favicon.ico"] or request.url.path.startswith(("/admin", "/auth", "/static", "/api/v1/bots")):
            await self.app(scope, receive, send)
            return

        from .policy import get_policy_engine, PolicyRequest

        policy_engine = get_policy_engine()

        # Build Policy Request
        policy_req = PolicyRequest(
            user=getattr(request.state, "user", {}),
            action="call_llm",
            resource=request.url.path,
            context={
                "method": request.method,
                "remote": request.client.host if request.client else "unknown"
            }
        )

        # Pass a DB session so the engine can load configurable SCOPE_RULES.
        # If the session fails, the engine falls back to built-in defaults.
        session = None
        try:
            async with _async_session() as session:
                allowed = await policy_engine.evaluate(policy_req, session=session)
        except Exception:
            allowed = await policy_engine.evaluate(policy_req, session=None)

        from .audit import get_audit_logger
        audit = get_audit_logger()
        audit.log(
            event_type="policy_eval",
            user=policy_req.user,
            resource=policy_req.resource,
            decision="allow" if allowed else "deny",
            metadata=policy_req.context,
            ip_address=request.client.host if request.client else None,
        )

        if not allowed:
            response = Response(content="Forbidden by Policy", status_code=403)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class BackpressureMiddleware:
    """Adaptive in-flight inference limiter shedding excess concurrency with HTTP 529.

    Note: in pure ASGI the guard slot is held until the inner app call fully
    completes (including streamed body transmission), whereas the previous
    BaseHTTPMiddleware released it when the response object was returned. This
    slightly tightens in-flight accounting for streamed inference responses,
    which is the stricter (safer) direction for overload shedding.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from .services.backpressure_service import is_inference_path, backpressure_guard
        from fastapi import HTTPException
        from fastapi.responses import JSONResponse

        path = scope.get("path", "")
        if is_inference_path(path):
            try:
                async with backpressure_guard():
                    await self.app(scope, receive, send)
            except HTTPException as exc:
                response = JSONResponse(
                    status_code=exc.status_code,
                    content={"error": exc.detail},
                    headers=dict(exc.headers or {}),
                )
                await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
