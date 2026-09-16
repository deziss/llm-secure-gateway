import secrets
import hashlib
import logging
from typing import Optional
from datetime import datetime, timezone, timedelta
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import APIKey, APIKeyCreate
from ..cache import TTLCache

logger = logging.getLogger(__name__)

# Cache validated API keys for 60s to avoid hashing + DB query per request.
# Trade-off: a revoked key stays valid for up to 60s.  Acceptable for most
# deployments; set TTL lower if instant revocation matters.
_key_cache = TTLCache(ttl_seconds=60.0, max_size=2048)


class AuthService:
    def _hash_key(self, key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    async def create_api_key(self, session: AsyncSession, data: APIKeyCreate) -> tuple[str, APIKey]:
        raw_key = f"sk-gateway-{secrets.token_urlsafe(32)}"
        key_hash = self._hash_key(raw_key)

        # Apply auto-rotation policy if no explicit expiry was provided
        expires_at = data.expires_at
        if expires_at is None:
            try:
                from .config_service import get_config_service
                import os
                config = get_config_service()
                days_str = await config.get_setting(
                    session, "KEY_EXPIRY_DAYS",
                    os.getenv("KEY_EXPIRY_DAYS", "0"),
                )
                days = int(days_str)
                if days > 0:
                    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)
            except Exception as exc:
                logger.warning("Failed to read KEY_EXPIRY_DAYS: %s", exc)

        api_key = APIKey(
            key_hash=key_hash,
            prefix=raw_key[:15] + "...",
            owner_id=data.owner,
            scopes=data.scopes,
            expires_at=expires_at,
            rate_limit_rpm=data.rate_limit_rpm,
        )
        session.add(api_key)
        return raw_key, api_key

    async def validate_key(self, session: AsyncSession, raw_key: str) -> Optional[APIKey]:
        key_hash = self._hash_key(raw_key)

        # Fast path: return cached result
        hit, cached = _key_cache.get(key_hash)
        if hit:
            return cached  # may be None (negative cache)

        # Slow path: DB lookup
        result = await session.execute(select(APIKey).where(APIKey.key_hash == key_hash))
        key = result.scalars().first()

        if not key or not key.is_active:
            _key_cache.set(key_hash, None)
            return None

        if key.expires_at:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if now > key.expires_at:
                _key_cache.set(key_hash, None)
                # Fire expired webhook (non-blocking)
                try:
                    import asyncio
                    from ..webhooks import notify_key_expired
                    asyncio.get_running_loop().create_task(
                        notify_key_expired(key.prefix, key.owner_id)
                    )
                except (RuntimeError, Exception):
                    pass
                return None
            # Warn if expiring within 7 days
            days_left = (key.expires_at - now).days
            if 0 < days_left <= 7:
                try:
                    import asyncio
                    from ..webhooks import notify_key_expiring
                    asyncio.get_running_loop().create_task(
                        notify_key_expiring(key.prefix, key.owner_id, days_left)
                    )
                except (RuntimeError, Exception):
                    pass

        _key_cache.set(key_hash, key)
        return key

    async def revoke_key(self, session: AsyncSession, prefix: str) -> bool:
        result = await session.execute(select(APIKey).where(APIKey.prefix == prefix))
        key = result.scalars().first()

        if key:
            key.is_active = False
            session.add(key)
            await session.commit()
            # Invalidate cache immediately on revoke
            _key_cache.invalidate(key.key_hash)
            return True
        return False


auth_service = AuthService()


def get_auth_service() -> AuthService:
    return auth_service
