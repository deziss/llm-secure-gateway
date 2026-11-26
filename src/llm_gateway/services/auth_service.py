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
            rate_limit_tpm=getattr(data, "rate_limit_tpm", None),
            max_concurrency=getattr(data, "max_concurrency", None),
            rotate_interval_days=getattr(data, "rotate_interval_days", None),
            rotation_grace_days=max(0, int(getattr(data, "rotation_grace_days", 7) or 7)),
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

    async def rotate_key(self, session: AsyncSession, prefix: str,
                         grace_days: Optional[int] = None) -> tuple[str, APIKey, APIKey] | None:
        """Rotate an API key: issue a replacement and put the old key in a grace window.

        The new key inherits owner, scopes, quotas, budgets and rotation policy.
        The old key stays valid for ``grace_days`` more days (zero-downtime
        overlap), is marked rotated, and both cache entries are handled so the
        switch takes effect promptly. Returns (raw_new_key, new_key, old_key)
        or None when the prefix is unknown. The raw key is shown exactly once.
        """
        result = await session.execute(select(APIKey).where(APIKey.prefix == prefix))
        old = result.scalars().first()
        if not old:
            return None
        if not old.is_active:
            raise ValueError("Key is already revoked")
        if old.rotated_at is not None:
            raise ValueError("Key was already rotated; rotate the current key instead")

        grace = grace_days if grace_days is not None and grace_days >= 0 else max(1, int(old.rotation_grace_days or 7))
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        data = APIKeyCreate(
            owner=old.owner_id,
            scopes=list(old.scopes or []),
            rate_limit_rpm=old.rate_limit_rpm,
            rate_limit_tpm=old.rate_limit_tpm,
            max_concurrency=old.max_concurrency,
            rotate_interval_days=old.rotate_interval_days,
            rotation_grace_days=grace,
        )
        raw_new, new = await self.create_api_key(session, data)
        new.rotated_from = old.prefix
        # A rotated key keeps the rotation policy, not the KEY_EXPIRY_DAYS default,
        # unless no policy exists (then create_api_key's default stands).
        if old.rotate_interval_days:
            new.expires_at = now + timedelta(days=int(old.rotate_interval_days))
        elif old.expires_at is None:
            new.expires_at = None

        # Old key: grace-window expiry (max of remaining life and grace), mark rotated, disable auto-rotation.
        old_grace_until = now + timedelta(days=grace)
        if old.expires_at is None or old.expires_at > old_grace_until:
            old.expires_at = old_grace_until
        old.rotated_at = now
        old.rotate_interval_days = None

        session.add(old)
        session.add(new)
        await session.commit()
        session.refresh(old)
        session.refresh(new)

        # Invalidate the old key's cached validation so the new expiry applies at once.
        _key_cache.invalidate(old.key_hash)

        try:
            import asyncio
            from ..webhooks import notify_key_rotated
            asyncio.get_running_loop().create_task(
                notify_key_rotated(old.prefix, new.prefix, old.owner_id, grace, auto=False)
            )
        except (RuntimeError, Exception):
            pass

        return raw_new, new, old

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
