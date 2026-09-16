from typing import Optional
from datetime import datetime, timezone
from sqlmodel import SQLModel, Field
import uuid
from enum import Enum


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Role(str, Enum):
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    DEVELOPER = "DEVELOPER"
    VIEWER = "VIEWER"

# Database Model
class User(SQLModel, table=True):
    __tablename__ = "user"
    
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: str = Field(index=True, unique=True)
    hashed_password: str
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    is_verified: bool = Field(default=False)
    role: Role = Field(default=Role.VIEWER)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: Optional[datetime] = Field(default=None)
    last_login: Optional[datetime] = Field(default=None)
