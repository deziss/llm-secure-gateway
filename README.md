# LLM Secure Gateway

[![Version](https://img.shields.io/badge/version-0.9.0-blue.svg)](pyproject.toml)
[![Python](https://img.shields.io/badge/python-3.14+-3776AB.svg?logo=python&logoColor=white)](Dockerfile)
[![License](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](LICENSE)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.141+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg?logo=docker&logoColor=white)](docker-compose.yml)
[![Tests](https://img.shields.io/badge/tests-241%20passing-brightgreen.svg)](tests/)
[![CI](https://github.com/deziss/llm-secure-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/deziss/llm-secure-gateway/actions/workflows/ci.yml)
[![Docker Publish](https://github.com/deziss/llm-secure-gateway/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/deziss/llm-secure-gateway/actions/workflows/docker-publish.yml)

**LLM Secure Gateway** is an enterprise-grade, multi-tenant AI reverse proxy and governance layer for local and cloud Large Language Models. It provides virtualized API key management, granular role-based access control (RBAC), intelligent cross-provider failover, PII sanitization, and full-stack observability with OpenTelemetry and Arize Phoenix.

Supports **Ollama**, **vLLM**, **llama.cpp**, **OpenAI**, **Anthropic Claude**, **Google Gemini**, **Groq**, and custom OpenAI-compatible backends.

---

## Highlights & Capabilities

### 🚀 High-Performance Multi-Backend Routing
- **Universal Provider Support**: Route requests to Ollama, vLLM clusters, llama.cpp servers, OpenAI, Anthropic, Google Gemini, Groq, or custom self-hosted endpoints.
- **Protocol Translation**: Seamlessly translates requests between OpenAI and Anthropic formats, including automatic `<think>` reasoning tag stripping.
- **Model Federation**: Aggregates distributed backend models into unified `/v1/models` and `/api/tags` endpoints.
- **Native Ollama Protocol**: Full `/api/chat`, `/api/generate`, `/api/tags`, and `/api/version` compatibility for drop-in use with OpenWebUI and the Ollama CLI.

### 🛡️ Enterprise Security & Multi-Tenancy
- **Scoped Virtual API Keys**: Generate per-tenant keys restricted by model whitelist, route whitelist, CIDR IP filters, rate limits, and expiration dates.
- **Zero-Trust Identity**: SPIFFE / mTLS identity extraction and validation support for service-to-service deployments.
- **Credential Vault**: Sensitive upstream provider credentials stored encrypted at rest using AES-128 Fernet cryptography.
- **Hardened Administrative UI**: Dark-mode management portal built with Tailwind CSS, Lucide icons, double-submit cookie CSRF tokens, and semantic accessibility (WCAG).
- **Content Moderation & PII Shield**: Real-time regex and rule-based scrubbing for credit cards, SSNs, phone numbers, and harmful prompts before reaching upstream models.

### ⚡ Resilience & Fault Tolerance
- **Granular Model Quarantining**: Temporarily isolates failing `(backend, model)` routes upon HTTP errors (401/403: 1h, 429: 5m, 503: 1m, 500: 30s) while keeping healthy models on the same host operational.
- **Cross-Provider Fallback Chains**: Prioritized failover across backends and models if a provider fails or exceeds capacity.
- **Per-Backend Circuit Breaking**: Automatically trips unhealthy backend endpoints after consecutive transport failures.

### 📊 Full-Stack Observability & Governance
- **Arize Phoenix Tracing**: OpenInference-compliant distributed tracing with per-tenant project sandboxing.
- **Prometheus & Metrics**: Native `/metrics` endpoint tracking request latency, token throughput, cache hits, error rates, and active connections.
- **Token-Level Streaming Telemetry**: High-frequency async parser computing real-time prompt, completion, and total token usage on chunked responses.
- **Spend & Quota Governance**: Per-tenant budget ceilings, token limits, and spend tracking with configurable webhooks and email notifications.

---

## Architecture Overview

```
                          ┌────────────────────────┐
                          │   Client Application   │
                          │ (OpenWebUI, SDK, CLI)  │
                          └───────────┬────────────┘
                                      │ HTTP / Stream
                                      ▼
             ┌──────────────────────────────────────────────────┐
             │            LLM Secure Gateway (FastAPI)          │
             │                                                  │
             │  ┌────────────────────────────────────────────┐  │
             │  │ Middleware Pipeline                        │  │
             │  │ • IP Tracking     • SPIFFE / mTLS Extract  │  │
             │  │ • CSRF Protection • RBAC / Virtual API Key │  │
             │  │ • Rate Limiter    • Policy & Route Check   │  │
             │  └──────────────────────┬─────────────────────┘  │
             │                         │                        │
             │  ┌──────────────────────▼─────────────────────┐  │
             │  │ Processing & Services                      │  │
             │  │ • PII Scanner & Guardrails                 │  │
             │  │ • Prompt Caching (Redis / In-Memory)       │  │
             │  │ • Protocol Translation (OpenAI/Anthropic)  │  │
             │  │ • Model Quarantining & Failover Engine     │  │
             │  └──────────────────────┬─────────────────────┘  │
             └─────────────────────────┼────────────────────────┘
                                       │
        ┌──────────────┬───────────────┼───────────────┬──────────────┐
        ▼              ▼               ▼               ▼              ▼
 ┌─────────────┐ ┌───────────┐ ┌───────────────┐ ┌───────────┐ ┌─────────────┐
 │   Ollama    │ │   vLLM    │ │   llama.cpp   │ │  OpenAI   │ │  Anthropic  │
 │  Instances  │ │  Cluster  │ │    Servers    │ │  & Gemini │ │   & Groq    │
 └─────────────┘ └───────────┘ └───────────────┘ └───────────┘ └─────────────┘
```

---

## Quick Start

### Prerequisites
- **Docker Engine** 24.0+ and **Docker Compose** v2+
- *(Optional for local development)*: **Python 3.14+**

### Pre-built Docker Image (GHCR)
You can pull the official container directly:
```bash
docker pull ghcr.io/deziss/llm-secure-gateway:latest
```

### 1. Clone & Configure
```bash
git clone https://github.com/deziss/llm-secure-gateway.git
cd llm-secure-gateway

# Copy example environment configuration
cp .env.example .env
```

Edit `.env` and configure your initial secrets (at minimum set `ENCRYPTION_KEY`, `AUTH_SECRET`, `DEFAULT_ADMIN_EMAIL`, and `DEFAULT_ADMIN_PASSWORD`).

### 2. Launch Services
```bash
docker compose up -d
```

The gateway container compiles and initializes Alembic database migrations automatically upon boot.

### 3. Service Access Points

| Service / Interface | URL | Description |
| :--- | :--- | :--- |
| **Admin Dashboard** | `http://localhost:6130/admin/dashboard` | Main management portal |
| **Authentication** | `http://localhost:6130/auth/login` | Login page for administrators & tenants |
| **Interactive Playgrounds** | `http://localhost:6130/admin/view/playground` | Chat, compare, and embedding test suites |
| **REST API Documentation** | `http://localhost:6130/docs` | Interactive OpenAPI / Swagger interface |
| **Phoenix Observability** | `http://localhost:6006` | Arize Phoenix tracing UI (if enabled) |
| **Prometheus Metrics** | `http://localhost:6130/metrics` | Prometheus metrics scrape target |

---

## Client Usage Examples

### Using OpenAI Python SDK
Set `base_url` to the gateway and supply your Gateway API key:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:6130/v1",
    api_key="gw-live-your-virtual-api-key"
)

response = client.chat.completions.create(
    model="llama-3.3-70b-instruct",
    messages=[
        {"role": "system", "content": "You are a helpful coding assistant."},
        {"role": "user", "content": "Write a Python function to compute Fibonacci numbers."}
    ],
    temperature=0.7,
)

print(response.choices[0].message.content)
```

### Using Ollama CLI & OpenWebUI
Point your Ollama client directly to the gateway host:

```bash
export OLLAMA_HOST=http://localhost:6130

# Verify gateway connectivity and version
curl http://localhost:6130/api/version
# Output: {"version": "0.9.0"}

# Query aggregated models
curl -H "Authorization: Bearer gw-live-your-key" http://localhost:6130/api/tags
```

### Direct cURL Request
```bash
curl -X POST http://localhost:6130/v1/chat/completions \
  -H "Authorization: Bearer gw-live-your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mistral-7b-instruct",
    "messages": [{"role": "user", "content": "Explain circuit breakers in distributed systems."}],
    "stream": false
  }'
```

---

## Configuration Reference

### Core Variables

| Variable | Required | Default | Purpose |
| :--- | :---: | :--- | :--- |
| `DATABASE_URL` | **Yes** | `postgresql+asyncpg://...` | PostgreSQL async connection string |
| `ENCRYPTION_KEY` | **Yes** | — | 32-byte url-safe base64 key for Fernet credential encryption |
| `AUTH_SECRET` | **Yes** | — | Secret key for JWT user session signature verification |
| `DEFAULT_ADMIN_EMAIL` | **Yes** | — | Initial administrator account email |
| `DEFAULT_ADMIN_PASSWORD` | **Yes** | — | Initial administrator account password |
| `APP_VERSION` | No | `0.9.0` | Gateway release version emitted in logs & telemetry |

### Performance & Scaling Options

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `REDIS_URL` | — | Redis URL for distributed rate limiting and prompt caching (e.g. `redis://redis:6379/0`) |
| `PGBOUNCER_URL` | — | PgBouncer URL for high-concurrency database connection pooling |
| `ENABLE_MODEL_FEDERATION`| `true` | Periodically discovers and registers models across all active backends |
| `FEDERATION_POLL_INTERVAL`| `300` | Model discovery polling interval in seconds |
| `PHOENIX_COLLECTOR_ENDPOINT` | `http://localhost:6006` | Target endpoint for Arize Phoenix OTLP traces |

---

## Production Deployment & High Availability

For scalable production deployments, use the production compose profile:

```bash
# Core deployment: Load-balanced gateways + PostgreSQL
docker compose -f docker-compose.prod.yml up -d

# Scale with Redis distributed rate-limiting
docker compose -f docker-compose.prod.yml --profile redis up -d

# Scale with PgBouncer connection pooling
docker compose -f docker-compose.prod.yml --profile pgbouncer up -d
```

All microservice dependencies gracefully degrade — if Redis or PgBouncer are temporarily offline, the gateway transparently switches to in-process token-bucket limiting and direct database connections.

---

## Testing & Quality Assurance

The gateway includes a comprehensive test suite containing **241 automated tests** covering unit logic, proxy translation, model resilience, and telemetry.

```bash
# Run full test suite inside isolated Docker container
docker run --rm \
  -e ENCRYPTION_KEY=dGVzdC1rZXktMTIzNDU2Nzg5MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTI= \
  -e AUTH_SECRET=test-auth-secret \
  -e DATABASE_URL="sqlite+aiosqlite:///:memory:" \
  -e REDIS_URL="" \
  llm-gateway:v0.9.0-py314 pytest tests/ -v
```

### Performance & Benchmarking Scripts
```bash
# Run latency & throughput benchmarks
./tests/perf.sh 50

# Run concurrent load test (120 seconds, 20 concurrent connections)
./tests/stress_test.sh 120 20
```

---

## Repository Structure

```
llm-secure-gateway/
├── Dockerfile                      # Production multi-stage image (Python 3.14.7-slim)
├── docker-compose.yml              # Standard deployment orchestration
├── docker-compose.prod.yml         # High-availability production orchestration
├── pyproject.toml                  # Project metadata, dependencies & packaging
├── alembic.ini                     # Database migration configuration
├── alembic/                        # Database schema migration scripts
│   └── versions/                   # Schema revision history
├── src/llm_gateway/
│   ├── main.py                     # FastAPI application bootstrap & lifecycle
│   ├── models.py                   # SQLModel data models & backend enumerations
│   ├── config.py                   # Environment configuration & settings parsing
│   ├── database.py                 # Async database session & engine factory
│   ├── middleware.py               # Auth, CSRF, policy & rate limit enforcement
│   ├── proxy_helpers.py            # Model quarantine, circuit breaking & routing
│   ├── policy.py                   # RBAC & endpoint access authorization
│   ├── rate_limit.py               # Distributed Redis & in-memory token bucket
│   ├── provider_registry.py        # Dynamic provider and backend registry
│   ├── tracking.py                 # Client IP & connection tracking
│   ├── webhooks.py                 # Quota and administrative alert webhooks
│   ├── auth/                       # User management & authentication routes
│   ├── routers/                    # Modular API route controllers
│   │   ├── proxy.py                # V1 model-based routing proxy (/v1, /api)
│   │   ├── v2_proxy.py             # V2 direct provider routing proxy (/v2)
│   │   ├── backends.py             # LLM backend management CRUD
│   │   ├── owners.py               # Multi-tenant owner & API key management
│   │   ├── aliases.py              # Model aliases & fallback chains
│   │   ├── bots.py                 # Multi-channel bot bridges (Slack, Discord)
│   │   ├── spend.py                # Spend tracking & cost governance
│   │   ├── settings.py             # Administrative settings & system status
│   │   └── ui.py                   # Server-side HTML template renderer
│   ├── services/                   # Core business logic services
│   │   ├── federation_service.py   # Multi-backend model discovery & polling
│   │   ├── model_metadata_service.py # Context length (n_ctx) extraction
│   │   ├── pii_service.py          # Data sanitization & PII scrubbing
│   │   ├── guardrails_service.py   # Prompt injection & policy inspection
│   │   ├── json_healer.py          # Automatic LLM JSON schema repair
│   │   ├── cache_service.py        # Semantic & exact prompt caching
│   │   └── mcp_service.py          # Model Context Protocol tools bridge
│   ├── telemetry/                  # OpenTelemetry & Arize Phoenix telemetry
│   ├── templates/                  # Jinja2 templates (glassmorphic dark UI)
│   └── static/                     # Static frontend assets (Tailwind, Lucide)
├── docs/                           # Detailed technical documentation
│   ├── ARCHITECTURE.md             # System architecture & sequence flows
│   ├── API_REFERENCE.md            # Comprehensive REST endpoint documentation
│   ├── DATABASE_MIGRATION.md       # Migration procedures & backup guidelines
│   └── TESTING.md                  # Testing patterns & CI configuration
└── tests/                          # Automated test suite (241 tests)
```

---

## Documentation Directory

| Document | Purpose |
| :--- | :--- |
| [USER_GUIDE.md](USER_GUIDE.md) | Step-by-step walkthrough for configuring backends, owners, and keys |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | Complete reference for all REST endpoints, request bodies, and responses |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | In-depth architectural designs, sequence diagrams, and security models |
| [TELEMETRY_GUIDE.md](TELEMETRY_GUIDE.md) | Configuration guide for Arize Phoenix, OpenTelemetry, and Prometheus |
| [UPGRADE.md](UPGRADE.md) | Migration guidelines, version upgrade procedures, and breaking changes |
| [CHANGELOG.md](CHANGELOG.md) | Chronological release notes and changelog history |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution standards, local dev setup & PR rules |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Community code of conduct guidelines |
| [.github/SECURITY.md](.github/SECURITY.md) | Security vulnerability disclosure policy |

---

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**. See the [LICENSE](LICENSE) file for complete details.
