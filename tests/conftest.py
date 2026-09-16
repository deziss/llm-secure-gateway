"""
Pytest configuration and fixtures for llm-gateway-v3 tests.
"""
import pytest
import asyncio

@pytest.fixture(autouse=True)
def ensure_event_loop():
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    yield loop

from typing import AsyncGenerator, Generator
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta
import os

# Set test environment variables before imports
# Generate a valid Fernet key: base64.urlsafe_b64encode(os.urandom(32))
os.environ["ENCRYPTION_KEY"] = "llJ1KD2l7NnR9z00fRiXAcJm_SflxW-uoIOqWOwqX5w="
os.environ["AUTH_SECRET"] = "test-auth-secret"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_session():
    """Create a mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.get = AsyncMock(return_value=None)
    return session


@pytest.fixture
def sample_backend_data():
    """Sample LLM backend data for testing."""
    return {
        "name": "test-ollama",
        "base_url": "http://localhost:11434",
        "backend_type": "ollama",
        "models": ["llama2", "mistral"],
        "fallback_urls": ["http://10.120.130.55:11434"],
        "allowed_endpoints": ["/v1/chat/completions"]
    }


@pytest.fixture
def sample_owner_data():
    """Sample owner data for testing."""
    return {
        "id": "user:test123",
        "name": "Test User",
        "email": "test@example.com",
        "type": "user",
        "is_active": True,
        "description": "Test owner for unit tests",
        "max_keys": 5
    }


@pytest.fixture
def sample_api_key_data():
    """Sample API key creation data for testing."""
    return {
        "owner": "user:test123",
        "scopes": ["chat"],
        "rate_limit_rpm": 60,
        "expires_at": None
    }


@pytest.fixture
def mock_request():
    """Create a mock FastAPI request."""
    request = MagicMock()
    request.url.path = "/v1/chat/completions"
    request.method = "POST"
    request.headers = {"content-type": "application/json"}
    request.client.host = "127.0.0.1"
    request.state = MagicMock()
    return request


@pytest.fixture
async def db_session():
    """Provide an in-memory SQLite AsyncSession for testing."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlmodel import SQLModel
    import llm_gateway.models  # ensure models registered
    import llm_gateway.auth.models  # ensure auth models registered
    
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(test_engine, expire_on_commit=False) as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await test_engine.dispose()


@pytest.fixture
async def client(db_session):
    """Async test client with DB session and auth overrides."""
    from llm_gateway.main import app
    from llm_gateway.database import get_session
    from llm_gateway.auth.models import User, Role
    from llm_gateway.routers.admin import current_active_user, require_admin
    import uuid

    admin_user = User(
        id=uuid.uuid4(),
        email="admin@test.local",
        hashed_password="hash",
        is_active=True,
        is_superuser=True,
        role=Role.ADMIN,
        is_verified=True,
    )

    async def _override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[current_active_user] = lambda: admin_user
    app.dependency_overrides[require_admin] = lambda: admin_user

    transport = ASGITransport(app=app)
    cookies = {"csrf_token": "test-csrf-token"}
    async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def admin_token_headers():
    """Headers for authenticated admin requests."""
    return {
        "x-csrf-token": "test-csrf-token",
        "Authorization": "Bearer test-admin-token",
    }
