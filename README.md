# LLM Secure Gateway

A secure, multi-tenant AI gateway providing API key management, RBAC authentication, policy enforcement, and LLM observability for Ollama, vLLM, llama.cpp, OpenAI, Anthropic, Google, Groq, and custom backends.

---

## Features

| Feature                     | Description                                                              |
| --------------------------- | ------------------------------------------------------------------------ |
| **RBAC Authentication**     | Role-based access (Admin, Manager, Developer, Viewer)                    |
| **Multi-Tenant Isolation**  | Per-owner API keys with scoped permissions                               |
| **Provider Key Management** | Store and use owner-specific LLM provider keys (Fernet-encrypted)        |
| **Rate Limiting**           | Token-bucket rate limiter, configurable per API key (RPM)                |
| **Phoenix Telemetry**       | OpenInference-compliant LLM tracing with per-tenant project isolation    |
| **Dynamic Routing**         | Route to backends by provider or model; smart load-balancing             |
| **Model Federation**        | Unify distributed backends into a single model namespace                 |
| **Security Hardening**      | Block destructive endpoints (`/api/pull`, `/api/delete`, etc.) per owner |
| **Invite System**           | Registration gating with cryptographic invite codes                      |
| **Premium Admin UI**        | Glassmorphism dark theme with real-time dashboards                       |
| **CSRF Protection**         | Double-submit cookie pattern for browser UI sessions                     |
| **Accessible UI**           | ARIA attributes, form labels, semantic roles across all templates        |
| **Zero-Trust Ready**        | SPIFFE/mTLS identity extraction support                                  |
| **llama.cpp Server**        | Native `llama.cpp` support with automatic model and `n_ctx` discovery via `/props` |
| **Granular Model Resilience** | Per-model cooldown quarantining (429/5xx), isolating failing model routes |
| **Ollama Protocol**         | Native `/api/tags` and `/api/version` endpoints for OpenWebUI and Ollama CLI |

---

## Quick Start

### Prerequisites

- Python 3.10+
- Docker & Docker Compose
- PostgreSQL 18

### Run with Docker

```bash
git clone https://github.com/deziss/llm-secure-gateway.git
cd llm-secure-gateway

cp .env.example .env
# Edit .env — at minimum set ENCRYPTION_KEY, DATABASE_URL,
# DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD

docker compose up -d
```

### Access Points

| URL                                           | Purpose              |
| --------------------------------------------- | -------------------- |
| `http://localhost:6130/admin/dashboard`       | Admin UI             |
| `http://localhost:6130/auth/login`            | Login page           |
| `http://localhost:6130/admin/view/playground` | API playground       |
| `http://localhost:6130/docs`                  | OpenAPI / Swagger UI |
| `http://localhost:6006`                       | Phoenix telemetry    |

---

## Configuration

### Required Environment Variables

| Variable                 | Description                                                                                                                                             |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DATABASE_URL`           | PostgreSQL async connection string (e.g. `postgresql+asyncpg://gateway:password@postgres:5432/gateway_db`)                                              |
| `ENCRYPTION_KEY`         | Fernet key for encrypting provider API keys. Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DEFAULT_ADMIN_EMAIL`    | Email for the default admin account created on first startup                                                                                            |
| `DEFAULT_ADMIN_PASSWORD` | Password for the default admin account                                                                                                                  |

### Optional Environment Variables

| Variable                      | Default                  | Description                                                                                                                        |
| ----------------------------- | ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `REDIS_URL`                   | —                        | Redis connection string for cross-worker rate limiting (e.g. `redis://redis:6379/0`). Falls back to in-process limiter if not set. |
| `PGBOUNCER_URL`               | —                        | PgBouncer connection string for transaction-mode pooling. Falls back to direct `DATABASE_URL` if not set.                          |
| `APP_BASE_URL`                | `http://localhost:8000`  | Base URL used in email templates (login links, etc.)                                                                               |
| `APP_VERSION`                 | `0.5.0`                  | Service version reported in telemetry resource attributes                                                                          |
| `SQL_ECHO`                    | `false`                  | Set to `true` to enable SQLAlchemy query logging (development only)                                                                |
| `PHOENIX_COLLECTOR_ENDPOINT`  | `http://localhost:6006`  | Phoenix OTEL collector endpoint                                                                                                    |
| `PHOENIX_API_KEY`             | —                        | Phoenix authentication key                                                                                                         |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | —                        | Generic OTLP metrics endpoint                                                                                                      |
| `SMTP_HOST`                   | `localhost`              | SMTP server hostname                                                                                                               |
| `SMTP_PORT`                   | `587`                    | SMTP server port                                                                                                                   |
| `SMTP_USER`                   | —                        | SMTP username                                                                                                                      |
| `SMTP_PASSWORD`               | —                        | SMTP password                                                                                                                      |
| `SMTP_TLS`                    | `true`                   | Enable STARTTLS for SMTP                                                                                                           |
| `EMAILS_FROM_EMAIL`           | `admin@llmgateway.io`    | Sender address for transactional emails                                                                                            |
| `EMAILS_FROM_NAME`            | `LLM Gateway Admin`      | Sender display name                                                                                                                |
| `ADMIN_EMAIL`                 | `superadmin@example.com` | Recipient for admin alert emails                                                                                                   |

### Production Deployment (HA)

```bash
# Core only (2 gateways + nginx LB + PostgreSQL)
docker compose -f docker-compose.prod.yml up -d

# With Redis rate limiting
docker compose -f docker-compose.prod.yml --profile redis up -d
# Set REDIS_URL=redis://redis:6379/0 in .env

# With PgBouncer connection pooling
docker compose -f docker-compose.prod.yml --profile pgbouncer up -d
# Set PGBOUNCER_URL=postgresql+asyncpg://gateway:password@pgbouncer:6432/gateway_db in .env
```

All optional services gracefully degrade — the gateway always starts even if Redis or PgBouncer are unreachable.

---

## Database Migrations

```bash
# Apply all pending migrations
docker exec -it llm-gateway alembic upgrade head

# Upgrade from v0.1 to v0.3 schema
docker exec -it llm-gateway alembic upgrade 001_v01_to_v03

# Create a new migration after model changes
docker exec -it llm-gateway alembic revision --autogenerate -m "description"
```

See [docs/DATABASE_MIGRATION.md](docs/DATABASE_MIGRATION.md) for data import procedures.

---

## Project Structure

```
llm-secure-gateway/
├── alembic/                        # Database migrations
│   ├── env.py
│   └── versions/
│       ├── 001_v01_to_v03.py       # v0.1 → v0.3 schema migration
│       └── 002_owner_schema_improvements.py
├── src/llm_gateway/
│   ├── main.py                     # FastAPI application entry point
│   ├── config.py                   # Application configuration (env vars)
│   ├── database.py                 # Async engine & session factory
│   ├── models.py                   # Core DB models (SQLModel)
│   ├── middleware.py               # Auth, CSRF & policy middleware
│   ├── proxy_helpers.py            # Shared proxy utilities
│   ├── policy.py                   # Policy engine
│   ├── audit.py                    # Structured audit logging
│   ├── rate_limit.py               # Token-bucket rate limiter
│   ├── tracking.py                 # IP tracking middleware
│   ├── telemetry/                  # Phoenix/OTEL integration (package)
│   │   ├── tracing.py              # Distributed tracing
│   │   ├── metrics.py              # Counters & histograms
│   │   ├── streaming.py            # Stream telemetry wrapping
│   │   └── setup.py                # Initialization
│   ├── provider_registry.py        # In-memory provider/model registry
│   ├── cli.py                      # Admin CLI tool
│   ├── pagination.py               # Shared pagination query params
│   ├── auth/
│   │   ├── models.py               # User model with RBAC roles
│   │   ├── schemas.py              # Pydantic request/response schemas
│   │   ├── users.py                # FastAPI Users configuration
│   │   ├── manager.py              # User manager
│   │   └── db.py                   # Auth DB helpers
│   ├── routers/
│   │   ├── admin.py                # Core admin routes & helpers
│   │   ├── backends.py             # Backend CRUD routes
│   │   ├── owners.py               # Owner management routes
│   │   ├── users.py                # User management routes
│   │   ├── settings.py             # System settings & metrics
│   │   ├── ui.py                   # UI page rendering routes
│   │   ├── auth_aux.py             # Auth helper routes
│   │   ├── proxy.py                # V1 proxy (model-based routing)
│   │   └── v2_proxy.py             # V2 proxy (direct/provider routing)
│   ├── services/
│   │   ├── __init__.py             # Re-exports all services
│   │   ├── config_service.py       # Backend & settings CRUD
│   │   ├── auth_service.py         # API key lifecycle
│   │   ├── owner_service.py        # Owner & provider key management
│   │   ├── email_service.py        # Transactional email (SMTP)
│   │   └── federation_service.py   # Background model polling
│   └── templates/                  # Jinja2 HTML templates
│       ├── base.html
│       ├── dashboard.html
│       ├── backends.html
│       ├── owners.html
│       ├── users.html
│       ├── settings.html
│       ├── playground.html
│       ├── chat_playground.html
│       └── embedding_playground.html
│   └── static/js/                  # Extracted frontend JavaScript
│       ├── globals.js              # Shared globals & fetchWithCsrf
│       ├── ui-components.js        # Toast, confirm, utilities
│       └── *.js                    # Page-specific modules
├── docs/
│   ├── ARCHITECTURE.md             # System design & diagrams
│   ├── API_REFERENCE.md            # Complete endpoint reference
│   ├── DATABASE_MIGRATION.md       # Data import & migration guide
│   └── gateway_ollama_endpoints.md # Ollama curl examples
├── tests/
├── docker-compose.yml
├── Dockerfile
├── alembic.ini
├── pyproject.toml
├── CHANGELOG.md
├── USER_GUIDE.md
├── TELEMETRY_GUIDE.md
└── UPGRADE.md
```

---

## Testing

### Unit tests (Docker — no services required)

```bash
# Build once
docker build -t llm-gateway-test .

# Run 57 unit tests
docker run --rm \
  -e ENCRYPTION_KEY=dGVzdC1rZXktMTIzNDU2Nzg5MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTI= \
  -e AUTH_SECRET=test-auth-secret \
  -e DATABASE_URL="sqlite+aiosqlite:///:memory:" \
  -e DEFAULT_ADMIN_EMAIL=admin@example.com \
  -e DEFAULT_ADMIN_PASSWORD=testpassword \
  llm-gateway-test \
  python -m pytest tests/ -v --tb=short \
    --ignore=tests/scripts/ \
    --ignore=tests/test_provider_integration.py \
    --ignore=tests/test_phoenix.py
```

### Performance & integration (requires running gateway)

```bash
# End-to-end smoke test
python tests/scripts/verify_gateway.py basic

# Full V3 verification
python tests/scripts/verify_gateway.py v3

# Performance test (default 20 iterations)
./tests/perf.sh
./tests/perf.sh 50

# Stress test (default 60s, 10 concurrent)
./tests/stress_test.sh
./tests/stress_test.sh 120 20
```

See [docs/TESTING.md](docs/TESTING.md) for full coverage map, fixtures, and CI integration.

---

## Documentation

| Document                                                             | Description                                              |
| -------------------------------------------------------------------- | -------------------------------------------------------- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)                         | System design, component diagrams, request flow          |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md)                       | Complete REST API endpoint reference                     |
| [docs/TESTING.md](docs/TESTING.md)                                   | Test suite guide — running, coverage, adding tests       |
| [USER_GUIDE.md](USER_GUIDE.md)                                       | Admin UI walkthroughs, tenant management, key generation |
| [TELEMETRY_GUIDE.md](TELEMETRY_GUIDE.md)                             | Phoenix/OTEL observability setup                         |
| [UPGRADE.md](UPGRADE.md)                                             | Breaking changes and migration steps                     |
| [docs/DATABASE_MIGRATION.md](docs/DATABASE_MIGRATION.md)             | pg_dump import and Alembic migration guide               |
| [docs/gateway_ollama_endpoints.md](docs/gateway_ollama_endpoints.md) | Ollama curl examples through the gateway                 |
| [CHANGELOG.md](CHANGELOG.md)                                         | Full version history                                     |

---

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)** — see the [LICENSE](file:///home/anshukushwaha/Desktop/learn/llm-secure-gateway/LICENSE) file for the complete license terms.
