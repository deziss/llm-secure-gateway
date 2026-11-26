# Architecture

## Overview

The LLM Secure Gateway is a FastAPI monolith acting as a secure reverse proxy in front of one or more LLM backends (Ollama, vLLM, llama.cpp, OpenAI, Anthropic, Google, Groq, or custom). It enforces authentication, rate limiting, policy, and per-owner permissions before forwarding requests, and emits structured traces to Arize Phoenix.

---

## System Architecture

```mermaid
graph TB
    subgraph "Clients"
        SDK[API Client / SDK]
        UI[Admin Browser UI]
        WL[Workload / SPIFFE]
    end

    subgraph "LLM Secure Gateway  (FastAPI)"
        direction TB
        IP[IPTrackingMiddleware]
        PM[PolicyMiddleware]
        AM[AuthMiddleware]

        subgraph "Routers"
            PROXY[proxy.py — V1 model routing]
            V2[v2_proxy.py — V2 direct / provider]
            ADM[admin / backends / owners / users / settings]
            UI_R[ui.py — Admin HTML pages]
        end

        PH[proxy_helpers.py]
        SVC[Services layer]
        POL[policy.py]
        AUD[audit.py]
        RL[rate_limit.py]
        TEL[telemetry/ package]
    end

    subgraph "Persistence"
        PG[(PostgreSQL)]
    end

    subgraph "LLM Backends"
        OL[Ollama]
        VL[vLLM]
        LLC[llama.cpp]
        OAI[OpenAI API]
        ANT[Anthropic API]
        GRQ[Groq API]
    end

    subgraph "Observability"
        PHX[Arize Phoenix]
    end

    SDK -->|Bearer / x-api-key| IP
    UI  -->|Cookie| IP
    WL  -->|X-Spiffe-ID header| IP

    IP --> PM --> AM

    AM -->|JWT / Cookie| UI_R
    AM -->|API Key validated| PROXY
    AM -->|API Key validated| V2
    AM -->|Cookie / JWT| ADM

    PROXY --> PH
    V2    --> PH
    PH    --> SVC
    SVC   --> PG

    PROXY -->|httpx streaming| OL
    PROXY -->|httpx streaming| VL
    PROXY -->|httpx streaming| OAI
    V2    -->|httpx streaming| ANT
    V2    -->|httpx streaming| GRQ

    TEL -->|OTLP| PHX
    PROXY --> TEL
    V2    --> TEL
    ADM   --> SVC
```

---

## Request Flow — Proxy Call

```mermaid
sequenceDiagram
    participant C as Client
    participant IPM as IPTrackingMiddleware
    participant PM as PolicyMiddleware
    participant AM as AuthMiddleware
    participant PR as proxy.py / v2_proxy.py
    participant PH as proxy_helpers
    participant DB as PostgreSQL
    participant BE as LLM Backend
    participant PHX as Phoenix

    C->>IPM: POST /v1/chat/completions
    IPM->>PM: update IP timestamp, pass through
    PM->>PM: evaluate policy (scope check)
    PM->>AM: allowed
    AM->>DB: validate API key hash
    DB-->>AM: APIKey row
    AM->>PR: inject request.state.user

    PR->>PH: apply_rate_limit(request)
    PR->>PH: get_owner_id_from_request(request)
    PR->>DB: resolve backend (model routing)
    PR->>PH: check_owner_permissions(session, owner_id, backend, path, model)
    DB-->>PR: OwnerPermission rows
    PR->>PH: build_backend_auth_headers(...)
    DB-->>PR: ProviderKey (decrypted)

    PR->>PHX: start span "llm_request"
    PR->>BE: httpx streaming request
    BE-->>PR: SSE stream
    PR-->>C: StreamingResponse (pass-through)
    PR->>PHX: record token usage, end span
```

---

## Resilience & Granular Quarantining

The gateway implements multi-tier resilience across proxies:
- **Per-URL Circuit Breaking**: `CircuitBreaker` trips after 5 consecutive transport failures.
- **Granular Model Quarantining**: Rather than failing an entire multi-model cluster node, failing `(backend, model)` routes enter a temporary quarantine window:
  - `401 / 403` (Quota/Auth failure): 1-hour cooldown (`QUARANTINE_FORBIDDEN`)
  - `429` (Rate limiting): 5-minute cooldown (`QUARANTINE_RATE_LIMIT`)
  - `503` (Overload / slot exhaustion): 1-minute cooldown (`QUARANTINE_OVERLOADED`)
  - `500 / 502 / 504` (Server crash / internal error): 30-second cooldown (`QUARANTINE_SERVER_ERR`)
- **Cross-Provider Failover Chains**: Evaluates prioritized fallback targets, automatically bypassing any targets currently in cooldown quarantine.

---

## Middleware Chain

All middlewares are **pure ASGI** implementations (since v0.19.0) — no `BaseHTTPMiddleware` task-group buffering, so streaming responses pass through frame-by-frame with no per-request body copying. Middleware is applied with `app.add_middleware` (first registered = outermost); outermost-to-innermost order:

| Order | Class | Purpose |
|---|---|---|
| 1 (outermost) | `SecurityHeadersMiddleware` | Adds HSTS/CSP/X-Frame-Options etc. to every response |
| 2 | `IPTrackingMiddleware` | Records client IP + last-seen timestamp |
| 3 | `CSRFMiddleware` | Double-submit cookie CSRF protection for `/admin` and `/auth` POST/PUT/DELETE |
| 4 | `LoginRateLimitMiddleware` | Brute-force protection on login endpoints |
| 5 | `BackpressureMiddleware` | Caps in-flight requests; the concurrency slot is held until a streamed response body completes |
| 6 | `PolicyMiddleware` | Evaluates scope policy; writes to audit log |
| 7 (innermost) | `AuthMiddleware` | Validates API key (Bearer / x-api-key, cached) or passes cookie-auth to router |

Routes exempt from `AuthMiddleware`: `/`, `/health`, `/docs`, `/openapi.json`, `/admin/*`, `/auth/*`.

The event loop runs on **uvloop**, and rate limiting / prompt caching use **redis.asyncio**, so no Redis I/O blocks the request path.

---

## Service Layer

```
services/
├── config_service.py    ConfigService     — LLM backend CRUD, system settings read
├── auth_service.py      AuthService       — API key creation, validation, revocation
├── owner_service.py     OwnerService      — Owner CRUD, provider key encrypt/decrypt
├── email_service.py     EmailService      — SMTP transactional email (welcome, reset, alerts)
└── federation_service.py FederationService — Background loop: poll backends, update model lists
```

All services are singletons instantiated at module import time and exposed via `get_*_service()` dependency functions. `OwnerService` raises `RuntimeError` at startup if `ENCRYPTION_KEY` is not set.

---

## Database Schema

```mermaid
erDiagram
    USER {
        uuid id PK
        string email UK
        string hashed_password
        enum role
        bool is_active
        bool is_superuser
        bool is_verified
        datetime created_at
    }

    OWNER {
        string id PK
        enum type
        string name
        string email
        string user_id FK
        bool block_endpoints
        bool is_active
        int max_keys
    }

    APIKEY {
        string key_hash PK
        string prefix
        string owner_id FK
        json scopes
        datetime expires_at
        int rate_limit_rpm
        bool is_active
    }

    LLMBACKEND {
        string name PK
        string base_url
        enum backend_type
        string api_key
        json models
        json fallback_urls
        json allowed_endpoints
    }

    OWNERPERMISSION {
        int id PK
        string owner_id FK
        string backend_name FK
        json allowed_models
        json allowed_endpoints
    }

    PROVIDERKEY {
        int id PK
        string owner_id FK
        string provider_id
        string encrypted_key
    }

    SYSTEMSETTING {
        string key PK
        string value
    }

    INVITECODE {
        string code PK
        string created_by
        string used_by
        bool is_used
    }

    OWNER ||--o{ APIKEY : "has"
    OWNER ||--o{ OWNERPERMISSION : "has"
    OWNER ||--o{ PROVIDERKEY : "has"
    LLMBACKEND ||--o{ OWNERPERMISSION : "referenced by"
    USER ||--o| OWNER : "linked to"
```

Beyond the core entities above, the data layer (22 tables as of v0.19.0) includes:

| Table | Purpose |
|---|---|
| `ModelServer` | Registered model servers/backends (table `model_server`); `api_key` Fernet-encrypted at rest (`enc:v1:`) |
| `FallbackChain`, `ModelAlias` | Multi-model fallback chains and alias → model+backend mapping used by both proxy planes |
| `SpendRecord`, `ModelPricing`, `ModelTelemetryRollup` | Per-request spend attribution, USD pricing catalog, time-bucketed telemetry rollups |
| `CachedResponse` | Persistent exact-match prompt cache (with hit counts and expiry) |
| `AlertRule`, `AlertIncident` | Declarative alert rules (metric/comparator/window/target filter) + deduped incident episodes with ack workflow |
| `AgentRun`, `AgentStep` | Agent-run correlation: one workflow, many gateway LLM calls, with step waterfalls and handoff edges |
| `GpuHost`, `NodeHost` | Infrastructure inventory reported by the GPU/node agents |
| `ObsIntegration` | External observability export destinations |
| `PolicyProfile` | Per-tenant (owner-scoped) policy overrides merged into `PolicyEngine` decisions; TTL-cached, admin CRUD under `/admin/policy-profiles` |
| `AuditLog` | Tamper-evidence audit trail; retention configured per tier (`RETENTION_DAYS_AUDIT`) |

---

## Routing Strategies

### V1 — Model-Based (Auto-Routing)

Requests to `/{path}` are routed by matching the `model` field in the request body against registered backends.

| Setting | Behaviour |
|---|---|
| `ENABLE_EXPERIMENTAL_ROUTING=false` (default) | First backend that lists the model wins |
| `ENABLE_EXPERIMENTAL_ROUTING=true` | Random load-balance across all matching backends |

### V2 — Direct Routing

`/direct/{backend_name}/{path}` — routes to the named backend explicitly. Bypass model-based selection.

### V2 — Provider Routing

`/provider/{provider}/{path}` — routes to any backend whose `backend_type` matches `provider` (e.g. `ollama`, `openai`).

### Fallback URLs & Connection Resilience

Each backend can configure `fallback_urls`. The gateway manages persistent pooled HTTPX connections per backend with proactive `keepalive_expiry=15.0s` to prevent races against upstream server keep-alive timeouts.

- **Transport & Network Failure Recovery**: Catches `ConnectError`, `ConnectTimeout`, `ReadTimeout`, `ReadError`, `RemoteProtocolError`, `WriteError`, and all `TransportError` exceptions.
- **Client Pool Eviction**: On transport error or unexpected connection reset before headers, the dead pooled client is evicted and cleanly closed via `_reset_client(base_url)` so subsequent attempts establish a clean TCP handshake.
- **Retry & Backoff**: Up to 3 attempts with exponential backoff (1s → 2s → 4s) when `ENABLE_RETRY_BACKOFF=true`, and at least 2 attempts by default to recover from stale keep-alive drops without user-facing 502 errors.
- **Failover**: If all attempts on a URL fail, it falls back to the next configured URL in `fallback_urls`, and trips the circuit breaker on repeated failures. Any 5xx response counts as a circuit failure, even when retries are disabled; the last 5xx response is still streamed back to the client.
- **Circuit Breaker**: Opens after `CIRCUIT_BREAKER_FAILURE_THRESHOLD` consecutive failures (default 5) and half-opens after `CIRCUIT_BREAKER_RECOVERY_TIMEOUT` seconds (default 30). In half-open state only a single probe request is admitted; concurrent callers still see the circuit as open until the probe succeeds (closes) or fails (re-opens).
- **Configurable Upstream Timeout**: Defaults to 120.0s (`UPSTREAM_TIMEOUT` environment variable) to support extended reasoning / thinking token generation.

---

## Security Model

### Authentication Layers

1. **API Key** (`Authorization: Bearer sk-gateway-...` or `x-api-key`): hashed with SHA-256, stored in `apikey.key_hash`. Validated in `AuthMiddleware`.
2. **Cookie (UI)**: FastAPI-Users session cookie `fastapiusersauth`. Passed through `AuthMiddleware` to router-level FastAPI-Users dependency.
3. **SPIFFE** (`X-Spiffe-ID` header): Trusted header from a secure mesh sidecar. MVP implementation — assumes boundary is secured.

### Authorization Layers

| Layer | Enforcement point | What it checks |
|---|---|---|
| Policy scope | `PolicyMiddleware` | API key must have `chat` scope |
| Backend endpoint whitelist | `proxy_helpers.check_owner_permissions` | Path must match `backend.allowed_endpoints` |
| Owner permission | `proxy_helpers.check_owner_permissions` | Owner must have a matching `OwnerPermission` row |
| Danger block | `proxy.py` / `v2_proxy.py` | Blocks `/api/pull`, `/api/delete`, `/api/push`, `/api/create` when `owner.block_endpoints=true` |
| Admin UI bypass | All proxy routes | `role=admin` or `role=manager` users skip owner permission checks |

### Encryption

Provider keys (`providerkey.encrypted_key`) and model-server API keys (`llmbackend.api_key`, since v0.19.0, stored with an `enc:v1:` prefix) are Fernet-encrypted at rest using `GATEWAY_SECRET_KEY` (falling back to `ENCRYPTION_KEY` → `SECRET_KEY` → `AUTH_SECRET`, then an ephemeral key). Existing plaintext backend keys can be upgraded in place via `POST /admin/backends/encrypt-keys`. Keys are masked in admin API responses and never logged.

---

## Static Assets

Frontend JavaScript is served from `src/llm_gateway/static/js/` via FastAPI's `StaticFiles` mount at `/static`.

| File | Purpose |
|---|---|
| `globals.js` | `BASE_URL`, `USER_ROLE`, `fetchWithCsrf()`, `logout()` |
| `ui-components.js` | `showToast()`, `showConfirm()`, `getInitials()`, `getAvatarColor()`, `escapeHtml()` |
| `dashboard.js` | Dashboard metrics polling and chart rendering |
| `backends.js` | Backend CRUD, model sync, health check UI |
| `owners.js` | Owner/permission/key management UI |
| `users.js` | User management and invite UI |
| `settings.js` | System settings toggle and invite code management |
| `playground.js` | API playground request builder |
| `chat-playground.js` | Chat interface with streaming support |
| `embedding-playground.js` | Embedding visualization with UMAP |

All state-changing UI requests use `fetchWithCsrf()` which automatically includes the CSRF token from the `csrf_token` cookie in the `X-CSRF-Token` header.

---

## Telemetry

The `telemetry/` package (split into `tracing.py`, `metrics.py`, `streaming.py`, `setup.py`) integrates with Arize Phoenix for LLM-specific tracing. Each request creates an OpenInference-compliant span with:

- `llm.model_name`, `llm.input_messages`, `llm.output_messages`
- `llm.token_count.prompt`, `llm.token_count.completion`, `llm.token_count.total`
- `llm.time_to_first_token`
- `owner.id`, `provider`, `backend.name`, `routing_mode`

**Multi-tenant isolation**: Each `(owner_id, provider)` pair gets its own Phoenix tracer pointing to a distinct project name (`gateway-{owner}-{provider}`). This prevents cross-tenant trace mixing in Phoenix dashboards.

Metrics (Prometheus/OTLP):

| Metric | Type | Description |
|---|---|---|
| `llm_gateway_requests_total` | Counter | All proxy requests |
| `llm_gateway_requests_success_total` | Counter | Successful (2xx) requests |
| `llm_gateway_requests_failed_total` | Counter | Failed requests |
| `llm_gateway_prompt_tokens_total` | Counter | Prompt tokens consumed |
| `llm_gateway_completion_tokens_total` | Counter | Completion tokens generated |
| `llm_gateway_total_tokens_total` | Counter | Combined prompt + completion tokens |
| `llm_gateway_request_duration_seconds` | Histogram | End-to-end gateway latency |
| `llm_gateway_backend_latency_seconds` | Histogram | Backend-only latency |

---

## Background Services

### Model Federation (`federation_service.py`)

Polls all registered backends every 30 seconds when `ENABLE_MODEL_FEDERATION=true`. Updates `llmbackend.models` in the database. Runs as an `asyncio.Task` launched at startup.

Backend polling endpoints:

| Backend type | Endpoint |
|---|---|
| `ollama` | `GET /api/tags` |
| `vllm`, `openai`, `groq`, `custom` | `GET /v1/models` |
| `anthropic` | `GET /v1/models` (with `x-api-key` + `anthropic-version` headers) |
| `google` | Not supported (returns empty list) |
