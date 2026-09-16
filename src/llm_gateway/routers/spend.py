"""Spend dashboard API — monthly cost tracking and budget overview."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..services.spend_service import get_spend_summary, get_spend_by_model
from .admin import require_admin

router = APIRouter(prefix="/admin", tags=["admin-spend"])


@router.get("/spend/summary")
async def spend_summary(
    session: AsyncSession = Depends(get_session),
    user=Depends(require_admin),
) -> list:
    """Monthly spend summary grouped by owner."""
    return await get_spend_summary(session)


@router.get("/spend/by-model")
async def spend_by_model(
    session: AsyncSession = Depends(get_session),
    user=Depends(require_admin),
) -> list:
    """Monthly spend summary grouped by model."""
    return await get_spend_by_model(session)
