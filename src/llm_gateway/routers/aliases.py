"""Model alias and fallback chain CRUD API."""

from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..models import ModelAlias, FallbackChain
from ..database import get_session
from .admin import require_admin

router = APIRouter(prefix="/admin", tags=["admin-aliases"])


class AliasCreate(BaseModel):
    alias: str
    backend_name: str
    model_name: str
    fallback_chain_id: Optional[int] = None


class ChainCreate(BaseModel):
    name: str
    targets: List[dict]  # [{"backend_name": "...", "model": "...", "translate": false}]


@router.get("/aliases")
async def list_aliases(
    session: AsyncSession = Depends(get_session),
    user=Depends(require_admin),
) -> list:
    result = await session.execute(select(ModelAlias))
    return list(result.scalars().all())


@router.post("/aliases")
async def create_alias(
    data: AliasCreate,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_admin),
) -> ModelAlias:
    existing = await session.execute(select(ModelAlias).where(ModelAlias.alias == data.alias))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail=f"Alias '{data.alias}' already exists")
    alias = ModelAlias(**data.model_dump())
    session.add(alias)
    await session.commit()
    await session.refresh(alias)
    return alias


@router.delete("/aliases/{alias_name}")
async def delete_alias(
    alias_name: str,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_admin),
) -> dict:
    result = await session.execute(select(ModelAlias).where(ModelAlias.alias == alias_name))
    alias = result.scalars().first()
    if not alias:
        raise HTTPException(status_code=404, detail="Alias not found")
    await session.delete(alias)
    await session.commit()
    return {"status": "deleted", "alias": alias_name}


@router.get("/fallback-chains")
async def list_chains(
    session: AsyncSession = Depends(get_session),
    user=Depends(require_admin),
) -> list:
    result = await session.execute(select(FallbackChain))
    return list(result.scalars().all())


@router.post("/fallback-chains")
async def create_chain(
    data: ChainCreate,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_admin),
) -> FallbackChain:
    existing = await session.execute(select(FallbackChain).where(FallbackChain.name == data.name))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail=f"Chain '{data.name}' already exists")
    chain = FallbackChain(name=data.name, targets=data.targets)
    session.add(chain)
    await session.commit()
    await session.refresh(chain)
    return chain


@router.delete("/fallback-chains/{chain_id}")
async def delete_chain(
    chain_id: int,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_admin),
) -> dict:
    result = await session.execute(select(FallbackChain).where(FallbackChain.id == chain_id))
    chain = result.scalars().first()
    if not chain:
        raise HTTPException(status_code=404, detail="Chain not found")
    await session.delete(chain)
    await session.commit()
    return {"status": "deleted", "chain_id": chain_id}
