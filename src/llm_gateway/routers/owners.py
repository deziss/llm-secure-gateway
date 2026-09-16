import logging

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from ..models import APIKey, APIKeyCreate, Owner, OwnerResponse, ProviderKey, OwnerType, OwnerPermission, LLMBackend
from ..services import AuthService, OwnerService, get_auth_service, get_owner_service
from ..database import get_session
from ..pagination import pagination_params
from .admin import current_active_user, is_admin_or_manager, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-owners"])

class APIKeyResponse(BaseModel):
    api_key: str
    prefix: str
    owner_id: str
    scopes: List[str]
    created_at: datetime
    expires_at: Optional[datetime] = None

@router.post("/keys", response_model=APIKeyResponse)
async def create_api_key(
    key_data: APIKeyCreate,
    service: AuthService = Depends(get_auth_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(current_active_user)
) -> APIKeyResponse:
    if not is_admin_or_manager(user):
        from sqlalchemy import select
        res = await session.execute(select(Owner).where(Owner.id == key_data.owner))
        owner_obj = res.scalars().first()
        if not owner_obj or owner_obj.user_id != str(user.id):
            raise HTTPException(status_code=403, detail="Not authorized for this owner")

    # Enforce max_keys limit
    from sqlalchemy import select as sa_select, func
    owner_check = await session.execute(sa_select(Owner).where(Owner.id == key_data.owner))
    owner_record = owner_check.scalars().first()
    if owner_record:
        active_count_res = await session.execute(
            sa_select(func.count()).select_from(APIKey).where(
                APIKey.owner_id == key_data.owner, APIKey.is_active == True
            )
        )
        active_count = active_count_res.scalar() or 0
        if active_count >= owner_record.max_keys:
            raise HTTPException(
                status_code=400,
                detail=f"Owner '{key_data.owner}' has reached the maximum of {owner_record.max_keys} active API keys. Revoke an existing key first."
            )

    raw_key, api_key = await service.create_api_key(session, key_data)
    await session.commit()
    await session.refresh(api_key)
    return APIKeyResponse(
        api_key=raw_key,
        prefix=api_key.prefix,
        owner_id=api_key.owner_id,
        scopes=api_key.scopes,
        created_at=api_key.created_at,
        expires_at=api_key.expires_at
    )

@router.get("/keys", response_model=List[APIKey])
async def list_api_keys(
    session: AsyncSession = Depends(get_session),
    user = Depends(current_active_user),
    pagination: tuple = Depends(pagination_params),
) -> list:
    skip, limit = pagination
    from sqlalchemy import select
    if is_admin_or_manager(user):
        query = select(APIKey)
    else:
        query = select(APIKey).join(Owner, APIKey.owner_id == Owner.id).where(Owner.user_id == str(user.id))
    result = await session.execute(query.offset(skip).limit(limit))
    return result.scalars().all()

@router.delete("/keys/{prefix}")
async def revoke_api_key(
    prefix: str,
    service: AuthService = Depends(get_auth_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(current_active_user)
) -> dict:
    from sqlalchemy import select
    if not is_admin_or_manager(user):
        res = await session.execute(select(APIKey).where(APIKey.prefix == prefix))
        key = res.scalars().first()
        if not key:
            raise HTTPException(status_code=404, detail="Key not found")
        res_owner = await session.execute(select(Owner).where(Owner.id == key.owner_id))
        owner_obj = res_owner.scalars().first()
        if not owner_obj or owner_obj.user_id != str(user.id):
             raise HTTPException(status_code=403, detail="Not authorized")
             
    success = await service.revoke_key(session, prefix)
    if not success:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"status": "revoked"}

@router.get("/owners/{owner_id}/keys", response_model=List[APIKey])
async def list_owner_keys(
    owner_id: str,
    session: AsyncSession = Depends(get_session),
    user = Depends(current_active_user),
    pagination: tuple = Depends(pagination_params),
) -> list:
    skip, limit = pagination
    from sqlalchemy import select
    if not is_admin_or_manager(user):
        res = await session.execute(select(Owner).where(Owner.id == owner_id))
        owner_obj = res.scalars().first()
        if not owner_obj or owner_obj.user_id != str(user.id):
            raise HTTPException(status_code=403, detail="Not authorized")

    result = await session.execute(
        select(APIKey).where(APIKey.owner_id == owner_id).offset(skip).limit(limit)
    )
    return result.scalars().all()

class ProviderKeyRequest(BaseModel):
    provider: str
    key: str

class OwnerUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    type: Optional[OwnerType] = None
    is_active: Optional[bool] = None
    description: Optional[str] = None
    max_keys: Optional[int] = None

@router.post("/owners", response_model=Owner)
async def create_owner(
    owner: Owner,
    service: OwnerService = Depends(get_owner_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(current_active_user)
) -> Owner:
    try:
        new_owner = await service.create_owner(
            session, 
            id=owner.id, 
            name=owner.name, 
            email=owner.email, 
            type=owner.type,
            user_id=owner.user_id if is_admin_or_manager(user) else str(user.id),
            description=owner.description,
            max_keys=owner.max_keys,
            is_active=owner.is_active,
            block_endpoints=owner.block_endpoints
        )
        await session.commit()
        await session.refresh(new_owner)

        from ..services.email_service import get_email_service
        try:
            get_email_service().send_admin_alert_background(
                "New Owner Added",
                f"Name: {owner.name}\nID: {owner.id}\nType: {owner.type}"
            )
        except Exception as e:
            logger.warning("Failed to send owner alert: %s", e)
        return new_owner
    except ValueError as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        await session.rollback()
        logger.error("Error creating owner: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error while creating owner")

@router.patch("/owners/{owner_id}", response_model=Owner)
async def update_owner(
    owner_id: str,
    update: OwnerUpdate,
    service: OwnerService = Depends(get_owner_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(current_active_user)
) -> Owner:
    if not is_admin_or_manager(user):
        from sqlalchemy import select
        res = await session.execute(select(Owner).where(Owner.id == owner_id))
        owner_obj = res.scalars().first()
        if not owner_obj or owner_obj.user_id != str(user.id):
            raise HTTPException(status_code=403, detail="Not authorized")
    updates = update.dict(exclude_unset=True)
    owner = await service.update_owner(session, owner_id, updates)
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")
    await session.commit()
    await session.refresh(owner)
    return owner

@router.post("/owners/{owner_id}/keys", response_model=ProviderKey)
async def set_provider_key(
    owner_id: str,
    req: ProviderKeyRequest,
    service: OwnerService = Depends(get_owner_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(current_active_user)
) -> ProviderKey:
    if not is_admin_or_manager(user):
        from sqlalchemy import select
        res = await session.execute(select(Owner).where(Owner.id == owner_id))
        owner_obj = res.scalars().first()
        if not owner_obj or owner_obj.user_id != str(user.id):
            raise HTTPException(status_code=403, detail="Not authorized")
    new_key = await service.set_provider_key(session, owner_id, req.provider, req.key)
    await session.commit()
    await session.refresh(new_key)
    return new_key

@router.get("/owners", response_model=List[OwnerResponse])
async def list_owners(
    service: OwnerService = Depends(get_owner_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(current_active_user),
    pagination: tuple = Depends(pagination_params),
) -> list:
    skip, limit = pagination
    if is_admin_or_manager(user):
        return await service.list_owners(session, skip=skip, limit=limit)
    from sqlalchemy import select
    result = await session.execute(
        select(Owner).where(Owner.user_id == str(user.id)).offset(skip).limit(limit)
    )
    return list(result.scalars().all())

@router.delete("/owners/{owner_id}")
async def delete_owner(
    owner_id: str,
    service: OwnerService = Depends(get_owner_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(current_active_user)
) -> dict:
    if not is_admin_or_manager(user):
        from sqlalchemy import select
        res = await session.execute(select(Owner).where(Owner.id == owner_id))
        owner_obj = res.scalars().first()
        if not owner_obj or owner_obj.user_id != str(user.id):
            raise HTTPException(status_code=403, detail="Not authorized to delete.")
    await service.delete_owner(session, owner_id)
    await session.commit()
    return {"status": "deleted"}

class OwnerPermissionCreate(BaseModel):
    backend_name: str
    allowed_models: List[str] = ["*"]
    allowed_endpoints: List[str] = ["*"]

@router.post("/owners/{owner_id}/permissions", response_model=OwnerPermission)
async def create_owner_permission(
    owner_id: str,
    perm_data: OwnerPermissionCreate,
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin)
) -> OwnerPermission:
    from sqlalchemy import select
    res = await session.execute(select(LLMBackend).where(LLMBackend.name == perm_data.backend_name))
    if not res.scalars().first():
        raise HTTPException(status_code=404, detail="Backend not found")
    owner_res = await session.execute(select(Owner).where(Owner.id == owner_id))
    owner = owner_res.scalars().first()
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")
    # allowed_endpoints is no longer enforced at the owner-permission level.
    # Endpoint-type auth is handled by API key scopes in PolicyMiddleware.
    new_perm = OwnerPermission(
        owner_id=owner_id,
        backend_name=perm_data.backend_name,
        allowed_models=perm_data.allowed_models,
        allowed_endpoints=["*"],
    )
    session.add(new_perm)
    await session.commit()
    await session.refresh(new_perm)
    return new_perm

@router.get("/owners/{owner_id}/permissions", response_model=List[OwnerPermission])
async def list_owner_permissions(
    owner_id: str,
    session: AsyncSession = Depends(get_session),
    user = Depends(current_active_user),
    pagination: tuple = Depends(pagination_params),
) -> list:
    skip, limit = pagination
    if not is_admin_or_manager(user):
        from sqlalchemy import select
        res = await session.execute(select(Owner).where(Owner.id == owner_id))
        owner_obj = res.scalars().first()
        if not owner_obj or owner_obj.user_id != str(user.id):
            raise HTTPException(status_code=403, detail="Not authorized")
    from sqlalchemy import select
    result = await session.execute(
        select(OwnerPermission).where(OwnerPermission.owner_id == owner_id).offset(skip).limit(limit)
    )
    return result.scalars().all()

@router.delete("/owners/{owner_id}/permissions/{permission_id}")
async def delete_owner_permission(
    owner_id: str,
    permission_id: int,
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin)
) -> dict:
    from sqlalchemy import select
    result = await session.execute(select(OwnerPermission).where(
        OwnerPermission.id == permission_id,
        OwnerPermission.owner_id == owner_id
    ))
    perm = result.scalars().first()
    if not perm:
        raise HTTPException(status_code=404, detail="Permission not found")
    await session.delete(perm)
    await session.commit()
    return {"status": "deleted"}
