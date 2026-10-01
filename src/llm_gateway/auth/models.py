from typing import Optional
from datetime import datetime, timezone
from sqlalchemy import DateTime
from sqlmodel import SQLModel, Field
import uuid
from enum import Enum


# All timestamp columns in this database are `TIMESTAMP WITHOUT TIME ZONE` and
# every value written is naive UTC (see _utcnow below).  Since sqlmodel 0.0.23 a
# bare `datetime` annotation maps to UTCDateTime(timezone=True), which rejects
# naive values outright -- so table fields pin sa_type=DateTime(timezone=False)
# to match the existing schema.  sa_type is used rather than a NaiveDatetime
# annotation because it works on every sqlmodel version this project supports.
# Non-table request/response schemas keep bare `datetime` so API clients may
# still send timezone-aware values.
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
    created_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=False))
    updated_at: Optional[datetime] = Field(default=None, sa_type=DateTime(timezone=False))
    last_login: Optional[datetime] = Field(default=None, sa_type=DateTime(timezone=False))
