from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

try:
    from sqlalchemy.ext.asyncio import AsyncSession
    from ..models import SystemSetting, _utcnow
    from ..database import get_session
except (ImportError, ModuleNotFoundError):
    AsyncSession = None  # type: ignore
    SystemSetting = None  # type: ignore
    _utcnow = None  # type: ignore
    def get_session():  # type: ignore
        pass

from .admin import require_admin
from ..licensing.client import get_licencia_client

router = APIRouter(prefix="/admin", tags=["admin-settings"])

class SettingUpdate(BaseModel):
    value: str

@router.get("/settings")
async def get_settings(
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin)
) -> list:
    from sqlmodel import select
    result = await session.execute(select(SystemSetting))
    settings = result.scalars().all()
    import os
    # Inject env-var defaults for settings not yet in DB
    _defaults = {
        "REQUIRE_INVITE": os.getenv("REQUIRE_INVITE", "false"),
        "ENABLE_AUDIT_DB": os.getenv("ENABLE_AUDIT_DB", "false"),
        "KEY_EXPIRY_DAYS": os.getenv("KEY_EXPIRY_DAYS", "0"),
        "WEBHOOK_URL": os.getenv("WEBHOOK_URL", ""),
        "ENABLE_FAST_PATH_OPTIMIZATIONS": os.getenv("ENABLE_FAST_PATH_OPTIMIZATIONS", "false"),
        "ENABLE_PROTOCOL_TRANSLATION": os.getenv("ENABLE_PROTOCOL_TRANSLATION", "false"),
        "ENABLE_THINKING_NORMALIZATION": os.getenv("ENABLE_THINKING_NORMALIZATION", "false"),
        "ENABLE_SPEND_TRACKING": os.getenv("ENABLE_SPEND_TRACKING", "true"),
        "ENABLE_BUDGET_ENFORCEMENT": os.getenv("ENABLE_BUDGET_ENFORCEMENT", "true"),
        "ENABLE_EXACT_CACHE": os.getenv("ENABLE_EXACT_CACHE", "false"),
        "ENABLE_SEMANTIC_CACHE": os.getenv("ENABLE_SEMANTIC_CACHE", "false"),
        "SEMANTIC_CACHE_THRESHOLD": os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.85"),
        "CACHE_TTL_SECONDS": os.getenv("CACHE_TTL_SECONDS", "3600"),
        "ENABLE_PII_MASKING": os.getenv("ENABLE_PII_MASKING", "false"),
        "ENABLE_GUARDRAILS": os.getenv("ENABLE_GUARDRAILS", "false"),
        "GUARDRAIL_SENSITIVITY": os.getenv("GUARDRAIL_SENSITIVITY", "medium"),
    }
    existing_keys = {s.key for s in settings}
    for key, default in _defaults.items():
        if key not in existing_keys:
            settings.append(SystemSetting(key=key, value=default))
    return settings

@router.patch("/settings/{key}")
async def update_setting(
    key: str, 
    update: SettingUpdate, 
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin)
) -> SystemSetting:
    from sqlmodel import select
    result = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()
    if not setting:
        setting = SystemSetting(key=key, value=update.value)
        session.add(setting)
    else:
        setting.value = update.value
        setting.updated_at = _utcnow()
        session.add(setting)
    await session.commit()
    await session.refresh(setting)
    return setting

@router.get("/license/status")
async def get_license_status(user = Depends(require_admin)):
    client = get_licencia_client()
    return client.state.model_dump()

@router.post("/license/refresh")
async def refresh_license(user = Depends(require_admin)):
    client = get_licencia_client()
    state = await client.validate()
    return state.model_dump()

@router.post("/license/deactivate")
async def deactivate_license_seat(user = Depends(require_admin)):
    client = get_licencia_client()
    success = await client.deactivate()
    return {"success": success}

