import logging
import os
import uuid
from typing import AsyncGenerator, Optional

from fastapi import Depends, Request, Response
from fastapi_users import BaseUserManager, UUIDIDMixin

from .models import User
from .db import get_user_db

logger = logging.getLogger(__name__)

SECRET = os.getenv("AUTH_SECRET")
if not SECRET:
    raise RuntimeError("AUTH_SECRET environment variable is required")

class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET
    session = None

    async def create(
        self,
        user_create,
        safe: bool = False,
        request: Optional[Request] = None,
    ) -> User:
        from fastapi import HTTPException, status
        from sqlmodel import select
        from ..models import SystemSetting, InviteCode
        
        # Check if invite is required
        require_invite = os.getenv("REQUIRE_INVITE", "false").lower() == "true"
        invite_record = None
        
        if getattr(self, "session", None):
            setting = await self.session.execute(select(SystemSetting).where(SystemSetting.key == "REQUIRE_INVITE"))
            setting = setting.scalar_one_or_none()
            if setting:
                require_invite = setting.value.lower() == "true"
                
        if require_invite:
            invite_code = getattr(user_create, "invite_code", None)
            if not invite_code:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="An invite code is required to register."
                )
            
            if getattr(self, "session", None):
                result = await self.session.execute(
                    select(InviteCode).where(InviteCode.code == invite_code, InviteCode.is_used == False)
                )
                invite_record = result.scalar_one_or_none()
                
                if not invite_record:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid or expired invite code."
                    )
        
        # Call original create
        created_user = await super().create(user_create, safe=safe, request=request)
        
        # If invite used, mark it as consumed
        if require_invite and invite_record and getattr(self, "session", None):
            invite_record.is_used = True
            invite_record.used_by = created_user.email
            self.session.add(invite_record)
            await self.session.commit()
            
        return created_user

    async def on_after_register(self, user: User, request: Optional[Request] = None) -> None:
        from ..services.email_service import get_email_service
        from datetime import datetime, timezone
        email_service = get_email_service()
        
        logger.info("User %s registered", user.id)
        email_service.send_welcome_email_background(user.email)
        
        # Admin Alert
        email_service.send_admin_alert_background(
            "New User Registered", 
            f"Email: {user.email}\nID: {user.id}\nTime: {datetime.now(timezone.utc)}"
        )

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ) -> None:
        from ..services.email_service import get_email_service
        email_service = get_email_service()

        logger.info("Password reset requested for user %s", user.id)
        
        # Send actual email
        base_url = os.getenv("BASE_URL", "http://localhost:8000")
        email_service.send_reset_password_email_background(user.email, token, base_url)

    async def verify_reset_token(self, token: str) -> Optional[User]:
        try:
            # Determine strategy (usually JWT for fastapi-users default)
            # We access the strategy property from BaseUserManager
            strategy = self.reset_password_token_strategy
            user = await strategy.read_token(token, self)
            return user
        except Exception:
            return None

    async def on_after_request_verify(
        self, user: User, token: str, request: Optional[Request] = None
    ) -> None:
        logger.info("Verification requested for user %s", user.id)

    async def on_after_login(
        self,
        user: User,
        request: Optional[Request] = None,
        response: Optional[Response] = None,
    ) -> None:
        from datetime import datetime, timezone
        logger.info("User %s logged in", user.id)
        user.last_login = datetime.now(timezone.utc).replace(tzinfo=None)
        await self.user_db.update(user, {"last_login": user.last_login})

from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_session

async def get_user_manager(user_db=Depends(get_user_db), session: AsyncSession = Depends(get_session)) -> AsyncGenerator[UserManager, None]:
    manager = UserManager(user_db)
    manager.session = session
    yield manager
