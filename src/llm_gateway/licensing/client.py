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
    LICENCIA_URL = os.getenv("LICENCIA_URL", "https://licencia.deziss.com")
    LICENCIA_API_KEY = os.getenv("LICENCIA_API_KEY", "")
    LICENCIA_LICENSE_KEY = os.getenv("LICENCIA_LICENSE_KEY", "")
    LICENCIA_LEASE_FILE = os.getenv("LICENCIA_LEASE_FILE", "/tmp/gateway_license_lease.json")

try:
    from ..observability import VERSION
except ImportError:
    VERSION = "0.19.0"

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
        base_url = url or LICENCIA_URL or "https://licencia.deziss.com"
        self.url = base_url.rstrip("/")
        self.api_key = api_key if api_key is not None else LICENCIA_API_KEY
        self.license_key = license_key if license_key is not None else LICENCIA_LICENSE_KEY
        self.lease_file = lease_file or LICENCIA_LEASE_FILE or "/tmp/gateway_license_lease.json"
        if self.license_key and self.api_key:
            self.state = LicenseState(is_community=False, hardware_id=generate_hardware_id())
        else:
            self.state = LicenseState(is_community=True, hardware_id=generate_hardware_id())

    def _mask_key(self, key: str) -> str:
        if not key or len(key) < 12:
            return "COMMUNITY-EDITION"
        return f"{key[:8]}...{key[-4:]}"

    def _apply_community_defaults(self, reason: str = "") -> LicenseState:
        # Show the *real* fingerprint even in Community mode: the Upgrade dialog prints
        # this value as "Hardware fingerprint for activation", and a placeholder there
        # sends operators to activate the wrong seat against the licensing server.
        self.state = LicenseState(
            tier=LicenseTier.COMMUNITY,
            is_valid=True,
            is_community=True,
            key_masked="COMMUNITY-EDITION",
            hardware_id=generate_hardware_id(),
            last_error=reason or None,
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

    async def validate(self, lease_fallback: bool = True) -> LicenseState:
        """Validate license with Licencia server, falling back to local cached lease only on network errors.

        Pass ``lease_fallback=False`` when validating a *new* key (upgrade flow): a cached
        lease for the old key must never make an unverified key look valid.
        """
        if not self.license_key or not self.api_key:
            missing = ", ".join(n for n, v in (("LICENCIA_API_KEY", self.api_key), ("license key", self.license_key)) if not v)
            reason = (
                f"Licensing is not configured on this gateway: {missing} is not set, so the "
                f"licensing server at {self.url} was never contacted."
            )
            logger.warning("%s Running in Community mode.", reason)
            return self._apply_community_defaults(reason)

        hw_id = generate_hardware_id()
        req_payload = LicenciaValidateRequest(
            key=self.license_key,
            hardwareId=hw_id,
            deviceName=platform.node(),
            os=platform.system().lower(),
            appVersion=VERSION,
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
                        return self._apply_community_defaults(
                            f"Licensing server {self.url} rejected this key (valid=false). "
                            "Check the key, its seat activation, and the hardware fingerprint on the provider side."
                        )
                elif 400 <= res.status_code < 500:
                    logger.warning("Licencia rejected key with status %d", res.status_code)
                    self._remove_lease_file()
                    return self._apply_community_defaults(
                        f"Licensing server {self.url} refused the request with HTTP {res.status_code} "
                        "(invalid key or bad/expired API credentials)."
                    )
                elif res.status_code >= 500:
                    logger.warning("Licencia server error %d, attempting offline lease fallback", res.status_code)
                    cached = self._load_lease_cache() if lease_fallback else None
                    if cached:
                        return cached
                    return self._apply_community_defaults(
                        f"Licensing server {self.url} returned HTTP {res.status_code} (server-side error)."
                    )
                else:
                    logger.warning("Licencia returned unexpected status %d", res.status_code)
                    return self._apply_community_defaults(
                        f"Licensing server {self.url} returned unexpected HTTP {res.status_code}."
                    )
        except Exception as exc:
            logger.warning("Licencia API unreachable (%s), attempting offline lease load", exc)
            cached = self._load_lease_cache() if lease_fallback else None
            if cached:
                return cached
            return self._apply_community_defaults(
                f"Could not reach the licensing server at {self.url} ({type(exc).__name__}). "
                "Check LICENCIA_URL, network/DNS from this container, and the provider's status."
            )

        logger.warning("No valid offline lease found. Falling back to Community mode.")
        return self._apply_community_defaults(
            "No offline lease available and the licensing server did not return a valid license."
        )

    async def upgrade(self, new_key: str) -> tuple:
        """Validate a new/upgrade license key strictly online and adopt it on success.

        On failure (invalid key, rejected seat, unreachable server) the previously
        configured key and its state are restored, so a bad attempt never downgrades
        the running tier. Returns ``(upgraded: bool, state: LicenseState)``.
        """
        new_key = (new_key or "").strip()
        if not new_key:
            return False, self.state
        if not self.api_key:
            reason = (
                "No LICENCIA_API_KEY is configured on this gateway, so the key could not be "
                f"checked against the licensing server at {self.url}. Set LICENCIA_API_KEY "
                "(and LICENCIA_URL if it differs from the default), then retry. The license "
                "was NOT contacted, so this is a gateway configuration issue, not a server rejection."
            )
            logger.warning("%s", reason)
            return False, self.state.model_copy(update={
                "last_error": reason,
                "hardware_id": generate_hardware_id(),
            })
        previous_key = self.license_key
        self.license_key = new_key
        state = await self.validate(lease_fallback=False)
        if state.is_valid and not state.is_community:
            logger.info("License upgraded successfully. New tier: %s", state.tier.value)
            return True, state
        self.license_key = previous_key
        restored = await self.validate()
        return False, restored

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
