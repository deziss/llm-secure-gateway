"""Spend dashboard API — monthly cost tracking, budget overview, and model token pricing."""

from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..database import get_session
from ..models import ModelPricing
from ..services.spend_service import (
    get_spend_summary,
    get_spend_by_model,
    list_model_pricing,
    upsert_model_pricing,
    delete_model_pricing,
    sync_all_catalog_pricing,
)
from .admin import require_manager

router = APIRouter(prefix="/admin", tags=["admin-spend"])


class PricingCreateRequest(BaseModel):
    model: str = Field(..., description="Model identifier, e.g. 'gpt-4o'")
    provider: str = Field(default="*", description="Provider identifier or '*' for any")
    input_cost_per_1m: float = Field(..., ge=0.0, description="Cost in USD per 1,000,000 input tokens")
    output_cost_per_1m: float = Field(..., ge=0.0, description="Cost in USD per 1,000,000 output tokens")
    cached_input_cost_per_1m: Optional[float] = Field(default=None, ge=0.0, description="Optional cost per 1M cached input tokens")
    description: Optional[str] = Field(default=None, description="Optional notes or details")


class PricingUpdateRequest(BaseModel):
    input_cost_per_1m: Optional[float] = Field(default=None, ge=0.0)
    output_cost_per_1m: Optional[float] = Field(default=None, ge=0.0)
    cached_input_cost_per_1m: Optional[float] = Field(default=None, ge=0.0)
    description: Optional[str] = None


class SyncCatalogRequest(BaseModel):
    provider: Optional[str] = Field(default=None, description="Provider to sync or null for all")


@router.get("/spend/summary")
async def spend_summary(
    session: AsyncSession = Depends(get_session),
    user=Depends(require_manager),
) -> list:
    """Monthly spend summary grouped by owner."""
    return await get_spend_summary(session)


@router.get("/spend/by-model")
async def spend_by_model(
    session: AsyncSession = Depends(get_session),
    user=Depends(require_manager),
) -> list:
    """Monthly spend summary grouped by model."""
    return await get_spend_by_model(session)


# ── Model Token Pricing Endpoints ────────────────────────────────────


@router.get("/pricing", response_model=List[ModelPricing])
async def get_pricing_list(
    session: AsyncSession = Depends(get_session),
    user=Depends(require_manager),
) -> List[ModelPricing]:
    """List all model token pricing rules."""
    return await list_model_pricing(session)


@router.post("/pricing", response_model=ModelPricing, status_code=status.HTTP_201_CREATED)
async def create_or_override_pricing(
    req: PricingCreateRequest,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_manager),
) -> ModelPricing:
    """Add or manually override token rate (USD per 1,000,000 tokens) for a model."""
    rule = await upsert_model_pricing(
        session=session,
        model=req.model,
        provider=req.provider,
        input_cost_per_1m=req.input_cost_per_1m,
        output_cost_per_1m=req.output_cost_per_1m,
        cached_input_cost_per_1m=req.cached_input_cost_per_1m,
        description=req.description,
    )
    return rule


@router.put("/pricing/{pricing_id}", response_model=ModelPricing)
async def update_pricing(
    pricing_id: int,
    req: PricingUpdateRequest,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_manager),
) -> ModelPricing:
    """Update token rates for an existing model pricing entry."""
    res = await session.execute(select(ModelPricing).where(ModelPricing.id == pricing_id))
    entry = res.scalars().first()
    if not entry:
        raise HTTPException(status_code=404, detail=f"Pricing rule {pricing_id} not found")

    in_rate = req.input_cost_per_1m if req.input_cost_per_1m is not None else entry.input_cost_per_1m
    out_rate = req.output_cost_per_1m if req.output_cost_per_1m is not None else entry.output_cost_per_1m
    cached_rate = req.cached_input_cost_per_1m if req.cached_input_cost_per_1m is not None else entry.cached_input_cost_per_1m
    desc = req.description if req.description is not None else entry.description

    updated = await upsert_model_pricing(
        session=session,
        model=entry.model,
        provider=entry.provider,
        input_cost_per_1m=in_rate,
        output_cost_per_1m=out_rate,
        cached_input_cost_per_1m=cached_rate,
        description=desc,
    )
    return updated


@router.delete("/pricing/{pricing_id}")
async def delete_pricing(
    pricing_id: int,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_manager),
) -> dict:
    """Delete a model pricing entry."""
    deleted = await delete_model_pricing(session, pricing_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Pricing rule {pricing_id} not found")
    return {"status": "deleted", "id": pricing_id}


@router.post("/pricing/sync-catalog")
async def sync_pricing_catalog(
    req: SyncCatalogRequest = SyncCatalogRequest(),
    session: AsyncSession = Depends(get_session),
    user=Depends(require_manager),
) -> dict:
    """Sync/auto-seed benchmark provider catalog rates into ModelPricing table."""
    seeded = await sync_all_catalog_pricing(session, provider=req.provider)
    return {"status": "synced", "count": len(seeded), "provider": req.provider or "all"}

