import os
import secrets
import time
from collections import defaultdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from .services import get_auth_service
from .database import _async_session
import logging

logger = logging.getLogger("uvicorn")

_IS_HTTPS = os.getenv("FORCE_HTTPS", "false").lower() == "true"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject security headers on every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Every front-end library is vendored under /static, so the policy is
        # 'self'-only.  Keeping it strict is what prevents a CDN dependency from
        # silently creeping back in: any re-added <script src="https://..."> is
        # blocked by the browser during development instead of shipping.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; "
            "img-src 'self' data:; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        if _IS_HTTPS:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # Authenticated HTML must never be reusable from the browser cache.
        # Without this, the browser re-displays a fully rendered admin page
        # after the session cookie has expired (or after logout); the page
        # then fires XHRs that the server rejects with 401.
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/html"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"

        return response


class LoginRateLimitMiddleware(BaseHTTPMiddleware):
    """Rate-limit login attempts per IP to prevent brute-force attacks.

    After MAX_ATTEMPTS failed logins within WINDOW_SECONDS, block the IP
    for LOCKOUT_SECONDS.  Only applies to POST /auth/cookie/login and
    POST /auth/jwt/login.
    """

    MAX_ATTEMPTS = 10
    WINDOW_SECONDS = 300   # 5 minutes
    LOCKOUT_SECONDS = 300  # 5 minute lockout

    def __init__(self, app):
        super().__init__(app)
        # IP -> list of failed-attempt timestamps
        self._attempts: dict[str, list[float]] = defaultdict(list)

    def _cleanup(self, ip: str) -> None:
        cutoff = time.time() - self.WINDOW_SECONDS
        self._attempts[ip] = [t for t in self._attempts[ip] if t > cutoff]
        if not self._attempts[ip]:
            del self._attempts[ip]

    async def dispatch(self, request: Request, call_next) -> Response:
        is_login = (
            request.method == "POST"
            and request.url.path in ("/auth/cookie/login", "/auth/jwt/login")
        )
        if not is_login:
            return await call_next(request)

        ip = request.client.host if request.client else "unknown"
        self._cleanup(ip)

        # Check if locked out
        if len(self._attempts.get(ip, [])) >= self.MAX_ATTEMPTS:
            retry_after = self.LOCKOUT_SECONDS
            return Response(
                content="Too many failed login attempts. Try again later.",
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        response = await call_next(request)

        # Record failed attempts (status 400 = bad credentials from FastAPI Users)
        if response.status_code in (400, 401, 422):
            self._attempts[ip].append(time.time())

        return response

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip auth for docs, health, root, and admin
        if request.url.path in ["/", "/health", "/docs", "/openapi.json", "/favicon.ico"] or request.url.path.startswith(("/admin", "/auth", "/static", "/api/v1/bots")):
            return await call_next(request)

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
                return Response(content="Invalid API Key", status_code=401)

            request.state.user = api_key
            request.state.auth_method = "api_key"
            return await call_next(request)

        if cookie_token:
            # UI user authenticated via cookie, defer to router dependencies
            return await call_next(request)

        # Method 2: Check for SPIFFE ID (mTLS/Sidecar)
        # In a real mesh, the sidecar validates the cert and passes the URI in a header
        spiffe_id = request.headers.get("X-Spiffe-ID")
        if spiffe_id:
            # MVP: Trust the header (assuming boundary is secure)
            # In validation phase, we would verify this against a sidecar config or similar.
            request.state.user = {"type": "workload", "spiffe_id": spiffe_id}
            request.state.auth_method = "spiffe"
            return await call_next(request)

        return Response(content="Missing or invalid Authorization (Bearer Token or SPIFFE ID)", status_code=401)

class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF protection for cookie-authenticated requests."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            response = await call_next(request)
            if "csrf_token" not in request.cookies:
                token = secrets.token_urlsafe(32)
                response.set_cookie(
                    "csrf_token", token, httponly=False, samesite="strict",
                    secure=_IS_HTTPS,
                )
            return response

        # Skip CSRF for API key auth (non-cookie auth)
        if request.headers.get("x-api-key") or (
            request.headers.get("authorization", "").startswith("Bearer ")
        ):
            return await call_next(request)

        # Skip CSRF for non-browser paths (proxy endpoints)
        if not request.url.path.startswith(("/admin", "/auth")):
            return await call_next(request)

        cookie_token = request.cookies.get("csrf_token")
        header_token = request.headers.get("x-csrf-token")

        if not cookie_token or cookie_token != header_token:
            return Response(content="CSRF token missing or invalid", status_code=403)

        return await call_next(request)


class PolicyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip for health/admin/docs/auth/static
        if request.url.path in ["/health", "/docs", "/openapi.json", "/favicon.ico"] or request.url.path.startswith(("/admin", "/auth", "/static", "/api/v1/bots")):
             return await call_next(request)

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
            return Response(content="Forbidden by Policy", status_code=403)

        return await call_next(request)


class BackpressureMiddleware(BaseHTTPMiddleware):
    """Adaptive in-flight inference limiter shedding excess concurrency with HTTP 529."""

    async def dispatch(self, request: Request, call_next) -> Response:
        from .services.backpressure_service import is_inference_path, backpressure_guard
        from fastapi import HTTPException
        from fastapi.responses import JSONResponse

        if is_inference_path(request.url.path):
            try:
                async with backpressure_guard():
                    return await call_next(request)
            except HTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"error": exc.detail},
                    headers=dict(exc.headers or {}),
                )
        return await call_next(request)
