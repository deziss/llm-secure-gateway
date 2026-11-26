"""Shared helpers used by both proxy routers to eliminate duplication."""
import json
import httpx
import asyncio
import logging
import time
import os
import random
from typing import Optional, List, Tuple, Dict, Any

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from .models import LLMBackend, OwnerPermission
from .services import ConfigService
from .rate_limit import get_rate_limiter
from .observability.collect.requests import get_live_stats

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Circuit breaker per backend URL — prevents hammering a dead backend.
# States: CLOSED (healthy) → OPEN (failing) → HALF_OPEN (testing recovery)
# ---------------------------------------------------------------------------
class CircuitBreaker:
    """Simple per-URL circuit breaker."""

    def __init__(self) -> None:
        # Configurable via env so operators can tune tripping without a code change.
        self.FAILURE_THRESHOLD = int(os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5"))
        self.RECOVERY_TIMEOUT = float(os.getenv("CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "30"))
        # url -> {"failures": int, "state": str, "last_failure": float}
        self._circuits: dict[str, dict] = {}
        # Guards the half-open state: only ONE concurrent probe may pass per URL.
        # Single event loop, so plain dict flags are sufficient.
        self._probe_in_flight: dict[str, bool] = {}
        self._probe_started: dict[str, float] = {}

    def _get(self, url: str) -> dict:
        if url not in self._circuits:
            self._circuits[url] = {"failures": 0, "state": "closed", "last_failure": 0.0}
        return self._circuits[url]

    def _clear_probe(self, url: str) -> None:
        self._probe_in_flight.pop(url, None)
        self._probe_started.pop(url, None)

    def release_probe(self, url: str) -> None:
        """Release a half-open probe slot without changing circuit state.

        For callers that open a probe via is_open() but report outcomes through
        other channels (e.g. the observability poller); the staleness timeout
        also covers callers that never call this.
        """
        self._clear_probe(url)

    def is_open(self, url: str) -> bool:
        c = self._get(url)
        if c["state"] == "open":
            if time.time() - c["last_failure"] > self.RECOVERY_TIMEOUT:
                c["state"] = "half_open"
            else:
                return True
        if c["state"] == "half_open":
            # Allow only a single concurrent probe through; everyone else still
            # sees the circuit as open (prevents half-open stampede).
            # A probe older than RECOVERY_TIMEOUT is considered abandoned so the
            # flag can never deadlock (record_failure/success also clear it).
            started = self._probe_started.get(url)
            probe_held = bool(self._probe_in_flight.get(url))
            if (
                probe_held
                and started is not None
                and time.time() - started > self.RECOVERY_TIMEOUT
            ):
                self._clear_probe(url)  # stale probe — release and re-admit
                probe_held = False
            if probe_held:
                return True
            self._probe_in_flight[url] = True
            self._probe_started[url] = time.time()
            return False
        return False

    def record_success(self, url: str) -> None:
        c = self._get(url)
        c["failures"] = 0
        c["state"] = "closed"
        self._clear_probe(url)

    def record_failure(self, url: str, backend_name: str = "") -> None:
        c = self._get(url)
        was_half_open = c["state"] == "half_open"
        c["failures"] += 1
        c["last_failure"] = time.time()
        if was_half_open:
            # Failed probe: re-open immediately (even below threshold) so the
            # backend gets another quiet recovery window, and free the probe slot.
            c["state"] = "open"
            self._clear_probe(url)
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

    def get_failure_counts(self) -> dict[str, int]:
        """Return {url: consecutive failure count} for monitoring dashboards."""
        return {url: info["failures"] for url, info in self._circuits.items()}


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


def get_backend_in_flight(
    store: Any,
    backend_name: str,
    base_url: str = "",
    fallback_urls: Optional[List[str]] = None,
) -> int:
    """Calculate active in-flight request count (waiting + generating) for a backend."""
    if store is None:
        return 0
    count = 0
    targets = {backend_name}
    if base_url:
        targets.add(base_url)
        targets.add(base_url.rstrip("/"))
    if fallback_urls:
        for u in fallback_urls:
            if u:
                targets.add(u)
                targets.add(u.rstrip("/"))

    # Check store._keys if LiveStats
    keys_dict = getattr(store, "_keys", None)
    if isinstance(keys_dict, dict):
        lock = getattr(store, "_lock", None)
        try:
            if lock:
                with lock:
                    items = list(keys_dict.items())
            else:
                items = list(keys_dict.items())
            for (srv, _stype, _mod), state in items:
                if srv in targets:
                    count += getattr(state, "waiting", 0) + getattr(state, "generating", 0)
            return count
        except Exception:
            pass

    # Check store.snapshot() if available (mock or custom store)
    if hasattr(store, "snapshot") and callable(store.snapshot):
        try:
            snap = store.snapshot()
            if isinstance(snap, list):
                for row in snap:
                    if isinstance(row, dict) and row.get("server") in targets:
                        count += int(row.get("waiting", 0)) + int(row.get("generating", 0))
                return count
        except Exception:
            pass

    return count



# ---------------------------------------------------------------------------
# Persistent HTTPX client pool — one client per backend base_url.
# ---------------------------------------------------------------------------
_backend_clients: dict[str, httpx.AsyncClient] = {}
# keepalive_expiry MUST be shorter than the idle keep-alive timeout of the upstreams
# we proxy to. vLLM/uvicorn closes idle connections after VLLM_HTTP_TIMEOUT_KEEP_ALIVE
# (5s by default); if we keep pooled sockets longer than that, concurrent requests pick
# server-closed sockets from the pool and die with empty httpx.ReadError / connection
# reset before headers. 4s guarantees the client always retires idle sockets first.
_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=4.0)

DEFAULT_UPSTREAM_TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT", "120.0"))


def _get_client(base_url: str) -> httpx.AsyncClient:
    """Return (or create) a persistent AsyncClient for the given base URL."""
    client = _backend_clients.get(base_url)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(limits=_DEFAULT_LIMITS, timeout=DEFAULT_UPSTREAM_TIMEOUT)
        _backend_clients[base_url] = client
    return client


async def _reset_client(base_url: str) -> None:
    """Evict and cleanly close a pooled client (e.g. after transport or connection drop)."""
    client = _backend_clients.pop(base_url, None)
    if client is not None and not client.is_closed:
        try:
            await client.aclose()
        except Exception:
            pass


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


async def apply_rate_limit(request: Request) -> None:
    """Raise HTTP 429 with Retry-After if the requesting API key has exceeded its rate limit.

    Checks:
    1. Requests-Per-Minute (RPM) limit
    2. Tokens-Per-Minute (TPM) limit
    3. Max in-flight concurrency limit
    """
    if not hasattr(request.state, "user") or not request.state.user:
        return

    user = request.state.user
    user_key = getattr(user, "key_hash", "default")
    limiter = get_rate_limiter()

    # 1. RPM Rate Limit
    if hasattr(user, "rate_limit_rpm"):
        limit_rpm = getattr(user, "rate_limit_rpm", None)
        rpm_val = 60 if limit_rpm is None else limit_rpm
        if rpm_val > 0:
            allowed, retry_after = await limiter.check_rate_limit_async(user_key, rpm_val)
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded",
                    headers={"Retry-After": str(int(retry_after) or 1)},
                )

    # 2. TPM Rate Limit
    limit_tpm = getattr(user, "rate_limit_tpm", None)
    if isinstance(limit_tpm, int) and limit_tpm > 0:
        allowed, retry_after = await limiter.check_token_limit_async(user_key, limit_tpm)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(int(retry_after) or 1)},
            )

    # 3. Concurrency Limit
    limit_concurrency = getattr(user, "max_concurrency", None)
    if isinstance(limit_concurrency, int) and limit_concurrency > 0:
        acquired, _ = await limiter.acquire_concurrency_async(user_key, limit_concurrency)
        if not acquired:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={"Retry-After": "1"},
            )
        request.state.concurrency_acquired = True
        request.state.concurrency_key = user_key


async def release_rate_limit_concurrency(request: Request) -> None:
    """Safely release concurrency slot if acquired during apply_rate_limit."""
    if getattr(request.state, "concurrency_acquired", False):
        user_key = getattr(request.state, "concurrency_key", None)
        request.state.concurrency_acquired = False
        if user_key:
            limiter = get_rate_limiter()
            try:
                await limiter.release_concurrency_async(user_key)
            except Exception as e:
                logger.warning("Failed to release concurrency for key %s: %s", user_key, e)


def _canonical_endpoint(path: str) -> str:
    """Normalise an endpoint path for allowed_endpoints matching.

    allowed_endpoints names API operations, not URL versions, and entries are
    stored both ways: relative to a base URL that already ends in /v1
    ("/chat/completions") and with the prefix ("/v1/chat/completions"). The
    request path always arrives as "v1/chat/completions". Comparing them
    literally rejected every request to backends registered the first way, so
    strip the leading slash and a leading "v1/" from both sides.
    """
    p = (path or "").strip().lstrip("/")
    if p == "v1":
        return ""
    return p[3:] if p.startswith("v1/") else p


def _endpoint_matches(path: str, entry: str) -> bool:
    """True if `path` is `entry` or nested under it (on a segment boundary)."""
    if not entry:
        return True
    return path == entry or path.startswith(entry.rstrip("/") + "/")


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
                detail={
                    "error": {
                        "code": "endpoint_not_allowed",
                        "message": "No endpoints are allowed for this backend. Please configure allowed_endpoints.",
                        "param": "endpoint",
                    }
                },
            )
        norm_path = _canonical_endpoint(path)
        if "*" not in backend.allowed_endpoints and not any(
            _endpoint_matches(norm_path, _canonical_endpoint(ep)) for ep in backend.allowed_endpoints
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": "endpoint_not_allowed",
                        "message": f"Endpoint '{path}' is not allowed for this backend",
                        "param": "endpoint",
                    }
                },
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
            detail={
                "error": {
                    "code": "backend_not_allowed",
                    "message": f"Owner '{owner_id}' has no permissions for backend '{backend.name}'",
                    "param": "backend",
                }
            },
        )

    # Check model access only (endpoint auth is in PolicyMiddleware)
    global_allowed = any(
        (not model) or ("*" in p.allowed_models) or (model in p.allowed_models)
        for p in perms
    )
    if not global_allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "model_not_allowed",
                    "message": f"Owner '{owner_id}' is not permitted to access model '{model}' on backend '{backend.name}'",
                    "param": "model",
                }
            },
        )

    # Per-owner policy profile (denied_models / allowed_models overrides).
    # get_profile is TTL-cached (60s, negatives included), so this is a DB hit
    # at most once per owner per minute on the hot path. No profile -> allow.
    if model:
        from .services import policy_profiles

        profile = await policy_profiles.get_profile(session, owner_id)
        profile_ok, profile_reason = policy_profiles.evaluate_model_access(
            profile, model
        )
        if not profile_ok:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": "model_not_allowed_by_policy_profile",
                        "message": (
                            f"Model '{model}' is not allowed by the policy "
                            f"profile for owner '{owner_id}' ({profile_reason})"
                        ),
                        "param": model,
                    }
                },
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
    # Strip hop-by-hop headers per RFC 7230 / RFC 9110 to protect connection pooling
    for hop_header in (
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    ):
        headers.pop(hop_header, None)
    # Ask for an uncompressed body: thinking normalization and protocol
    # translation read it as text. (Raw passthrough would otherwise forward
    # gzip bytes under a header we may have to drop.)
    headers["accept-encoding"] = "identity"
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
    If model contains a slash (e.g. 'openai/gpt-4o', 'vllm-node1/llama-3.1-8b'),
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


async def get_target_backend(
    model: str,
    service: ConfigService,
    session: AsyncSession,
    provider: Optional[str] = None,
    owner_id: Optional[str] = None,
    strategy: Optional[str] = None,
    messages: Optional[List[Dict[str, Any]]] = None,
) -> LLMBackend:
    """Resolve the appropriate backend for a given model and optional provider using Smart Routing.

    If owner_id is provided, only backends the owner has permissions for are considered.
    If ENABLE_EXPERIMENTAL_ROUTING is true or strategy is configured, it dynamically balances load
    using least-busy (queue-aware) or configured routing.
    Otherwise, it statically returns the first matching backend.
    """
    import random
    backends = await service.list_backends(session)
    if not backends:
        raise HTTPException(status_code=503, detail="No LLM backend configured")

    # Dynamic Complexity-based routing for virtual auto models
    if model and model.lower() in ("auto", "smart-route", "auto-router"):
        from .routing.complexity import classify_complexity, resolve_complexity_model
        tier = classify_complexity(messages)
        model = resolve_complexity_model(tier)
        logger.info("Complexity router assigned model '%s' (Tier: %s)", model, tier.value)

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

    # Check routing strategy settings
    try:
        is_experimental = await service.get_setting(session, "ENABLE_EXPERIMENTAL_ROUTING", os.getenv("ENABLE_EXPERIMENTAL_ROUTING", "false"))
    except Exception:
        is_experimental = os.getenv("ENABLE_EXPERIMENTAL_ROUTING", "false")

    try:
        routing_strategy_setting = await service.get_setting(session, "ROUTING_STRATEGY", os.getenv("ROUTING_STRATEGY", ""))
    except Exception:
        routing_strategy_setting = os.getenv("ROUTING_STRATEGY", "")

    if isinstance(is_experimental, str):
        is_exp_enabled = is_experimental.strip().lower() in ("true", "1")
    else:
        is_exp_enabled = is_experimental is True

    strat_val = strategy if isinstance(strategy, str) else ""
    setting_val = routing_strategy_setting if isinstance(routing_strategy_setting, str) else ""
    eff_strategy = (strat_val or setting_val).strip().lower()

    use_dynamic = is_exp_enabled or (eff_strategy in ("least_busy", "least-busy", "queue_aware", "queue-aware", "random", "weighted_random", "gpu_aware", "gpu-aware", "infra_aware", "infra-aware", "cost_aware", "cost-aware"))

    if not use_dynamic:
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
            if b_type.lower() != provider.lower() and backend.name.lower() != provider.lower():
                continue

        # Check if model exists on this backend (support exact model, clean model, or wildcard)
        b_models = backend.models or []
        if model in b_models or clean_model in b_models or "*" in b_models:
            valid_backends.append(backend)

    if not valid_backends:
        # 3. Last Resort Fallback
        fallback = next((b for b in backends if b.backend_type == provider or (hasattr(b.backend_type, 'value') and b.backend_type.value == provider)), backends[0])
        logger.warning(f"No backend strictly hosts model '{model}'. Falling back to default backend '{fallback.name}'.")
        return fallback

    # Check if explicit random/weighted_random was chosen
    if eff_strategy in ("random", "weighted_random"):
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

    # 2b. Cost-aware routing: cheapest healthy replica for this model, with a
    # latency/queue tiebreaker. Pricing comes from model_pricing (TTL cache, local
    # engines count free). Falls back gracefully when nothing is priced.
    if eff_strategy in ("cost_aware", "cost-aware"):
        from .routing.cost_aware import get_pricing_map, select_cost_aware

        try:
            from .observability.collect.poller import get_snapshot
            snapshot = get_snapshot()
        except Exception:
            snapshot = {}
        statuses = {s.get("name"): s.get("status") for s in (snapshot.get("servers") or [])}

        pricing_map = await get_pricing_map(session)

        # Rough cost estimate: chars/4 prompt tokens; output defaults to 512.
        prompt_chars = 0
        est_out = 512.0
        if isinstance(messages, list):
            for m in messages:
                c = m.get("content") if isinstance(m, dict) else None
                if isinstance(c, str):
                    prompt_chars += len(c)
                elif isinstance(c, list):  # multimodal content parts
                    prompt_chars += sum(len(p.get("text", "")) for p in c if isinstance(p, dict))
        est_in = max(1.0, prompt_chars / 4.0)

        def _lat(b):
            return get_backend_avg_latency(b.base_url)

        def _inflight(b):
            return get_backend_in_flight(get_live_stats(), b.name, b.base_url, getattr(b, "fallback_urls", None))

        selected, ranked = select_cost_aware(
            valid_backends, clean_model, pricing_map, _lat, _inflight, est_in, est_out, statuses)
        if ranked and ranked[0].estimated_cost_usd is None and len(ranked) > 1:
            # Nothing priced: cost routing has no signal — defer to capacity routing.
            from .routing.gpu_aware import select_gpu_aware
            selected, _ = select_gpu_aware(valid_backends, clean_model, snapshot, _lat, _inflight)
            logger.info("Cost-aware routing had no pricing for '%s'; fell back to capacity scoring -> '%s'",
                        model, selected.name)
        else:
            logger.info(
                "Cost-aware routing selected backend '%s' for model '%s' (est_cost=$%s; %s); candidates=%s",
                selected.name, model,
                ranked[0].estimated_cost_usd, ranked[0].reasons,
                [(c.backend.name, c.estimated_cost_usd, c.score) for c in ranked[:5]],
            )
        return selected

    # 2a. GPU/infrastructure-aware routing: rank candidates by live serving capacity
    # (KV cache, queue, VRAM headroom, latency, health) using the observability poller.
    if eff_strategy in ("gpu_aware", "gpu-aware", "infra_aware", "infra-aware"):
        from .routing.gpu_aware import select_gpu_aware
        try:
            from .observability.collect.poller import get_snapshot
            snapshot = get_snapshot()
        except Exception:  # poller unavailable -> neutral scores, still ranks by latency/health
            snapshot = {}

        def _lat(b):
            return get_backend_avg_latency(b.base_url)

        def _inflight(b):
            return get_backend_in_flight(get_live_stats(), b.name, b.base_url, getattr(b, "fallback_urls", None))

        selected, ranked = select_gpu_aware(valid_backends, clean_model, snapshot, _lat, _inflight)
        top = ranked[0].reasons
        logger.info(
            "GPU-aware routing selected backend '%s' for model '%s' (score=%.3f; %s); candidates=%s",
            selected.name, model, ranked[0].score, top,
            [(c.backend.name, c.score) for c in ranked[:5]],
        )
        return selected

    # 2b. Least-Busy (Queue-Aware) Dynamic Load Balancing
    store = get_live_stats()

    candidate_evals = []
    for idx, b in enumerate(valid_backends):
        inflight = get_backend_in_flight(store, b.name, b.base_url, getattr(b, "fallback_urls", None))
        avg_lat = max(0.0, float(get_backend_avg_latency(b.base_url)))
        weight = max(1, int(getattr(b, "weight", 100) or 100))

        # Latency factor penalizes higher latency (lower factor -> higher load score)
        latency_factor = max(0.2, 1.0 / (1.0 + avg_lat))
        score = (inflight + 1.0) / (weight * latency_factor)

        # Tie-breaker key:
        # 1. Lower score is better
        # 2. If tied, lower latency is better
        # 3. If tied, higher weight is better (-weight)
        # 4. Stable order (idx)
        candidate_evals.append((
            round(score, 8),
            round(avg_lat, 6),
            -weight,
            idx,
            b,
            inflight,
            weight,
            avg_lat,
        ))

    best = min(candidate_evals, key=lambda x: (x[0], x[1], x[2], x[3]))
    selected = best[4]
    selected_inflight = best[5]
    selected_weight = best[6]
    selected_avg_lat = best[7]

    logger.info(
        "Least-busy routing selected backend '%s' (in_flight=%d, weight=%d, avg_lat=%.3fs) for model '%s'",
        selected.name,
        selected_inflight,
        selected_weight,
        selected_avg_lat,
        model,
    )
    return selected


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
    """Return [base_url] + fallback_urls for a backend, filtering empty strings."""
    urls = []
    if getattr(backend, "base_url", None):
        urls.append(backend.base_url)
    if hasattr(backend, "fallback_urls") and backend.fallback_urls:
        urls.extend([u for u in backend.fallback_urls if u])
    return urls


class UpstreamConnectionReset(httpx.ReadError):
    """Raised when every transport attempt fails before response headers arrived.

    A bare ``httpx.ReadError('')`` with no message reaches the client as an
    opaque 502. This wrapper keeps the original exception accessible and names
    the most common real-world causes -- an intermediary WAF/middlebox
    rejecting hostile-looking payload content, the upstream closing a stale
    keep-alive socket, or the upstream dying mid-attempt -- so operators get an
    actionable message from the trace alone. Subclasses ``httpx.ReadError`` so
    every existing handler (``except httpx.ReadError`` / ``RequestError``) and
    test contract keeps working unchanged.
    """

    def __init__(self, base_url: str, original: BaseException) -> None:
        self.base_url = base_url
        self.original = original
        super().__init__(
            "Upstream connection reset before response headers were received "
            f"from {base_url} ({type(original).__name__}). The upstream edge "
            "(load balancer / WAF) may have rejected the request content, "
            "closed a stale keep-alive socket, or restarted mid-request."
        )


def serialize_json_body(data: Any) -> bytes:
    """Serialize dict to JSON bytes with ensure_ascii=True and markup escaping.

    Guarantees all non-ASCII characters (em-dashes, smart quotes, emojis, etc.)
    are safely escaped as ASCII \\uXXXX sequences. This prevents upstream LLM servers
    (specifically vLLM and llama.cpp HTTP engines) from abruptly terminating the
    connection with ConnectionResetError / httpx.ReadError on raw multibyte UTF-8.

    ``<``, ``>`` and ``&`` are additionally escaped as \\u003c / \\u003e / \\u0026.
    This is lossless -- every JSON parser decodes them back to the original
    characters, so the model receives the exact same string -- but the wire
    bytes no longer look like HTML/XSS markup. WAFs and API-gateway middleboxes
    in front of backends (e.g. ModSecurity rejecting '<script>alert(1)</script>'
    or '<svg onload=...>') otherwise RST the connection before sending any
    response, which surfaces as an opaque empty httpx.ReadError and a 502.
    """
    text = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return text.encode("ascii")


def _retry_sleep(backoff: float) -> float:
    """Equal-jitter retry delay: a random sleep in [backoff/2, backoff].

    Without jitter, all concurrent requests that failed at the same instant wake
    and retry at exactly the same time, stampeding a backend that just blipped
    (retry storm). Jitter spreads retries so the backend recovers smoothly.
    """
    return random.uniform(backoff / 2.0, backoff)


MAX_RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF_MAX", "8.0"))


async def try_backend_with_fallback(
    backend: LLMBackend,
    path: str,
    method: str,
    headers: dict,
    body: Optional[dict],
    raw_body: Optional[bytes],
    timeout: float = DEFAULT_UPSTREAM_TIMEOUT,
    enable_retry: bool = False,
) -> Tuple[httpx.Response, str, Optional[httpx.AsyncClient]]:
    """Try the primary URL, then each fallback URL on connection failure.

    Returns (response, used_url, None).  The client is pooled internally;
    callers must close the *response* (not the client) when done.
    """
    urls = _get_all_urls(backend)
    last_exc = None
    body = normalize_backend_payload(body, backend_type_name(backend))

    cb = _circuit_breaker

    # Filter out URLs whose circuit is open. If ALL URLs are open, allow the primary
    # URL to act as a probe (half-open fallback) rather than failing immediately without trying.
    # Exception: if the primary is half-open with its single probe slot already in
    # flight, don't stampede — reject this request instead.
    candidate_urls = [u for u in urls if not cb.is_open(u)]
    if not candidate_urls and urls:
        primary = cb._get(urls[0])
        if primary["state"] == "half_open" and cb._probe_in_flight.get(urls[0]):
            raise httpx.ConnectError(
                f"Circuit breaker open for all URLs of backend '{backend.name}' "
                f"(configured URLs: {urls}); half-open probe already in flight"
            )
        logger.warning(
            "Circuit breaker OPEN for all URLs of backend '%s'; forcing half-open probe on %s",
            backend.name,
            urls[0],
        )
        candidate_urls = [urls[0]]

    # Last terminal 5xx response kept open (streamed, unread) so it can be
    # returned to the client once all URLs/attempts are exhausted.
    pending_5xx: Optional[httpx.Response] = None
    pending_5xx_url: Optional[str] = None

    for idx, base_url in enumerate(candidate_urls):

        target_url = clean_target_url(base_url, path)

        # Allow at least 2 attempts so transient drops on stale pooled sockets recover immediately
        max_attempts = 3 if enable_retry else 2
        attempt = 0
        backoff = 1.0

        forward_headers = dict(headers)
        forward_content = None
        if body is not None:
            forward_content = serialize_json_body(body)
            forward_headers["content-type"] = "application/json"
            forward_headers["content-length"] = str(len(forward_content))
        elif raw_body is not None:
            forward_content = raw_body

        while attempt < max_attempts:
            attempt += 1
            client = _get_client(base_url)
            t0 = time.time()
            try:
                req = client.build_request(
                    method,
                    target_url,
                    headers=forward_headers,
                    content=forward_content,
                    timeout=timeout,
                )
                try:
                    r = await client.send(req, stream=True)
                except httpx.ReadError as e:
                    logger.warning(
                        "Upstream ReadError on %s (%s, attempt %d/%d, elapsed %.2fs): connection reset before headers",
                        target_url,
                        backend.name,
                        attempt,
                        max_attempts,
                        time.time() - t0,
                    )
                    raise

                if r.status_code >= 500:
                    # Any 5xx counts against the circuit, even with retries
                    # disabled — otherwise a backend that always returns 500
                    # never trips its breaker.
                    cb.record_failure(base_url, backend.name)
                    can_retry = enable_retry and attempt < max_attempts
                    has_more_urls = idx < len(candidate_urls) - 1
                    if can_retry or has_more_urls:
                        # We won't return this response, so release its stream
                        # before opening another one.
                        await r.aclose()
                        if pending_5xx is not None:
                            await pending_5xx.aclose()
                            pending_5xx = None
                            pending_5xx_url = None
                    else:
                        # Last candidate and attempt exhausted: keep this
                        # response open and return it to the client below.
                        if pending_5xx is not None:
                            await pending_5xx.aclose()
                        pending_5xx = r
                        pending_5xx_url = base_url
                    if can_retry:
                        logger.warning(f"Backend {base_url} returned {r.status_code}, retrying (attempt {attempt}/{max_attempts})...")
                        await asyncio.sleep(_retry_sleep(backoff))
                        backoff = min(backoff * 2.0, MAX_RETRY_BACKOFF)
                        continue
                    # Try the next candidate URL (or fall through to return the
                    # terminal 5xx response once all candidates are exhausted).
                    break

                if pending_5xx is not None:
                    # A stale 5xx from an earlier candidate was superseded by a
                    # successful attempt; release it.
                    await pending_5xx.aclose()
                    pending_5xx = None
                    pending_5xx_url = None

                cb.record_success(base_url)
                record_backend_latency(base_url, time.time() - t0)
                if idx > 0 or attempt > 1:
                    logger.info(f"Request succeeded: using {base_url} (URL {idx+1}/{len(urls)}, Attempt {attempt}/{max_attempts})")
                return r, base_url, None
            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.ReadError,
                httpx.WriteError,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
                httpx.RemoteProtocolError,
                httpx.TransportError,
            ) as exc:
                last_exc = exc
                cb.record_failure(base_url, backend.name)
                # Purge and close client to discard broken keepalive sockets from the pool
                await _reset_client(base_url)
                if attempt < max_attempts:
                    sleep_s = _retry_sleep(backoff)
                    logger.warning(
                        "Backend %s failed (%s: %s), retrying (attempt %d/%d) in %.2fs (jittered from %.1fs)...",
                        base_url,
                        type(exc).__name__,
                        exc,
                        attempt,
                        max_attempts,
                        sleep_s,
                        backoff,
                    )
                    await asyncio.sleep(sleep_s)
                    backoff = min(backoff * 2.0, MAX_RETRY_BACKOFF)
                    continue
                else:
                    logger.warning(
                        "Backend %s failed (%s: %s) after %d attempt(s), trying next URL...",
                        base_url,
                        type(exc).__name__,
                        exc,
                        attempt,
                    )
                    break

    if pending_5xx is not None:
        # All URLs/attempts exhausted with upstream 5xx responses and no
        # transport exception after the last one: hand the last 5xx (streamed,
        # unread) to the client rather than raising.
        if idx > 0 or attempt > 1:
            logger.info(
                "Returning last 5xx response from %s (URL %d/%d, attempt %d/%d)",
                pending_5xx_url, idx + 1, len(candidate_urls), attempt, max_attempts,
            )
        return pending_5xx, pending_5xx_url or "", None

    if last_exc is None:
        last_exc = httpx.ConnectError(
            f"No healthy upstream URLs available for backend '{backend.name}' (configured URLs: {urls})"
        )
    elif isinstance(last_exc, httpx.TransportError) and pending_5xx is None:
        # Pure transport failure (reset/timeout/connect) on every URL and
        # attempt. Wrap with an actionable message -- the bare ReadError from
        # httpx often carries an empty string, which becomes an opaque 502.
        last_exc = UpstreamConnectionReset(candidate_urls[idx] if candidate_urls else "", last_exc)
    raise last_exc


# ---------------------------------------------------------------------------
# Granular Model & Path Quarantining
# ---------------------------------------------------------------------------

QUARANTINE_FORBIDDEN = 3600   # 1 hour for 401/403
QUARANTINE_RATE_LIMIT = 300   # 5 minutes for 429
QUARANTINE_OVERLOADED = 60    # 1 minute for 503
QUARANTINE_SERVER_ERR = 30    # 30 seconds for 500/502/504

_model_quarantines: Dict[Tuple[str, str], float] = {}

def quarantine_model(backend_name: str, model_name: str, duration: int, reason: str = "") -> None:
    """Quarantine a specific (backend, model) pair for a cooldown duration."""
    import time
    if not backend_name or not model_name:
        return
    expiry = time.time() + duration
    _model_quarantines[(backend_name, model_name)] = expiry
    logger.warning(
        "QUARANTINE: Model '%s' on backend '%s' quarantined for %ds. Reason: %s",
        model_name, backend_name, duration, reason or "Error response"
    )

def is_model_quarantined(backend_name: str, model_name: str) -> bool:
    """Check whether a (backend, model) pair is currently in quarantine cooldown."""
    import time
    if not backend_name or not model_name:
        return False
    expiry = _model_quarantines.get((backend_name, model_name), 0.0)
    if expiry > time.time():
        return True
    if (backend_name, model_name) in _model_quarantines:
        del _model_quarantines[(backend_name, model_name)]
    return False

def get_active_quarantines() -> List[Dict[str, Any]]:
    """Return all active model quarantines with seconds remaining."""
    import time
    now = time.time()
    active = []
    for (b_name, m_name), exp in list(_model_quarantines.items()):
        if exp > now:
            active.append({
                "backend_name": b_name,
                "model_name": m_name,
                "remaining_seconds": int(exp - now),
            })
        else:
            del _model_quarantines[(b_name, m_name)]
    return active

def clear_model_quarantine(backend_name: str, model_name: str) -> None:
    """Clear quarantine for a (backend, model) pair."""
    _model_quarantines.pop((backend_name, model_name), None)


SERVED_MODEL_EXTENSION = "llm_gateway.served_model"


def _mark_served_model(r, model: Optional[str]) -> None:
    """Record which model actually served a response on ``r.extensions`` (fallback targets differ)."""
    if not model:
        return
    try:
        r.extensions[SERVED_MODEL_EXTENSION] = model
    except Exception:
        pass


def normalize_backend_payload(body: Any, backend_type: str) -> Any:
    """Normalize vendor-specific payload differences across backends.

    - vLLM expects reasoning_effort in ('xhigh', 'medium', 'low'); OpenAI standard uses 'high'.
    - OpenAI/Azure expect reasoning_effort in ('high', 'medium', 'low').
    - Clean up messages for upstream backends (e.g. vLLM):
      - Ensure 'role' is present on every message in 'messages' (default to 'assistant' if tool_calls present, else 'user').
      - Convert stringified 'tool_calls' into list of dicts.
      - Strip non-standard client replay fields ('reasoning', 'reasoning_content', 'cot_id', 'cot_summary')
        that cause vLLM's chat template renderer to crash with TCP connection reset (ReadError).
    """
    if not isinstance(body, dict):
        return body
    btype = str(backend_type or "").lower()
    new_body = dict(body)

    # vLLM reasoning_effort normalization: map 'high' -> 'xhigh'
    if btype == "vllm" and "reasoning_effort" in new_body:
        effort = str(new_body.get("reasoning_effort", "")).lower()
        if effort == "high":
            new_body["reasoning_effort"] = "xhigh"
    elif btype in ("openai", "azure") and "reasoning_effort" in new_body:
        effort = str(new_body.get("reasoning_effort", "")).lower()
        if effort == "xhigh":
            new_body["reasoning_effort"] = "high"

    # Normalize messages for upstream compatibility
    if "messages" in new_body and isinstance(new_body["messages"], list):
        sanitized_messages = []
        for msg in new_body["messages"]:
            if not isinstance(msg, dict):
                sanitized_messages.append(msg)
                continue
            m = dict(msg)
            # Ensure 'role' is always present
            if "role" not in m or not m["role"]:
                m["role"] = "assistant" if ("tool_calls" in m or "tool_call_id" in m) else "user"

            # Parse stringified tool_calls if sent by certain agent frameworks
            if "tool_calls" in m and isinstance(m["tool_calls"], str):
                try:
                    parsed_tc = json.loads(m["tool_calls"])
                    if isinstance(parsed_tc, list):
                        m["tool_calls"] = parsed_tc
                except Exception:
                    pass

            # For vLLM and local backends, strip non-standard thinking/reasoning history fields
            # that cause Jinja chat templates and tokenizer to crash with connection reset
            if btype in ("vllm", "ollama", "llamacpp"):
                for field in ("reasoning", "reasoning_content", "cot_id", "cot_summary"):
                    m.pop(field, None)

            sanitized_messages.append(m)
        new_body["messages"] = sanitized_messages

    return new_body


def backend_type_name(backend) -> str:
    """Backend type as a plain string (the enum value), for labels and spans."""
    bt = getattr(backend, "backend_type", None)
    return str(getattr(bt, "value", bt) or "unknown")


def served_model_of(r, default: str) -> str:
    """Model that served ``r``: the fallback target's model, else ``default`` (the requested model)."""
    try:
        value = (getattr(r, "extensions", None) or {}).get(SERVED_MODEL_EXTENSION)
    except Exception:
        value = None
    return value if isinstance(value, str) and value else default


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
    timeout: float = DEFAULT_UPSTREAM_TIMEOUT,
    enable_retry: bool = False,
) -> Tuple[httpx.Response, str, LLMBackend, Optional[httpx.AsyncClient]]:
    """Try primary backend. If it returns 429/5xx or connection fails, try fallback chain targets."""
    req_model = (body.get("model") if isinstance(body, dict) else "") or "*"
    
    # If primary model is actively quarantined and we have a fallback chain, skip directly to fallback
    if fallback_chain_id and is_model_quarantined(backend.name, req_model):
        logger.info("Primary backend '%s' model '%s' is in cooldown quarantine, bypassing to fallback chain %s",
                    backend.name, req_model, fallback_chain_id)
    else:
        try:
            r, used_url, client = await try_backend_with_fallback(
                backend, path, method, headers, body, raw_body, timeout=timeout, enable_retry=enable_retry
            )
            if r.status_code < 400 or (r.status_code not in (429, 500, 502, 503, 504) or not fallback_chain_id):
                return r, used_url, backend, client
            
            # Apply granular quarantine to this model
            dur = (QUARANTINE_RATE_LIMIT if r.status_code == 429 
                   else (QUARANTINE_OVERLOADED if r.status_code == 503 
                   else (QUARANTINE_FORBIDDEN if r.status_code in (401, 403) else QUARANTINE_SERVER_ERR)))
            quarantine_model(backend.name, req_model, dur, f"HTTP {r.status_code}")
            
            logger.warning(
                "Primary backend %s returned status %d for model %s, triggering fallback chain %s",
                backend.name, r.status_code, req_model, fallback_chain_id
            )
            await r.aclose()
        except Exception as exc:
            dur = QUARANTINE_SERVER_ERR
            quarantine_model(backend.name, req_model, dur, f"Exception: {exc}")
            if not fallback_chain_id:
                raise
            logger.warning(
                "Primary backend %s failed (%s) for model %s, triggering fallback chain %s",
                backend.name, exc, req_model, fallback_chain_id
            )

    from .services.fallback_service import get_chain_targets, rank_fallback_targets
    targets = await get_chain_targets(session, fallback_chain_id)
    targets = rank_fallback_targets(targets)
    backends = await service.list_backends(session)
    backends_by_name = {b.name: b for b in backends}

    last_exc = None
    for target in targets:
        target_name = target.get("backend_name")
        target_model = target.get("model")
        fb_backend = backends_by_name.get(target_name)
        if not fb_backend:
            continue

        # Granular quarantine check on target model
        if target_model and is_model_quarantined(target_name, target_model):
            logger.info("Skipping quarantined fallback target backend %s (model=%s)", target_name, target_model)
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
                _mark_served_model(r, target_model or req_model)
                return r, used_url, fb_backend, client
            
            dur = (QUARANTINE_RATE_LIMIT if r.status_code == 429 
                   else (QUARANTINE_OVERLOADED if r.status_code == 503 
                   else (QUARANTINE_FORBIDDEN if r.status_code in (401, 403) else QUARANTINE_SERVER_ERR)))
            quarantine_model(target_name, target_model or "*", dur, f"HTTP {r.status_code}")
            await r.aclose()
        except Exception as exc:
            quarantine_model(target_name, target_model or "*", QUARANTINE_SERVER_ERR, f"Exception: {exc}")
            last_exc = exc
            continue

    if last_exc:
        raise last_exc
    raise HTTPException(status_code=502, detail="All targets in fallback chain failed")


# Hop-by-hop and length/encoding headers that must not be copied from an
# upstream response onto a StreamingResponse: the body may be rewritten
# (thinking normalization, protocol translation), and Starlette streams it
# chunked anyway.
_DROP_RESPONSE_HEADERS = {
    "content-length", "content-encoding", "transfer-encoding", "connection", "keep-alive",
}


def passthrough_response_headers(upstream_headers) -> dict:
    return {k: v for k, v in upstream_headers.items() if k.lower() not in _DROP_RESPONSE_HEADERS}


async def output_guardrail_kwargs(session, *, model: str = "") -> Dict[str, Any]:
    """kwargs for stream_with_telemetry implementing output guardrails.

    Returns {} when output guardrails are disabled so streaming behaves exactly
    as before. When enabled, returns the guardrail callable, configured action
    and an audit callback.
    """
    from .services.guardrails_service import (
        output_guardrail_state, make_output_guardrail_fn, audit_guardrail_event,
    )

    enabled, action = await output_guardrail_state(session)
    if not enabled:
        return {}

    owner_id = ""

    def _on_violation(decision):
        audit_guardrail_event(owner_id or None, model or None, decision, where="stream_output")

    return {
        "output_guardrail": make_output_guardrail_fn(),
        "output_guardrail_action": action,
        "on_output_violation": _on_violation,
    }


async def output_pii_kwargs(session, *, model: str = "",
                            owner_id: Optional[str] = None) -> Dict[str, Any]:
    """kwargs for stream_with_telemetry implementing response PII masking.

    Returns {} unless response masking is enabled. Uses dynamic settings:
    GUARDRAIL_OUTPUT_PII_MASKING (tri-state override; empty falls back to
    ENABLE_PII_MASKING) and optional PII_MASK_CATEGORIES (comma-separated).
    The scanner honours DB-custom patterns (SystemSetting PII_PATTERNS).
    """
    from .services import config_service
    from .services.pii_service import PIIScrubber, get_pii_scanner_async

    try:
        raw = str(await config_service.get_setting(
            session, "GUARDRAIL_OUTPUT_PII_MASKING", "")).strip()
        if raw == "":
            raw = str(await config_service.get_setting(
                session, "ENABLE_PII_MASKING", "false"))
        if raw.lower() != "true":
            return {}
        cats_raw = str(await config_service.get_setting(
            session, "PII_MASK_CATEGORIES", "")).strip()
        categories = ([c.strip() for c in cats_raw.split(",") if c.strip()]
                      or None)
        scanner = await get_pii_scanner_async(session)
    except Exception as exc:  # pragma: no cover - config lookup must never break a request
        logger.warning("Response PII masking setup failed: %s", exc)
        return {}

    def _on_pii_mask(total: int, categories: set) -> None:
        try:
            from .audit import get_audit_logger

            get_audit_logger().log(
                "pii_response_masked", owner_id, model or "unknown", "info",
                {"masked_count": total, "categories": sorted(categories),
                 "where": "response_output"},
            )
        except Exception:  # pragma: no cover
            pass

    return {
        "pii_scrubber": PIIScrubber(scanner, categories=categories),
        "on_pii_mask": _on_pii_mask,
    }


async def check_response_guardrail(session, response_obj: Any, *, model: str = "",
                                   where: str = "output") -> Optional[dict]:
    """POST_CALL guardrail for non-streaming JSON responses.

    Returns None when the response is clean (or the check is disabled), or a
    redacted replacement response body when sensitive content was detected.
    """
    from .services.guardrails_service import (
        output_guardrail_state, evaluate_guardrail, GuardrailPhase,
        GuardrailAction, extract_assistant_text, audit_guardrail_event, GUARDRAIL_NOTICE,
    )

    enabled, action = await output_guardrail_state(session)
    if not enabled:
        return None
    try:
        text = extract_assistant_text(response_obj)
        if not text:
            return None
        decision = evaluate_guardrail(GuardrailPhase.POST_CALL, text)
        if decision.is_allowed:
            return None
        audit_guardrail_event(None, model or None, decision, where=where)
        if action != "block":
            return None
        return {"__guardrail_redacted__": True, "text": GUARDRAIL_NOTICE}
    except Exception as exc:  # pragma: no cover - never break a good response
        logger.warning("response guardrail check failed: %s", exc)
        return None


def redacted_openai_body(original: Any, notice: str) -> dict:
    """Build a minimal OpenAI-style redacted completion preserving the original id/model."""
    orig = original if isinstance(original, dict) else {}
    return {
        "id": orig.get("id", "gateway-guardrail"),
        "object": orig.get("object", "chat.completion"),
        "created": orig.get("created", 0),
        "model": orig.get("model", "unknown"),
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": notice},
            "finish_reason": "stop",
        }],
        "usage": orig.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
    }


async def mask_response_pii(session, body: Any, *, model: str = "",
                           owner_id: Optional[str] = None,
                           anthropic: bool = False) -> Any:
    """Response-side PII masking for non-streaming JSON bodies handled outside
    stream_with_telemetry (the /v1/messages client.post path).

    Returns the (possibly modified) body; no-op when masking is disabled.
    """
    kwargs = await output_pii_kwargs(session, model=model, owner_id=owner_id)
    scrubber = kwargs.get("pii_scrubber")
    on_mask = kwargs.get("on_pii_mask")
    if scrubber is None or not isinstance(body, dict):
        return body
    try:
        if anthropic:
            blocks = body.get("content")
            if isinstance(blocks, list):
                for block in blocks:
                    if isinstance(block, dict) and isinstance(block.get("text"), str) and block["text"]:
                        block["text"] = scrubber.scrub_full(block["text"])
        else:
            for choice in (body.get("choices") or []):
                if not isinstance(choice, dict):
                    continue
                msg = choice.get("message") or choice.get("delta")
                if isinstance(msg, dict) and isinstance(msg.get("content"), str) and msg["content"]:
                    msg["content"] = scrubber.scrub_full(msg["content"])
        total = getattr(scrubber, "total_masked", 0) or 0
        if total and on_mask is not None:
            on_mask(total, set(getattr(scrubber, "categories_seen", set()) or set()))
    except Exception as exc:  # pragma: no cover - masking must never break a response
        logger.warning("Response PII masking failed: %s", exc)
    return body


def redacted_anthropic_body(original: Any, notice: str, model: str = "") -> dict:
    """Build a minimal Anthropic-style redacted message preserving id/model."""
    orig = original if isinstance(original, dict) else {}
    return {
        "id": orig.get("id", "gateway-guardrail"),
        "type": "message",
        "role": "assistant",
        "model": orig.get("model", model or "unknown"),
        "content": [{"type": "text", "text": notice}],
        "stop_reason": "stop",
        "usage": orig.get("usage", {"input_tokens": 0, "output_tokens": 0}),
    }
