from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

class LicenseTier(str, Enum):
    COMMUNITY = "community"
    PRO = "pro"
    ENTERPRISE = "enterprise"

class LicenciaValidateRequest(BaseModel):
    key: str
    hardwareId: str
    macAddress: Optional[str] = None
    deviceName: str
    os: str
    appVersion: str

class LicenciaEntitlements(BaseModel):
    product: str = "llm-secure-gateway"
    tier: LicenseTier = LicenseTier.COMMUNITY
    features: Dict[str, bool] = Field(default_factory=dict)
    limits: Dict[str, int] = Field(default_factory=dict)
    metered: Dict[str, int] = Field(default_factory=dict)
    softLimits: List[str] = Field(default_factory=list)

class LicenciaValidateResponse(BaseModel):
    valid: bool
    licenseId: Optional[str] = None
    type: Optional[str] = "ONLINE"
    expiresAt: Optional[str] = None
    product: Optional[str] = "llm-secure-gateway"
    entitlements: LicenciaEntitlements = Field(default_factory=LicenciaEntitlements)
    leaseToken: Optional[str] = None
    leaseExpiresAt: Optional[str] = None
    checkInInterval: int = 6  # hours
    onlineGracePeriod: int = 168  # hours (7 days)
    error: Optional[str] = None

class LicenseState(BaseModel):
    tier: LicenseTier = LicenseTier.COMMUNITY
    is_valid: bool = True
    is_community: bool = True
    key_masked: str = "COMMUNITY-EDITION"
    hardware_id: str = "localhost"
    lease_expires_at: Optional[str] = None
    grace_expires_at: Optional[str] = None
    features: Dict[str, bool] = Field(default_factory=dict)
    limits: Dict[str, int] = Field(default_factory=dict)
    last_validated_at: Optional[str] = None
