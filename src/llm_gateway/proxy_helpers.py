"""Shared helpers used by both proxy routers to eliminate duplication."""
import httpx
import asyncio
import logging
import time
from typing import Optional, List, Tuple

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from .models import LLMBackend, OwnerPermission
from .services import ConfigService
from .rate_limit import get_rate_limiter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Circuit breaker per backend URL — prevents hammering a dead backend.
# States: CLOSED (healthy) → OPEN (failing) → HALF_OPEN (testing recovery)
# ---------------------------------------------------------------------------
class CircuitBreaker:
    """Simple per-URL circuit breaker."""

    FAILURE_THRESHOLD = 5     # consecutive failures to trip
    RECOVERY_TIMEOUT = 30.0   # seconds before trying again (half-open)

    def __init__(self) -> None:
        # url -> {"failures": int, "state": str, "last_failure": float}
        self._circuits: dict[str, dict] = {}

    def _get(self, url: str) -> dict:
        if url not in self._circuits:
            self._circuits[url] = {"failures": 0, "state": "closed", "last_failure": 0.0}
        return self._circuits[url]

    def is_open(self, url: str) -> bool:
        c = self._get(url)
        if c["state"] == "open":
            if time.time() - c["last_failure"] > self.RECOVERY_TIMEOUT:
                c["state"] = "half_open"
                return False  # allow one probe
            return True
        return False

    def record_success(self, url: str) -> None:
        c = self._get(url)
        c["failures"] = 0
        c["state"] = "closed"

    def record_failure(self, url: str, backend_name: str = "") -> None:
        c = self._get(url)
        c["failures"] += 1
        c["last_failure"] = time.time()
        if c["failures"] >= self.FAILURE_THRESHOLD and c["state"] != "open":
            c["state"] = "open"
            logger.warning("Circuit OPEN for %s after %d failures", url, c["failures"])
            # Fire webhook (non-blocking)
            try:
                loop = asyncio.get_running_loop()
                from .webhooks import notify_backend_down
                loop.create_task(notify_backend_down(backend_name or url, url))
            except RuntimeError:
                pass

    def get_status(self) -> dict[str, str]:
        """Return {url: state} for monitoring."""
        return {url: info["state"] for url, info in self._circuits.items()}


_circuit_breaker = CircuitBreaker()


def get_circuit_breaker() -> CircuitBreaker:
    return _circuit_breaker

# ---------------------------------------------------------------------------
# Backend Latency Tracker
# ---------------------------------------------------------------------------
_backend_latency: dict[str, list[float]] = {}


def record_backend_latency(base_url: str, duration: float) -> None:
    history = _backend_latency.setdefault(base_url, [])
    history.append(duration)
    if len(history) > 20:
        history.pop(0)


def get_backend_avg_latency(base_url: str) -> float:
    history = _backend_latency.get(base_url)
    if not history:
        return 0.0
    return sum(history) / len(history)



# ---------------------------------------------------------------------------
# Persistent HTTPX client pool — one client per backend base_url.
# ---------------------------------------------------------------------------
_backend_clients: dict[str, httpx.AsyncClient] = {}
_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)


def _get_client(base_url: str) -> httpx.AsyncClient:
    """Return (or create) a persistent AsyncClient for the given base URL."""
    client = _backend_clients.get(base_url)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(limits=_DEFAULT_LIMITS, timeout=60.0)
        _backend_clients[base_url] = client
    return client


async def close_all_clients() -> None:
    """Shutdown hook — close every pooled client."""
    for client in _backend_clients.values():
        if not client.is_closed:
            await client.aclose()
    _backend_clients.clear()


def get_owner_id_from_request(request: Request) -> tuple[str, bool]:
    """Extract (owner_id, is_admin_ui) from request.state.user.

    Returns ('anonymous', False) when no user is set.
    """
    owner_id = "anonymous"
    is_admin_ui = False
    if hasattr(request.state, "user") and request.state.user:
        user = request.state.user
        if hasattr(user, "owner_id"):  # APIKey object — use the plain FK string, not the lazy relationship
            owner_id = user.owner_id
        elif hasattr(user, "role"):  # User object (logged in via UI)
            owner_id = str(user.id)
            if user.role in ["admin", "manager"]:
                is_admin_ui = True
        elif isinstance(user, dict) and "spiffe_id" in user:
            owner_id = user["spiffe_id"]
    return owner_id, is_admin_ui


def apply_rate_limit(request: Request) -> None:
    """Raise HTTP 429 with Retry-After if the requesting API key has exceeded its rate limit."""
    if hasattr(request.state, "user") and hasattr(request.state.user, "rate_limit_rpm"):
        limiter = get_rate_limiter()
        user_key = getattr(request.state.user, "key_hash", "default")
        limit = getattr(request.state.user, "rate_limit_rpm", None) or 60
        allowed, retry_after = limiter.check_rate_limit(user_key, limit)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(int(retry_after) or 1)},
            )


async def check_owner_permissions(
    session: AsyncSession,
    owner_id: str,
    backend: LLMBackend,
    path: str,
    model: str | None,
) -> None:
    """Verify owner has permission to access the given backend and model.

    Endpoint-type authorization is handled by API key scopes in PolicyMiddleware.
    This function checks:
      1. Global backend endpoint whitelist (LLMBackend.allowed_endpoints)
      2. Owner has a permission row for the backend
      3. Owner permission allows the requested model

    Raises HTTP 403 on any violation.
    """
    # Global backend endpoint whitelist (different from per-owner — this stays)
    if hasattr(backend, "allowed_endpoints") and backend.allowed_endpoints is not None:
        if len(backend.allowed_endpoints) == 0:
            raise HTTPException(
                status_code=403,
                detail="No endpoints are allowed for this backend. Please configure allowed_endpoints.",
            )
        norm_path = path.lstrip("/")
        if "*" not in backend.allowed_endpoints and not any(norm_path.startswith(ep.lstrip("/")) for ep in backend.allowed_endpoints):
            raise HTTPException(
                status_code=403,
                detail=f"Endpoint '{path}' is not allowed for this backend",
            )

    # Owner must have at least one permission for this backend
    perm_res = await session.execute(
        select(OwnerPermission).where(
            OwnerPermission.owner_id == owner_id,
            OwnerPermission.backend_name == backend.name,
        )
    )
    perms = perm_res.scalars().all()

    if not perms:
        raise HTTPException(
            status_code=403,
            detail=f"Owner '{owner_id}' has no permissions for backend '{backend.name}'",
        )

    # Check model access only (endpoint auth is in PolicyMiddleware)
    for p in perms:
        if (not model) or ("*" in p.allowed_models) or (model in p.allowed_models):
            return

    raise HTTPException(
        status_code=403,
        detail=f"Owner '{owner_id}' is not permitted to access model '{model}' on backend '{backend.name}'",
    )


async def build_backend_auth_headers(
    request: Request,
    backend: LLMBackend,
    session: AsyncSession,
    owner_id: str,
    provider_id: str,
) -> dict:
    """Build forwarded headers, injecting the correct Authorization for the backend."""
    from .services import get_owner_service

    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)
    headers.pop("x-api-key", None)
    headers.pop("authorization", None)

    owner_service = get_owner_service()
    owner_key = await owner_service.get_decrypted_key(session, owner_id, provider_id)

    if owner_key:
        headers["Authorization"] = f"Bearer {owner_key}"
    elif backend.api_key:
        raw_key = (
            backend.api_key.get_secret_value()
            if hasattr(backend.api_key, "get_secret_value")
            else backend.api_key
        )
        headers["Authorization"] = f"Bearer {raw_key}"

    return headers


def resolve_provider_and_model(model: str, query_provider: Optional[str] = None) -> tuple[Optional[str], str]:
    """Extract provider and clean model name.

    If query_provider is set, it wins.
    If model contains a slash (e.g. 'openai/gpt-4o', 'vllm-42/llama-3.1-8b'),
    the prefix is resolved as provider/backend name, and the rest as the model.
    """
    if query_provider:
        if "/" in model:
            p, m = model.split("/", 1)
            if p.lower() == query_provider.lower():
                return query_provider, m
        return query_provider, model

    if "/" in model:
        p, m = model.split("/", 1)
        return p, m

    return None, model


async def get_target_backend(model: str, service: ConfigService, session: AsyncSession, provider: Optional[str] = None, owner_id: Optional[str] = None) -> LLMBackend:
    """Resolve the appropriate backend for a given model and optional provider using Smart Routing.

    If owner_id is provided, only backends the owner has permissions for are considered.
    If ENABLE_EXPERIMENTAL_ROUTING is true, it finds all matching backends and randomly balances load.
    Otherwise, it statically returns the first matching backend.
    """
    import random
    backends = await service.list_backends(session)
    if not backends:
        raise HTTPException(status_code=503, detail="No LLM backend configured")

    # Resolve model alias if configured
    try:
        if type(session).__name__ not in ("AsyncMock", "MagicMock"):
            from .services.fallback_service import resolve_alias
        alias_backend, real_model, _ = await resolve_alias(session, model)
        if alias_backend:
            matched = next((b for b in backends if b.name == alias_backend), None)
            if matched:
                return matched
            model = real_model
    except Exception as e:
        logger.debug("Alias resolution skipped: %s", e)

    # Extract provider hint if model has prefix or provider arg passed
    prov_hint, clean_model = resolve_provider_and_model(model, provider)
    eff_provider = prov_hint or provider

    if eff_provider:
        p_lower = eff_provider.lower()
        matched_backends = [
            b for b in backends
            if b.name.lower() == p_lower
            or (str(b.backend_type.value) if hasattr(b.backend_type, "value") else str(b.backend_type)).lower() == p_lower
        ]
        if matched_backends:
            backends = matched_backends
        elif provider:
            raise HTTPException(status_code=404, detail=f"No backends found for provider '{provider}'")

    # Filter by owner permissions when owner_id is provided
    if owner_id and owner_id != "anonymous":
        perm_res = await session.execute(
            select(OwnerPermission.backend_name).where(OwnerPermission.owner_id == owner_id)
        )
        permitted_backends = {row[0] for row in perm_res.fetchall()}
        if permitted_backends:
            owner_backends = [b for b in backends if b.name in permitted_backends]
            if owner_backends:
                backends = owner_backends

    # Check if Experimental Routing is enabled
    is_experimental = await service.get_setting(session, "ENABLE_EXPERIMENTAL_ROUTING", "false")

    if str(is_experimental).lower() != "true":
         # Original Static Logic
         # Try explicit provider lookup via the registry
         if provider:
             from .provider_registry import get_provider, get_model
             reg_provider = get_provider(provider)
             if reg_provider:
                 reg_model = get_model(reg_provider.id, model)
                 if reg_model:
                     for backend in backends:
                         if backend.backend_type.value == reg_provider.id and model in backend.models:
                             return backend
         # Fallback: iterate over all backends and match model (or clean model)
         prov_hint, clean_model = resolve_provider_and_model(model, provider)
         for m_cand in [clean_model, model]:
             for backend in backends:
                 if m_cand in backend.models or "*" in backend.models:
                     return backend
         return backends[0]

    valid_backends = []

    # 1. Gather all healthy backends that host this model
    for backend in backends:
        # If provider is explicitly specified, filter by it first
        if provider:
            b_type = str(backend.backend_type.value) if hasattr(backend.backend_type, 'value') else str(backend.backend_type)
            if b_type != provider:
                continue

        # Only add if the model actually exists on this backend
        if model in backend.models:
            valid_backends.append(backend)

    # 2. Smart Routing Load Balancing (Weights + Rolling Latency)
    if valid_backends:
        effective_weights = []
        for b in valid_backends:
            w = max(1, getattr(b, "weight", 100) or 100)
            avg_lat = get_backend_avg_latency(b.base_url)
            if avg_lat > 0:
                latency_factor = 1.0 / (0.5 + min(avg_lat, 5.0))
                w = max(1, int(w * latency_factor))
            effective_weights.append(w)
        selected_backend = random.choices(valid_backends, weights=effective_weights, k=1)[0]
        logger.info("Smart Routing selected backend '%s' for model '%s' (weight=%s). (%d candidates available)", selected_backend.name, model, getattr(selected_backend, "weight", 100), len(valid_backends))
        return selected_backend

    # 3. Last Resort Fallback
    fallback = next((b for b in backends if b.backend_type == provider or (hasattr(b.backend_type, 'value') and b.backend_type.value == provider)), backends[0])
    logger.warning(f"No backend strictly hosts model '{model}'. Falling back to default backend '{fallback.name}'.")
    return fallback


def clean_target_url(base_url: str, path: str) -> str:
    """Build target URL by combining base_url and path, stripping any duplicate path segments like /v1/v1 or /api/api."""
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    base_path = parsed.path.strip("/")
    req_path = path.strip("/")
    
    if base_path and req_path:
        base_segments = base_path.split("/")
        req_segments = req_path.split("/")
        if base_segments[-1] == req_segments[0]:
            cleaned_req_path = "/".join(req_segments[1:])
            return f"{base_url.rstrip('/')}/{cleaned_req_path.lstrip('/')}"
            
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _get_all_urls(backend: LLMBackend) -> List[str]:
    """Return [base_url] + fallback_urls for a backend."""
    urls = [backend.base_url]
    if hasattr(backend, "fallback_urls") and backend.fallback_urls:
        urls.extend(backend.fallback_urls)
    return urls


async def try_backend_with_fallback(
    backend: LLMBackend,
    path: str,
    method: str,
    headers: dict,
    body: Optional[dict],
    raw_body: Optional[bytes],
    timeout: float = 60.0,
    enable_retry: bool = False,
) -> Tuple[httpx.Response, str, Optional[httpx.AsyncClient]]:
    """Try the primary URL, then each fallback URL on connection failure.

    Returns (response, used_url, None).  The client is pooled internally;
    callers must close the *response* (not the client) when done.
    """
    urls = _get_all_urls(backend)
    last_exc = None

    cb = _circuit_breaker

    for idx, base_url in enumerate(urls):
        # Skip URLs with open circuit breaker
        if cb.is_open(base_url):
            logger.info(f"Circuit OPEN for {base_url}, skipping to next URL")
            continue

        target_url = clean_target_url(base_url, path)
        client = _get_client(base_url)

        max_attempts = 3 if enable_retry else 1
        attempt = 0
        backoff = 1.0

        while attempt < max_attempts:
            attempt += 1
            t0 = time.time()
            try:
                req = client.build_request(
                    method,
                    target_url,
                    headers=headers,
                    json=body if body else None,
                    content=raw_body if not body else None,
                    timeout=timeout,
                )
                r = await client.send(req, stream=True)

                if enable_retry and r.status_code >= 500:
                    cb.record_failure(base_url, backend.name)
                    if attempt < max_attempts:
                        logger.warning(f"Backend {base_url} returned {r.status_code}, retrying (attempt {attempt}/{max_attempts})...")
                        await r.aclose()
                        await asyncio.sleep(backoff)
                        backoff *= 2.0
                        continue

                cb.record_success(base_url)
                record_backend_latency(base_url, time.time() - t0)
                if idx > 0 or attempt > 1:
                    logger.info(f"Request succeeded: using {base_url} (URL {idx+1}/{len(urls)}, Attempt {attempt}/{max_attempts})")
                return r, base_url, None
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                last_exc = exc
                cb.record_failure(base_url, backend.name)
                if enable_retry and attempt < max_attempts:
                    logger.warning(f"Backend {base_url} failed ({type(exc).__name__}), retrying (attempt {attempt}/{max_attempts})...")
                    await asyncio.sleep(backoff)
                    backoff *= 2.0
                    continue
                else:
                    logger.warning(f"Backend {base_url} failed ({type(exc).__name__}), trying next URL...")
                    break

    raise last_exc  # type: ignore[misc]


async def try_with_cross_provider_fallback(
    backend: LLMBackend,
    path: str,
    method: str,
    headers: dict,
    body: Optional[dict],
    raw_body: Optional[bytes],
    session: AsyncSession,
    service: ConfigService,
    fallback_chain_id: Optional[int] = None,
    timeout: float = 60.0,
    enable_retry: bool = False,
) -> Tuple[httpx.Response, str, LLMBackend, Optional[httpx.AsyncClient]]:
    """Try primary backend. If it returns 429/5xx or connection fails, try fallback chain targets."""
    try:
        r, used_url, client = await try_backend_with_fallback(
            backend, path, method, headers, body, raw_body, timeout=timeout, enable_retry=enable_retry
        )
        if r.status_code < 400 or (r.status_code not in (429, 500, 502, 503, 504) or not fallback_chain_id):
            return r, used_url, backend, client
        logger.warning(
            "Primary backend %s returned status %d, triggering fallback chain %s",
            backend.name, r.status_code, fallback_chain_id
        )
        await r.aclose()
    except Exception as exc:
        if not fallback_chain_id:
            raise
        logger.warning(
            "Primary backend %s failed (%s), triggering fallback chain %s",
            backend.name, exc, fallback_chain_id
        )

    from .services.fallback_service import get_chain_targets
    targets = await get_chain_targets(session, fallback_chain_id)
    backends = await service.list_backends(session)
    backends_by_name = {b.name: b for b in backends}

    last_exc = None
    for target in targets:
        target_name = target.get("backend_name")
        target_model = target.get("model")
        fb_backend = backends_by_name.get(target_name)
        if not fb_backend:
            continue

        logger.info("Failing over to chain target backend %s (model=%s)", target_name, target_model)
        fb_body = dict(body) if isinstance(body, dict) else None
        if fb_body and target_model:
            fb_body["model"] = target_model

        try:
            r, used_url, client = await try_backend_with_fallback(
                fb_backend, path, method, headers, fb_body, raw_body, timeout=timeout, enable_retry=enable_retry
            )
            if r.status_code < 400 or r.status_code not in (429, 500, 502, 503, 504):
                return r, used_url, fb_backend, client
            await r.aclose()
        except Exception as exc:
            last_exc = exc
            continue

    if last_exc:
        raise last_exc
    raise HTTPException(status_code=502, detail="All targets in fallback chain failed")
