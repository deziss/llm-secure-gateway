"""Model alias and fallback chain CRUD API."""

from typing import Optional, List
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..models import ModelAlias, FallbackChain
from ..database import get_session
from .admin import require_manager

logger = logging.getLogger(__name__)

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
    user=Depends(require_manager),
) -> list:
    result = await session.execute(select(ModelAlias))
    return list(result.scalars().all())


@router.post("/aliases")
async def create_alias(
    data: AliasCreate,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_manager),
) -> ModelAlias:
    try:
        alias_name = data.alias.strip()
        backend_name = data.backend_name.strip()
        model_name = data.model_name.strip()
        existing = await session.execute(select(ModelAlias).where(ModelAlias.alias == alias_name))
        if existing.scalars().first():
            raise HTTPException(status_code=409, detail=f"Alias '{alias_name}' already exists")

        alias = ModelAlias(
            alias=alias_name,
            backend_name=backend_name,
            model_name=model_name,
            fallback_chain_id=data.fallback_chain_id,
        )
        session.add(alias)
        await session.commit()
        await session.refresh(alias)
        return alias
    except HTTPException:
        await session.rollback()
        raise
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Alias '{data.alias}' already exists")
    except Exception as e:
        await session.rollback()
        logger.error("Error creating alias: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to create alias: {str(e)}")


@router.delete("/aliases/{alias_name}")
async def delete_alias(
    alias_name: str,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_manager),
) -> dict:
    try:
        clean_alias = alias_name.strip()
        result = await session.execute(select(ModelAlias).where(ModelAlias.alias == clean_alias))
        alias = result.scalars().first()
        if not alias:
            raise HTTPException(status_code=404, detail="Alias not found")
        await session.delete(alias)
        await session.commit()
        return {"status": "deleted", "alias": clean_alias}
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        logger.error("Error deleting alias: %s", e)
        raise HTTPException(status_code=500, detail="Failed to delete alias")


@router.get("/fallback-chains")
async def list_chains(
    session: AsyncSession = Depends(get_session),
    user=Depends(require_manager),
) -> list:
    result = await session.execute(select(FallbackChain))
    return list(result.scalars().all())


@router.post("/fallback-chains")
async def create_chain(
    data: ChainCreate,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_manager),
) -> FallbackChain:
    try:
        clean_name = data.name.strip()
        existing = await session.execute(select(FallbackChain).where(FallbackChain.name == clean_name))
        if existing.scalars().first():
            raise HTTPException(status_code=409, detail=f"Chain '{clean_name}' already exists")
        chain = FallbackChain(name=clean_name, targets=data.targets)
        session.add(chain)
        await session.commit()
        await session.refresh(chain)
        return chain
    except HTTPException:
        await session.rollback()
        raise
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Chain '{data.name}' already exists")
    except Exception as e:
        await session.rollback()
        logger.error("Error creating fallback chain: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to create chain: {str(e)}")


@router.delete("/fallback-chains/{chain_id}")
async def delete_chain(
    chain_id: int,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_manager),
) -> dict:
    try:
        result = await session.execute(select(FallbackChain).where(FallbackChain.id == chain_id))
        chain = result.scalars().first()
        if not chain:
            raise HTTPException(status_code=404, detail="Chain not found")
        await session.delete(chain)
        await session.commit()
        return {"status": "deleted", "chain_id": chain_id}
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        logger.error("Error deleting fallback chain: %s", e)
        raise HTTPException(status_code=500, detail="Failed to delete fallback chain")
