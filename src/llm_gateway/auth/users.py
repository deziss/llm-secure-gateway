import uuid
from typing import Optional
from fastapi import Depends, Request
from fastapi_users import FastAPIUsers
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    CookieTransport,
    JWTStrategy,
)
from .models import User
from .manager import get_user_manager
from .manager import SECRET

# Transports
bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")
cookie_transport = CookieTransport(cookie_max_age=3600, cookie_samesite="lax")

# Strategy
def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=SECRET, lifetime_seconds=3600)

# Backends
auth_backend_bearer = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

auth_backend_cookie = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)

# FastAPI Users Instance
fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [auth_backend_bearer, auth_backend_cookie],
)

current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
optional_current_active_user = fastapi_users.current_user(active=True, optional=True)

async def set_ui_user(request: Request, ui_user: Optional[User] = Depends(optional_current_active_user)) -> None:
    # If the user is authenticated via UI cookie, and no API Key has been set by middleware
    if ui_user and getattr(request.state, "user", None) is None:
        request.state.user = ui_user
        request.state.auth_method = "cookie"
