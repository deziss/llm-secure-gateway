import os
import logging
from typing import Dict, List, Optional
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from cryptography.fernet import Fernet

from ..models import Owner, OwnerType, ProviderKey

logger = logging.getLogger(__name__)


class OwnerService:
    def __init__(self):
        raw_key = os.getenv("ENCRYPTION_KEY")
        if not raw_key:
            raise RuntimeError(
                "ENCRYPTION_KEY environment variable is required but not set. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        self.fernet = Fernet(raw_key.encode() if isinstance(raw_key, str) else raw_key)

    def encrypt(self, data: str) -> str:
        return self.fernet.encrypt(data.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self.fernet.decrypt(token.encode()).decode()

    async def create_owner(
        self,
        session: AsyncSession,
        id: str,
        name: str,
        email: str = None,
        type: OwnerType = OwnerType.USER,
        **kwargs,
    ) -> Owner:
        existing = await session.get(Owner, id)
        if existing:
            raise ValueError(f"Owner with id '{id}' already exists")
        owner = Owner(id=id, name=name, email=email, type=type, **kwargs)
        session.add(owner)
        return owner

    async def get_owner(self, session: AsyncSession, id: str) -> Optional[Owner]:
        return await session.get(Owner, id)

    async def update_owner(self, session: AsyncSession, id: str, updates: Dict) -> Optional[Owner]:
        owner = await self.get_owner(session, id)
        if not owner:
            return None
        for key, value in updates.items():
            if value is not None and hasattr(owner, key):
                setattr(owner, key, value)
        session.add(owner)
        return owner

    async def set_provider_key(self, session: AsyncSession, owner_id: str, provider: str, key: str) -> ProviderKey:
        encrypted = self.encrypt(key)
        result = await session.execute(
            select(ProviderKey).where(ProviderKey.owner_id == owner_id, ProviderKey.provider_id == provider)
        )
        existing = result.scalars().first()
        if existing:
            existing.encrypted_key = encrypted
            session.add(existing)
            return existing

        pk = ProviderKey(owner_id=owner_id, provider_id=provider, encrypted_key=encrypted)
        session.add(pk)
        await session.commit()
        await session.refresh(pk)
        return pk

    async def get_decrypted_key(self, session: AsyncSession, owner_id: str, provider: str) -> Optional[str]:
        result = await session.execute(
            select(ProviderKey).where(ProviderKey.owner_id == owner_id, ProviderKey.provider_id == provider)
        )
        pk = result.scalars().first()
        if pk:
            return self.decrypt(pk.encrypted_key)
        return None

    async def list_owners(
        self, session: AsyncSession, *, skip: int = 0, limit: int = 50
    ) -> List[Owner]:
        query = select(Owner).offset(skip).limit(limit)
        result = await session.execute(query)
        return list(result.scalars().all())

    async def delete_owner(self, session: AsyncSession, id: str) -> None:
        owner = await self.get_owner(session, id)
        if owner:
            await session.delete(owner)


def _make_owner_service() -> OwnerService:
    try:
        return OwnerService()
    except RuntimeError as e:
        logger.error(str(e))
        raise


owner_service = _make_owner_service()


def get_owner_service() -> OwnerService:
    return owner_service
