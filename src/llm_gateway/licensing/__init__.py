"""Licencia licensing integration package for LLM Secure Gateway."""

from .fingerprint import generate_hardware_id, is_localhost_bypass
from .models import (
    LicenseTier,
    LicenciaValidateRequest,
    LicenciaValidateResponse,
    LicenciaEntitlements,
    LicenseState,
)
from .client import LicenciaClient, get_licencia_client
from .entitlements import check_backend_quota, check_key_quota, require_feature

__all__ = [
    "generate_hardware_id",
    "is_localhost_bypass",
    "LicenseTier",
    "LicenciaValidateRequest",
    "LicenciaValidateResponse",
    "LicenciaEntitlements",
    "LicenseState",
    "LicenciaClient",
    "get_licencia_client",
    "check_backend_quota",
    "check_key_quota",
    "require_feature",
]
