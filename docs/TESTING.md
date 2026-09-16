# Testing Guide

This document covers how to run the test suite, what each test module covers, and how to add new tests.

---

## Quick Start

### Run in Docker (recommended)

```bash
# Build the image once
docker build -t llm-gateway-test .

# Run the full unit test suite
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

Or using the test compose file:

```bash
# Rebuild image if source changed, then run
docker build -t llm-gateway-test . && \
docker compose -f docker-compose.test.yml run --rm test
```

### Run locally (requires Python 3.10+)

```bash
# Install with test dependencies
pip install -e ".[all,test]"

# Run unit tests
pytest tests/ -v --tb=short \
  --ignore=tests/scripts/ \
  --ignore=tests/test_provider_integration.py \
  --ignore=tests/test_phoenix.py
```

---

## Test Suite Overview

### Unit Tests (no database, no Docker required)

| File | Class / Scope | What it covers |
|---|---|---|
| `test_audit.py` | `TestAuditLogger` | Identity resolution (API key, SPIFFE, unknown), metadata defaults, singleton guarantee |
| `test_models.py` | `TestModels`, `TestAuthModels` | SQLModel creation, enum values, default field values, `APIKeyCreate` schema |
| `test_policy.py` | `TestPolicyEngine` | Scope checks (chat/read_only/admin), SPIFFE workload pass-through, singleton |
| `test_proxy_helpers.py` | `TestGetOwnerIdFromRequest` | API key owner, admin/manager UI bypass flag, SPIFFE dict, anonymous fallback |
| | `TestApplyRateLimit` | Allow within limit, 429 on exhaustion, no-user path |
| | `TestCheckOwnerPermissions` | Wildcard perms, no-perm-row 403, model-restricted 403, endpoint-blocked by backend, empty whitelist 403, specific model allow |
| `test_rate_limit.py` | `TestRateLimiter` | Allow within limit, block when exceeded, unlimited (rpm=0), token replenishment, per-user buckets, stale-bucket cleanup, singleton |
| `test_services.py` | `TestConfigService` | Backend register/get mock flows |
| | `TestAuthService` | SHA-256 hash consistency and uniqueness |
| | `TestOwnerService` | Fernet encrypt/decrypt roundtrip, key generation |
| `test_tracking.py` | `TestIPTracking` | Initially empty, recent IPs returned, old IPs filtered (>5 min), `last_seen_seconds_ago` accuracy, middleware dispatch records IP, middleware cleanup of stale IPs |

**Current count: 57 unit tests, all passing.**

### Integration / Smoke Tests (require running gateway + database)

| File | Purpose |
|---|---|
| `scripts/full_flow.py` | Owner creation + API key creation against live PostgreSQL |
| `scripts/key_creation.py` | API key lifecycle against live gateway |
| `scripts/register_manual.py` | User registration flow |
| `test_provider_integration.py` | Provider key storage and retrieval |
| `test_phoenix.py` | Phoenix telemetry span emission |
| `scripts/verify_gateway.py` | End-to-end smoke: auth → backend → proxy request |
| `scripts/verify_email.py` | SMTP email delivery verification |

Run integration tests with the gateway running (`docker compose up -d`):

```bash
# Basic smoke test
python tests/scripts/verify_gateway.py basic

# Full V3 verification
python tests/scripts/verify_gateway.py v3

# Both
python tests/scripts/verify_gateway.py all
```

### Load / Performance Tests

```bash
# Performance test — default 20 iterations
./tests/perf.sh
./tests/perf.sh 50

# Stress test — default 60s, 10 concurrent workers
./tests/stress_test.sh
./tests/stress_test.sh 120 20   # 120s, 20 concurrent
```

---

## Test Infrastructure

### Environment Variables

The unit test suite uses these variables (safe dummy values, no real services needed):

| Variable | Test value | Why |
|---|---|---|
| `ENCRYPTION_KEY` | `dGVzdC1rZXktMTIzNDU2...` | Valid base64 Fernet key for `OwnerService` |
| `AUTH_SECRET` | `test-auth-secret` | FastAPI-Users secret |
| `DATABASE_URL` | `sqlite+aiosqlite:///:memory:` | In-memory SQLite; no PostgreSQL needed |
| `DEFAULT_ADMIN_EMAIL` | `admin@example.com` | Prevents startup warning |
| `DEFAULT_ADMIN_PASSWORD` | `testpassword` | Prevents startup warning |

These are set in [conftest.py](../tests/conftest.py) via `os.environ` before any imports.

### conftest.py Fixtures

| Fixture | Scope | Description |
|---|---|---|
| `event_loop` | session | Shared asyncio loop for all async tests |
| `mock_session` | function | `AsyncMock` database session with stubbed execute/commit/add |
| `sample_backend_data` | function | Valid `LLMBackend` dict for model construction |
| `sample_owner_data` | function | Valid `Owner` dict |
| `sample_api_key_data` | function | Valid `APIKeyCreate` dict |
| `mock_request` | function | `MagicMock` FastAPI request with state and headers |

### pytest Configuration

In `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"   # All async tests automatically wrapped
```

`asyncio_mode = "auto"` means any `async def test_*` function works without `@pytest.mark.asyncio`.

---

## Adding New Tests

### Unit test for a module

```python
# tests/test_my_module.py
import pytest
from unittest.mock import MagicMock, AsyncMock


class TestMyThing:
    def test_sync_behaviour(self):
        from llm_gateway.my_module import MyThing
        thing = MyThing()
        assert thing.value == "expected"

    @pytest.mark.asyncio
    async def test_async_behaviour(self, mock_session):
        from llm_gateway.my_module import MyThing
        result = await MyThing().do_work(mock_session)
        assert result is not None
```

### Testing a route handler directly (no HTTP layer)

```python
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
import pytest


@pytest.mark.asyncio
async def test_my_route(mock_session):
    from llm_gateway.routers.backends import get_backend

    mock_session.execute = AsyncMock(return_value=...)
    with patch("llm_gateway.routers.backends.get_session", return_value=mock_session):
        result = await get_backend("my-backend", session=mock_session)
    assert result.name == "my-backend"
```

### Testing middleware

Use `IPTrackingMiddleware` in `test_tracking.py` as the pattern: construct the middleware with a `MagicMock` app, call `dispatch(request, call_next)` directly via `asyncio.get_event_loop().run_until_complete(...)`, and inspect module-level state.

---

## Generating a Fresh Fernet Test Key

If you need a new valid `ENCRYPTION_KEY` for tests:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Update the value in `conftest.py` and the Docker `run` command.

---

## CI Integration

To run tests in CI (GitHub Actions example):

```yaml
- name: Run unit tests
  run: |
    docker build -t llm-gateway-test .
    docker run --rm \
      -e ENCRYPTION_KEY=${{ secrets.TEST_ENCRYPTION_KEY }} \
      -e AUTH_SECRET=test-secret \
      -e DATABASE_URL="sqlite+aiosqlite:///:memory:" \
      -e DEFAULT_ADMIN_EMAIL=admin@example.com \
      -e DEFAULT_ADMIN_PASSWORD=testpassword \
      llm-gateway-test \
      python -m pytest tests/ -v --tb=short \
        --ignore=tests/scripts/ \
        --ignore=tests/test_provider_integration.py \
        --ignore=tests/test_phoenix.py
```
