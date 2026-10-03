from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from pydantic import BaseModel
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from ..auth.models import Role, User
from ..models import InviteCode
from ..database import get_session
from ..pagination import pagination_params
from .admin import require_admin, require_manager, get_user_manager

router = APIRouter(prefix="/admin", tags=["admin-users"])

class UserResponse(BaseModel):
    id: str
    email: str
    is_active: bool
    is_superuser: bool
    is_verified: bool
    role: Optional[Role] = None
    created_at: str
    last_login: Optional[str] = None

class UserUpdate(BaseModel):
    is_superuser: Optional[bool] = None
    is_active: Optional[bool] = None
    role: Optional[Role] = None

class AdminUserCreate(BaseModel):
    email: str
    password: str
    role: Role
    is_active: bool = True
    is_superuser: bool = False

@router.get("/users", response_model=List[UserResponse])
async def list_users(
    session: AsyncSession = Depends(get_session),
    user = Depends(require_manager),
    pagination: tuple = Depends(pagination_params),
) -> list:
    skip, limit = pagination
    from sqlalchemy import select
    result = await session.execute(select(User).offset(skip).limit(limit))
    users = result.scalars().all()
    return [
        UserResponse(
            id=str(u.id),
            email=u.email,
            is_active=u.is_active,
            is_superuser=u.is_superuser,
            is_verified=u.is_verified,
            role=u.role,
            created_at=u.created_at.isoformat() if u.created_at else None,
            last_login=u.last_login.isoformat() if u.last_login else None
        ) 
        for u in users
    ]

@router.post("/users", response_model=UserResponse)
async def create_user_admin(
    user_data: AdminUserCreate,
    session: AsyncSession = Depends(get_session),
    user_manager = Depends(get_user_manager),
    user = Depends(require_admin)
) -> UserResponse:
    from sqlalchemy import select
    res = await session.execute(select(User).where(User.email == user_data.email))
    if res.scalars().first():
        raise HTTPException(status_code=400, detail="User with this email already exists")
    
    hashed_password = user_manager.password_helper.hash(user_data.password)
    new_user = User(
        id=uuid.uuid4(),
        email=user_data.email,
        hashed_password=hashed_password,
        is_active=user_data.is_active,
        is_superuser=user_data.is_superuser,
        role=user_data.role,
        is_verified=True
    )
    session.add(new_user)
    await session.commit()
    await session.refresh(new_user)
    
    return UserResponse(
        id=str(new_user.id),
        email=new_user.email,
        is_active=new_user.is_active,
        is_superuser=new_user.is_superuser,
        is_verified=new_user.is_verified,
        role=new_user.role,
        created_at=new_user.created_at.isoformat() if new_user.created_at else None,
        last_login=None
    )

def _role_of(u) -> str:
    return (u.role.value if hasattr(u.role, "value") else str(u.role or "")).upper()


def _is_admin(u) -> bool:
    return bool(u.is_superuser) or _role_of(u) == Role.ADMIN.value


def _check_user_update_allowed(actor, target, update: "UserUpdate") -> None:
    """Who may change what on PATCH /admin/users/{id}.

    The route only requires MANAGER, and it used to write role,
    is_superuser and is_active with no further check, so a manager could
    promote themselves (or anyone) to ADMIN/superuser or deactivate the
    admins. Now:
      - nobody changes their own role, superuser flag or active state
        (prevents locking yourself out, and self-promotion);
      - only admins grant or revoke superuser;
      - only admins edit an admin or superuser account;
      - managers may set role only to DEVELOPER or VIEWER, and toggle
        is_active, on DEVELOPER/VIEWER accounts.
    """
    if str(actor.id) == str(target.id) and (
        update.role is not None or update.is_superuser is not None or update.is_active is not None
    ):
        raise HTTPException(status_code=403, detail="You cannot change your own role, superuser flag or active state")
    if _is_admin(actor):
        return
    if update.is_superuser is not None:
        raise HTTPException(status_code=403, detail="Only admins can change the superuser flag")
    if _is_admin(target):
        raise HTTPException(status_code=403, detail="Only admins can modify an admin account")
    if _role_of(target) == Role.MANAGER.value:
        raise HTTPException(status_code=403, detail="Only admins can modify a manager account")
    if update.role is not None and (update.role.value if hasattr(update.role, "value") else str(update.role)).upper() not in (Role.DEVELOPER.value, Role.VIEWER.value):
        raise HTTPException(status_code=403, detail="Managers can only assign the DEVELOPER or VIEWER role")


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    update: UserUpdate,
    session: AsyncSession = Depends(get_session),
    user = Depends(require_manager)
) -> dict:
    from sqlalchemy import select
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")
    result = await session.execute(select(User).where(User.id == user_uuid))
    db_user = result.scalars().first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    _check_user_update_allowed(user, db_user, update)
    if update.is_superuser is not None:
        db_user.is_superuser = update.is_superuser
    if update.is_active is not None:
        db_user.is_active = update.is_active
    if hasattr(update, 'role') and update.role is not None:
        db_user.role = update.role
    session.add(db_user)
    await session.commit()
    return {"status": "updated"}

@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin)
) -> dict:
    from sqlalchemy import select
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")
    result = await session.execute(select(User).where(User.id == user_uuid))
    db_user = result.scalars().first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    await session.delete(db_user)
    await session.commit()
    return {"status": "deleted"}

class UserPasswordReset(BaseModel):
    password: str

@router.post("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: str,
    reset: UserPasswordReset,
    session: AsyncSession = Depends(get_session),
    user_manager = Depends(get_user_manager),
    user = Depends(require_admin)
) -> dict:
    from sqlalchemy import select
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")
    result = await session.execute(select(User).where(User.id == user_uuid))
    db_user = result.scalars().first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    hashed_password = user_manager.password_helper.hash(reset.password)
    db_user.hashed_password = hashed_password
    session.add(db_user)
    await session.commit()
    return {"status": "password_reset"}

@router.get("/invites")
async def list_invites(
    session: AsyncSession = Depends(get_session),
    user = Depends(require_manager),
    pagination: tuple = Depends(pagination_params),
) -> list:
    skip, limit = pagination
    from sqlalchemy import select
    result = await session.execute(
        select(InviteCode).order_by(InviteCode.created_at.desc()).offset(skip).limit(limit)
    )
    return result.scalars().all()

@router.post("/invites")
async def create_invite(
    user = Depends(require_manager),
    session: AsyncSession = Depends(get_session)
) -> InviteCode:
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    code = ''.join(secrets.choice(alphabet) for i in range(12))
    new_invite = InviteCode(code=code, created_by=str(user.id))
    session.add(new_invite)
    await session.commit()
    await session.refresh(new_invite)
    return new_invite

@router.delete("/invites/{code}")
async def delete_invite(
    code: str,
    session: AsyncSession = Depends(get_session),
    user = Depends(require_manager)
) -> dict:
    from sqlalchemy import select
    result = await session.execute(select(InviteCode).where(InviteCode.code == code))
    invite = result.scalars().first()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    await session.delete(invite)
    await session.commit()
    return {"status": "deleted"}
