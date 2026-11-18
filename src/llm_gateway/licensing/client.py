import json
import logging
import os
import platform
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

try:
    from ..config import (
        LICENCIA_URL,
        LICENCIA_API_KEY,
        LICENCIA_LICENSE_KEY,
        LICENCIA_LEASE_FILE,
    )
except ImportError:
    LICENCIA_URL = os.getenv("LICENCIA_URL", "http://172.20.0.11:3000")
    LICENCIA_API_KEY = os.getenv("LICENCIA_API_KEY", "")
    LICENCIA_LICENSE_KEY = os.getenv("LICENCIA_LICENSE_KEY", "")
    LICENCIA_LEASE_FILE = os.getenv("LICENCIA_LEASE_FILE", "/tmp/gateway_license_lease.json")

from .fingerprint import generate_hardware_id
from .models import (
    LicenseState,
    LicenseTier,
    LicenciaValidateRequest,
    LicenciaValidateResponse,
)

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LicenciaClient:
    """Async client managing validation, lease caching, and entitlement queries for Licencia."""

    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        license_key: Optional[str] = None,
        lease_file: Optional[str] = None,
    ):
        base_url = url or LICENCIA_URL or "http://172.20.0.11:3000"
        self.url = base_url.rstrip("/")
        self.api_key = api_key if api_key is not None else LICENCIA_API_KEY
        self.license_key = license_key if license_key is not None else LICENCIA_LICENSE_KEY
        self.lease_file = lease_file or LICENCIA_LEASE_FILE or "/tmp/gateway_license_lease.json"
        if self.license_key and self.api_key:
            self.state = LicenseState(is_community=False)
        else:
            self.state = LicenseState(is_community=True)

    def _mask_key(self, key: str) -> str:
        if not key or len(key) < 12:
            return "COMMUNITY-EDITION"
        return f"{key[:8]}...{key[-4:]}"

    def _apply_community_defaults(self) -> LicenseState:
        self.state = LicenseState(
            tier=LicenseTier.COMMUNITY,
            is_valid=True,
            is_community=True,
            key_masked="COMMUNITY-EDITION",
            hardware_id="localhost",
            features={
                "basic_routing": True,
                "token_rate_limits": True,
                "audit_logs": True,
                "semantic_cache": False,
                "complexity_routing": False,
                "live_traces": False,
            },
            limits={"max_backends": 2, "max_keys": 5},
            last_validated_at=_utcnow_iso(),
        )
        return self.state

    async def validate(self) -> LicenseState:
        """Validate license with Licencia server, falling back to local cached lease only on network errors."""
        if not self.license_key or not self.api_key:
            logger.info("No Licencia credentials configured. Running in Community mode.")
            return self._apply_community_defaults()

        hw_id = generate_hardware_id()
        req_payload = LicenciaValidateRequest(
            key=self.license_key,
            hardwareId=hw_id,
            deviceName=platform.node(),
            os=platform.system().lower(),
            appVersion="0.15.1",
        )

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    f"{self.url}/api/v1/licenses/validate",
                    json=req_payload.model_dump(),
                    headers=headers,
                )
                if res.status_code == 200:
                    data = res.json()
                    validated = LicenciaValidateResponse(**data)
                    if validated.valid:
                        tier_val = (
                            validated.entitlements.tier
                            if isinstance(validated.entitlements.tier, LicenseTier)
                            else LicenseTier(str(validated.entitlements.tier).lower())
                        )
                        grace_expires_at = None
                        if validated.leaseExpiresAt:
                            try:
                                exp_dt = datetime.fromisoformat(validated.leaseExpiresAt.replace("Z", "+00:00"))
                                if exp_dt.tzinfo is None:
                                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                                grace_dt = exp_dt + timedelta(hours=validated.onlineGracePeriod or 168)
                                grace_expires_at = grace_dt.isoformat()
                            except Exception:
                                pass

                        self.state = LicenseState(
                            tier=tier_val,
                            is_valid=True,
                            is_community=False,
                            key_masked=self._mask_key(self.license_key),
                            hardware_id=hw_id,
                            lease_expires_at=validated.leaseExpiresAt,
                            grace_expires_at=grace_expires_at,
                            features=validated.entitlements.features,
                            limits=validated.entitlements.limits,
                            last_validated_at=_utcnow_iso(),
                        )
                        self._save_lease_cache(data)
                        logger.info("Licencia validated successfully. Tier: %s", tier_val.value)
                        return self.state
                    else:
                        logger.warning("Licencia rejected key: valid=False")
                        self._remove_lease_file()
                        return self._apply_community_defaults()
                elif 400 <= res.status_code < 500:
                    logger.warning("Licencia rejected key with status %d", res.status_code)
                    self._remove_lease_file()
                    return self._apply_community_defaults()
                elif res.status_code >= 500:
                    logger.warning("Licencia server error %d, attempting offline lease fallback", res.status_code)
                    cached = self._load_lease_cache()
                    if cached:
                        return cached
                    return self._apply_community_defaults()
                else:
                    logger.warning("Licencia returned unexpected status %d", res.status_code)
                    return self._apply_community_defaults()
        except Exception as exc:
            logger.warning("Licencia API unreachable (%s), attempting offline lease load", exc)
            cached = self._load_lease_cache()
            if cached:
                return cached

        logger.warning("No valid offline lease found. Falling back to Community mode.")
        return self._apply_community_defaults()

    async def deactivate(self) -> bool:
        """Gracefully release license seat on shutdown."""
        if not self.license_key or not self.api_key or self.state.is_community:
            return True
        try:
            hw_id = generate_hardware_id()
            headers = {"Content-Type": "application/json", "x-api-key": self.api_key}
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.post(
                    f"{self.url}/api/v1/licenses/deactivate",
                    json={"key": self.license_key, "hardwareId": hw_id},
                    headers=headers,
                )
                return res.status_code in (200, 204)
        except Exception as exc:
            logger.debug("Licencia deactivation error: %s", exc)
            return False

    def is_feature_enabled(self, feature_name: str) -> bool:
        if self.state.tier == LicenseTier.ENTERPRISE:
            return True
        return bool(self.state.features.get(feature_name, False))

    def get_limit(self, limit_name: str, default: int = 0) -> int:
        """Get tier limit (0 means unlimited)."""
        if self.state.tier == LicenseTier.ENTERPRISE:
            return 0
        return self.state.limits.get(limit_name, default)

    def _remove_lease_file(self) -> None:
        if os.path.exists(self.lease_file):
            try:
                os.remove(self.lease_file)
            except OSError as exc:
                logger.debug("Failed to remove lease file %s: %s", self.lease_file, exc)

    def _save_lease_cache(self, data: dict) -> None:
        try:
            parent = os.path.dirname(self.lease_file)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            with open(self.lease_file, "w") as f:
                json.dump(data, f)
        except Exception as exc:
            logger.debug("Failed to write lease cache: %s", exc)

    def _load_lease_cache(self) -> Optional[LicenseState]:
        if not os.path.exists(self.lease_file):
            return None
        try:
            with open(self.lease_file, "r") as f:
                data = json.load(f)
            validated = LicenciaValidateResponse(**data)
            if validated.valid and validated.leaseExpiresAt:
                exp = datetime.fromisoformat(validated.leaseExpiresAt.replace("Z", "+00:00"))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                grace = timedelta(hours=validated.onlineGracePeriod or 168)
                now = datetime.now(timezone.utc)
                if now <= (exp + grace):
                    tier_val = (
                        validated.entitlements.tier
                        if isinstance(validated.entitlements.tier, LicenseTier)
                        else LicenseTier(str(validated.entitlements.tier).lower())
                    )
                    self.state = LicenseState(
                        tier=tier_val,
                        is_valid=True,
                        is_community=False,
                        key_masked=self._mask_key(self.license_key),
                        hardware_id=generate_hardware_id(),
                        lease_expires_at=validated.leaseExpiresAt,
                        grace_expires_at=(exp + grace).isoformat(),
                        features=validated.entitlements.features,
                        limits=validated.entitlements.limits,
                        last_validated_at=_utcnow_iso(),
                    )
                    logger.info("Loaded active offline lease (valid through grace period)")
                    return self.state
        except Exception as exc:
            logger.debug("Failed reading lease cache: %s", exc)
        return None


_client_instance: Optional[LicenciaClient] = None


def get_licencia_client() -> LicenciaClient:
    global _client_instance
    if _client_instance is None:
        _client_instance = LicenciaClient()
    return _client_instance
