import os
import sys
import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from llm_gateway.licensing.models import LicenseState, LicenseTier
from llm_gateway.licensing.entitlements import check_backend_quota, check_key_quota, require_feature

def test_backend_quota_enforcement():
    community_state = LicenseState(
        tier=LicenseTier.COMMUNITY,
        limits={"max_backends": 2},
    )
    # Allowed at 1 backend
    check_backend_quota(current_count=1, state=community_state)

    # Blocked at 2 backends
    with pytest.raises(HTTPException) as exc:
        check_backend_quota(current_count=2, state=community_state)
    assert exc.value.status_code == 402
    assert "upgrade your Licencia plan" in exc.value.detail

def test_key_quota_enforcement():
    community_state = LicenseState(
        tier=LicenseTier.COMMUNITY,
        limits={"max_keys": 5},
    )
    # Allowed at 4 keys
    check_key_quota(current_count=4, state=community_state)

    # Blocked at 5 keys
    with pytest.raises(HTTPException) as exc:
        check_key_quota(current_count=5, state=community_state)
    assert exc.value.status_code == 402

def test_enterprise_unlimited():
    enterprise_state = LicenseState(
        tier=LicenseTier.ENTERPRISE,
        limits={"max_backends": 0, "max_keys": 0},
    )
    # Unlimited allows any count
    check_backend_quota(current_count=100, state=enterprise_state)
    check_key_quota(current_count=5000, state=enterprise_state)

def test_require_feature_guard():
    pro_state = LicenseState(
        tier=LicenseTier.PRO,
        features={"audit_logs": True, "phoenix": True, "semantic_cache": False},
    )
    require_feature("audit_logs", state=pro_state)

    with pytest.raises(HTTPException) as exc:
        require_feature("semantic_cache", state=pro_state)
    assert exc.value.status_code == 403
    assert "semantic_cache" in exc.value.detail

    enterprise_state = LicenseState(tier=LicenseTier.ENTERPRISE)
    require_feature("semantic_cache", state=enterprise_state)
