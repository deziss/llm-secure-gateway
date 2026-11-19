import os
import sys

# Ensure src is on sys.path even when running without PYTHONPATH configured
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import pytest
from llm_gateway.licensing.fingerprint import generate_hardware_id, is_localhost_bypass
from llm_gateway.licensing.models import (
    LicenciaValidateRequest,
    LicenciaValidateResponse,
    LicenciaEntitlements,
    LicenseTier,
)

def test_hardware_fingerprint_deterministic():
    hw1 = generate_hardware_id()
    hw2 = generate_hardware_id()
    assert isinstance(hw1, str)
    assert len(hw1) == 64  # SHA-256 hex string
    assert hw1 == hw2

def test_localhost_bypass():
    assert is_localhost_bypass("localhost") is True
    assert is_localhost_bypass("127.0.0.1") is True
    assert is_localhost_bypass("::1") is True
    assert is_localhost_bypass("  localhost  ") is True
    assert is_localhost_bypass("  127.0.0.1\n") is True
    assert is_localhost_bypass("abc1234567890") is False
    assert is_localhost_bypass(None) is False
    assert is_localhost_bypass(12345) is False
    assert is_localhost_bypass("") is False

def test_licencia_models_serialization():
    req = LicenciaValidateRequest(
        key="LIC-1234-5678-9012-3456",
        hardwareId="test-hw-id",
        deviceName="test-host",
        os="linux",
        appVersion="0.15.1",
    )
    payload = req.model_dump()
    assert payload["key"] == "LIC-1234-5678-9012-3456"
    assert payload["hardwareId"] == "test-hw-id"

    entitlements = LicenciaEntitlements(
        product="llm-secure-gateway",
        tier=LicenseTier.ENTERPRISE,
        features={"semantic_cache": True, "complexity_routing": True, "live_traces": True},
        limits={"max_backends": 0, "max_keys": 0},
    )
    resp = LicenciaValidateResponse(
        valid=True,
        licenseId="lic-uuid-1234",
        type="ONLINE",
        entitlements=entitlements,
        leaseToken="jwt.lease.token",
        leaseExpiresAt="2027-01-01T00:00:00Z",
        checkInInterval=6,
        onlineGracePeriod=168,
    )
    assert resp.valid is True
    assert resp.entitlements.tier == LicenseTier.ENTERPRISE
    assert resp.entitlements.features.get("semantic_cache") is True
