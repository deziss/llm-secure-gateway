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
]
