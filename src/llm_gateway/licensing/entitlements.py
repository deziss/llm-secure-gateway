from fastapi import HTTPException
from typing import Optional
from .models import LicenseState, LicenseTier
from .client import get_licencia_client

def check_backend_quota(current_count: int, state: Optional[LicenseState] = None) -> None:
    """Enforce max_backends quota based on active Licencia tier."""
    client_state = state or get_licencia_client().state
    if client_state.tier == LicenseTier.ENTERPRISE:
        return
    max_backends = client_state.limits.get("max_backends", 2)
    if max_backends > 0 and current_count >= max_backends:
        raise HTTPException(
            status_code=402,
            detail=f"Backend limit reached ({max_backends} on {client_state.tier.value} tier). Please upgrade your Licencia plan for additional backends.",
        )

def check_key_quota(current_count: int, state: Optional[LicenseState] = None) -> None:
    """Enforce max_keys quota based on active Licencia tier."""
    client_state = state or get_licencia_client().state
    if client_state.tier == LicenseTier.ENTERPRISE:
        return
    max_keys = client_state.limits.get("max_keys", 5)
    if max_keys > 0 and current_count >= max_keys:
        raise HTTPException(
            status_code=402,
            detail=f"API key limit reached ({max_keys} on {client_state.tier.value} tier). Please upgrade your Licencia plan for additional keys.",
        )

def require_feature(feature_name: str, state: Optional[LicenseState] = None) -> None:
    """Enforce feature entitlement based on active Licencia tier."""
    client_state = state or get_licencia_client().state
    if client_state.tier == LicenseTier.ENTERPRISE:
        return
    if not client_state.features.get(feature_name, False):
        raise HTTPException(
            status_code=403,
            detail=f"Feature '{feature_name}' is not enabled on your {client_state.tier.value} plan. Please upgrade your Licencia license.",
        )
