import logging
import os
import pathlib

from contextlib import asynccontextmanager
from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from .middleware import AuthMiddleware, CSRFMiddleware, PolicyMiddleware, SecurityHeadersMiddleware, LoginRateLimitMiddleware, BackpressureMiddleware
from .tracking import IPTrackingMiddleware
from .database import init_db
from .telemetry import setup_telemetry

logging.basicConfig(level=logging.INFO)

_VERSION = "0.8.1"





async def create_default_admin() -> None:
    from sqlalchemy.ext.asyncio import AsyncSession
    from .database import engine
    from .auth.models import User, Role
    from sqlalchemy import select
    import bcrypt
    import uuid

    admin_email = os.environ.get("DEFAULT_ADMIN_EMAIL")
    admin_password = os.environ.get("DEFAULT_ADMIN_PASSWORD")

    if not admin_email or not admin_password:
        logging.getLogger(__name__).warning(
            "DEFAULT_ADMIN_EMAIL or DEFAULT_ADMIN_PASSWORD not set — skipping default admin creation."
        )
        return

    async with AsyncSession(engine) as session:
        result = await session.execute(select(User).where(User.email == admin_email))
        admin_user = result.scalars().first()

        if not admin_user:
            hashed = bcrypt.hashpw(admin_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            new_admin = User(
                id=uuid.uuid4(),
                email=admin_email,
                hashed_password=hashed,
                is_active=True,
                is_superuser=True,
                role=Role.ADMIN,
                is_verified=True,
            )
            session.add(new_admin)
            await session.commit()
            logging.getLogger(__name__).info(f"Created default admin user: {admin_email}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await create_default_admin()

    # Start the background model federation polling loop
    import asyncio
    from .services.federation_service import federation_loop
    fed_task = asyncio.create_task(federation_loop())
    try:
        yield
    finally:
        fed_task.cancel()
        from .proxy_helpers import close_all_clients
        await close_all_clients()

app = FastAPI(
    title="Secure LLM Gateway",
    description="A secure gateway for Ollama/vLLM with API Key and SPIFFE authentication.",
    version=_VERSION,
    lifespan=lifespan,
)

# Setup Telemetry immediately
setup_telemetry(app)

app.add_middleware(SecurityHeadersMiddleware)  # outermost response transform
app.add_middleware(IPTrackingMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(LoginRateLimitMiddleware)
app.add_middleware(BackpressureMiddleware)
app.add_middleware(PolicyMiddleware)
app.add_middleware(AuthMiddleware)

# Auth Routes
from .auth.users import fastapi_users, auth_backend_bearer, auth_backend_cookie
from .auth.schemas import UserRead, UserCreate

app.include_router(
    fastapi_users.get_auth_router(auth_backend_bearer),
    prefix="/auth/jwt",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_auth_router(auth_backend_cookie),
    prefix="/auth/cookie",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)

app.include_router(
    fastapi_users.get_reset_password_router(),
    prefix="/auth",
    tags=["auth"],
)


from .routers import (
    admin_router,
    proxy_router,
    v2_proxy_router,
    backends_router,
    owners_router,
    users_router,
    settings_router,
    bots_router,
    aliases_router,
    spend_router,
    ui_router,
    auth_aux_router,
)

app.include_router(ui_router)
app.include_router(auth_aux_router)
app.include_router(admin_router)
app.include_router(backends_router)
app.include_router(owners_router)
app.include_router(users_router)
app.include_router(settings_router)
app.include_router(bots_router)
app.include_router(aliases_router)
app.include_router(spend_router)

app.mount("/static", StaticFiles(directory=str(pathlib.Path(__file__).resolve().parent / "static")), name="static")

# These MUST be registered before the proxy catch-all /{path:path}
@app.get("/")
async def root() -> dict:
    return {
        "service": "Secure LLM Gateway",
        "version": _VERSION,
        "docs": "/docs",
        "health": "/health",
    }

@app.get("/health")
async def health_check() -> dict:
    """Liveness + basic readiness check: verifies DB is reachable."""
    from sqlalchemy import text
    from .database import _async_session

    db_ok = True
    try:
        async with _async_session() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    status = "ok" if db_ok else "degraded"
    return {"status": status, "service": "llm-gateway", "database": "up" if db_ok else "down"}

@app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
async def favicon_ico() -> Response:
    from fastapi.responses import FileResponse
    logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "logo.png")
    if os.path.exists(logo_path):
        return FileResponse(logo_path, media_type="image/png")
    return Response(status_code=204)

# Proxy catch-all routes last — they match /{path:path}
app.include_router(v2_proxy_router)
app.include_router(proxy_router)


