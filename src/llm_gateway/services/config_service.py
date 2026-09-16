from typing import Dict, List, Optional
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import LLMBackend, SystemSetting
from ..cache import TTLCache

# Settings change at most a few times per day — 30s TTL avoids 2-3 DB
# queries per proxy request while still picking up changes quickly.
_settings_cache = TTLCache(ttl_seconds=30.0, max_size=128)

# Backend list is read on every proxy call.  10s TTL keeps it fresh enough
# for admin changes while eliminating the per-request full-table scan.
_backends_cache = TTLCache(ttl_seconds=10.0, max_size=8)


class ConfigService:
    async def register_backend(self, session: AsyncSession, backend: LLMBackend) -> LLMBackend:
        session.add(backend)
        _backends_cache.clear()  # bust cache on mutation
        return backend

    async def get_backend(self, session: AsyncSession, name: str) -> Optional[LLMBackend]:
        result = await session.execute(select(LLMBackend).where(LLMBackend.name == name))
        return result.scalars().first()

    async def list_backends(
        self, session: AsyncSession, *, skip: int = 0, limit: int = 50
    ) -> List[LLMBackend]:
        cache_key = f"backends:{skip}:{limit}"
        hit, cached = _backends_cache.get(cache_key)
        if hit:
            return cached

        query = select(LLMBackend).offset(skip).limit(limit)
        result = await session.execute(query)
        backends = list(result.scalars().all())
        _backends_cache.set(cache_key, backends)
        return backends

    async def get_setting(self, session: AsyncSession, key: str, default: str = "") -> str:
        hit, cached = _settings_cache.get(key)
        if hit:
            return cached

        result = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
        setting = result.scalars().first()
        value = setting.value if setting else default
        _settings_cache.set(key, value)
        return value

    async def update_backend(self, session: AsyncSession, name: str, updates: Dict) -> Optional[LLMBackend]:
        backend = await self.get_backend(session, name)
        if not backend:
            return None
        for key, value in updates.items():
            if value is not None and hasattr(backend, key):
                setattr(backend, key, value)
        session.add(backend)
        _backends_cache.clear()  # bust cache on mutation
        return backend

    async def delete_backend(self, session: AsyncSession, name: str) -> bool:
        backend = await self.get_backend(session, name)
        if backend:
            await session.delete(backend)
            _backends_cache.clear()
            return True
        return False


config_service = ConfigService()


def get_config_service() -> ConfigService:
    return config_service
