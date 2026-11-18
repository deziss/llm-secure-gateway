import os
import sys
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from llm_gateway.licensing.client import LicenciaClient, get_licencia_client
from llm_gateway.licensing.models import LicenseTier, LicenseState

@pytest.mark.asyncio
async def test_client_community_fallback_when_no_key():
    client = LicenciaClient(url="http://mock-licencia", api_key="", license_key="")
    state = await client.validate()
    assert state.is_valid is True
    assert state.tier == LicenseTier.COMMUNITY
    assert state.is_community is True
    assert client.is_feature_enabled("semantic_cache") is False
    assert client.get_limit("max_backends") == 2

@pytest.mark.asyncio
async def test_client_online_validation_success():
    client = LicenciaClient(
        url="http://mock-licencia",
        api_key="lic_live_test_api_key",
        license_key="LIC-1111-2222-3333-4444",
        lease_file="/tmp/test_gateway_lease.json",
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "valid": True,
        "licenseId": "lic-999",
        "entitlements": {
            "product": "llm-secure-gateway",
            "tier": "enterprise",
            "features": {"semantic_cache": True, "complexity_routing": True},
            "limits": {"max_backends": 0, "max_keys": 0},
        },
        "leaseToken": "jwt-lease-token-content",
        "leaseExpiresAt": "2027-01-01T00:00:00Z",
        "checkInInterval": 6,
        "onlineGracePeriod": 168,
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        state = await client.validate()

        assert state.is_valid is True
        assert state.tier == LicenseTier.ENTERPRISE
        assert state.is_community is False
        assert client.is_feature_enabled("semantic_cache") is True
        assert client.is_feature_enabled("complexity_routing") is True
        assert client.get_limit("max_backends") == 0  # Enterprise unlimited

@pytest.mark.asyncio
async def test_client_deactivate():
    client = LicenciaClient(
        url="http://mock-licencia",
        api_key="lic_live_test_api_key",
        license_key="LIC-1111-2222-3333-4444",
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        success = await client.deactivate()
        assert success is True

@pytest.mark.asyncio
async def test_client_offline_lease_fallback():
    import os
    lease_path = "/tmp/test_offline_lease.json"
    cached_payload = {
        "valid": True,
        "licenseId": "lic-offline-123",
        "entitlements": {
            "product": "llm-secure-gateway",
            "tier": "pro",
            "features": {"audit_logs": True, "phoenix": True},
            "limits": {"max_backends": 10, "max_keys": 50},
        },
        "leaseToken": "offline-token",
        "leaseExpiresAt": "2029-01-01T00:00:00Z",
        "checkInInterval": 6,
        "onlineGracePeriod": 168,
    }
    with open(lease_path, "w") as f:
        json.dump(cached_payload, f)

    try:
        client = LicenciaClient(
            url="http://offline-licencia-unreachable:9999",
            api_key="lic_live_offline",
            license_key="LIC-9999-8888-7777-6666",
            lease_file=lease_path,
        )
        # Network call fails, should fallback to cached lease
        state = await client.validate()
        assert state.is_valid is True
        assert state.tier == LicenseTier.PRO
        assert client.is_feature_enabled("phoenix") is True
        assert client.get_limit("max_backends") == 10
    finally:
        if os.path.exists(lease_path):
            os.remove(lease_path)

def test_singleton_get_licencia_client():
    c1 = get_licencia_client()
    c2 = get_licencia_client()
    assert c1 is c2
    assert isinstance(c1, LicenciaClient)

@pytest.mark.asyncio
async def test_client_deactivate_noop_without_credentials():
    client = LicenciaClient(url="http://mock-licencia", api_key="", license_key="")
    success = await client.deactivate()
    assert success is True

@pytest.mark.asyncio
async def test_client_expired_lease_falls_back_to_community():
    lease_path = "/tmp/test_expired_lease.json"
    expired_payload = {
        "valid": True,
        "licenseId": "lic-expired",
        "entitlements": {
            "product": "llm-secure-gateway",
            "tier": "enterprise",
            "features": {"semantic_cache": True},
            "limits": {"max_backends": 0},
        },
        "leaseToken": "expired-token",
        "leaseExpiresAt": "2020-01-01T00:00:00Z",
        "checkInInterval": 6,
        "onlineGracePeriod": 24,
    }
    with open(lease_path, "w") as f:
        json.dump(expired_payload, f)

    try:
        client = LicenciaClient(
            url="http://unreachable-host:9999",
            api_key="lic_key",
            license_key="LIC-123",
            lease_file=lease_path,
        )
        state = await client.validate()
        assert state.is_valid is True
        assert state.tier == LicenseTier.COMMUNITY
        assert state.is_community is True
    finally:
        if os.path.exists(lease_path):
            os.remove(lease_path)

