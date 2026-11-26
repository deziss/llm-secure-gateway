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
    # Self-registration always produces a VIEWER.  The declared default is
    # VIEWER and UserManager.create() overrides any client-supplied value, so
    # posting role=ADMIN here has no effect.  Role changes go through the admin
    # users router (UserUpdate) only.
    role: Optional[Role] = Role.VIEWER

class UserUpdate(schemas.BaseUserUpdate):
    role: Optional[Role] = None
