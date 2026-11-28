import os
import sys
import types
from unittest.mock import AsyncMock, patch, MagicMock

# Enable running in minimal test environment without full app dependencies
if "llm_gateway.routers" not in sys.modules:
    import pathlib
    routers_dir = str(pathlib.Path(__file__).parent.parent / "src" / "llm_gateway" / "routers")
    pkg = types.ModuleType("llm_gateway.routers")
    pkg.__path__ = [routers_dir]
    sys.modules["llm_gateway.routers"] = pkg

if "llm_gateway.routers.admin" not in sys.modules:
    admin_mod = types.ModuleType("llm_gateway.routers.admin")
    def require_admin():
        return {"email": "admin@example.com"}
    admin_mod.require_admin = require_admin
    sys.modules["llm_gateway.routers.admin"] = admin_mod

import pytest
from fastapi.testclient import TestClient
from llm_gateway.licensing.models import LicenseState, LicenseTier

def test_license_status_endpoint():
    from llm_gateway.routers.settings import router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    from llm_gateway.routers.admin import require_admin
    # Override admin dependency for unit test
    app.dependency_overrides[require_admin] = lambda: {"email": "admin@example.com"}

    with patch("llm_gateway.routers.settings.get_licencia_client") as mock_get:
        mock_client = MagicMock()
        mock_client.state = LicenseState(
            tier=LicenseTier.PRO,
            is_valid=True,
            is_community=False,
            key_masked="LIC-1234...9999",
            hardware_id="test-hardware-id-1234",
            limits={"max_backends": 10, "max_keys": 50},
            features={"audit_logs": True, "semantic_cache": True},
        )
        mock_get.return_value = mock_client

        client = TestClient(app)
        res = client.get("/admin/license/status")
        assert res.status_code == 200
        data = res.json()
        assert data["tier"] == "pro"
        assert data["key_masked"] == "LIC-1234...9999"
        assert data["hardware_id"] == "test-hardware-id-1234"
        assert data["limits"]["max_backends"] == 10

def test_license_refresh_endpoint():
    from llm_gateway.routers.settings import router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    from llm_gateway.routers.admin import require_admin
    app.dependency_overrides[require_admin] = lambda: {"email": "admin@example.com"}

    with patch("llm_gateway.routers.settings.get_licencia_client") as mock_get:
        mock_client = MagicMock()
        mock_client.validate = AsyncMock(return_value=LicenseState(tier=LicenseTier.ENTERPRISE))
        mock_get.return_value = mock_client

        client = TestClient(app)
        res = client.post("/admin/license/refresh")
        assert res.status_code == 200
        assert res.json()["tier"] == "enterprise"
        mock_client.validate.assert_called_once()

def test_license_deactivate_endpoint():
    from llm_gateway.routers.settings import router
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession
    from llm_gateway.database import get_session
    app = FastAPI()
    app.include_router(router)

    from llm_gateway.routers.admin import require_admin
    app.dependency_overrides[require_admin] = lambda: {"email": "admin@example.com"}
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result

    async def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session

    with patch("llm_gateway.routers.settings.get_licencia_client") as mock_get:
        mock_client = MagicMock()
        mock_client.deactivate = AsyncMock(return_value=True)
        mock_get.return_value = mock_client

        client = TestClient(app)
        res = client.post("/admin/license/deactivate")
        assert res.status_code == 200
        assert res.json()["success"] is True
        session.execute.assert_awaited_once()
        mock_client.deactivate.assert_called_once()
