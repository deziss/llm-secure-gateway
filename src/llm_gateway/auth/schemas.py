import uuid
from typing import Optional
from fastapi_users import schemas
from .models import Role

class UserRead(schemas.BaseUser[uuid.UUID]):
    role: Role = Role.VIEWER
    # We keep is_superuser for internal fastapi_users compatibility but we can set it to False
    is_superuser: bool = False

class UserCreate(schemas.BaseUserCreate):
    invite_code: Optional[str] = None
    role: Optional[Role] = Role.VIEWER

class UserUpdate(schemas.BaseUserUpdate):
    role: Optional[Role] = None
