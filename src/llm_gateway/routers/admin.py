from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel

from ..auth.users import current_active_user
from ..auth.models import Role, User
from ..auth.manager import get_user_manager
from ..services import ConfigService, get_config_service
from ..database import get_session
from ..pagination import pagination_params

def require_admin(user = Depends(current_active_user)) -> User:
    role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
    if not user.is_superuser and role_val != Role.ADMIN.value:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user

def require_manager(user = Depends(current_active_user)) -> User:
    role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
    if not user.is_superuser and role_val not in [Role.ADMIN.value, Role.MANAGER.value]:
        raise HTTPException(status_code=403, detail="Manager or Admin privileges required")
    return user

def is_admin_or_manager(user) -> bool:
    role_val = user.role.value if hasattr(user.role, "value") else user.role
    return user.is_superuser or role_val in [Role.ADMIN.value, Role.MANAGER.value]

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(current_active_user)]
)

@router.get("/metrics")
async def get_system_metrics(
    service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin)
) -> dict:
    from ..tracking import get_active_ips, get_traffic_history
    from sqlalchemy import func, select
    from ..auth.models import User

    active_ips = get_active_ips()
    backends = await service.list_backends(session)
    user_count_res = await session.execute(select(func.count(User.id)))
    user_count = user_count_res.scalar()

    result = {
        "active_ips": active_ips,
        "total_active_ips": len(active_ips),
        "total_backends": len(backends),
        "total_users": user_count,
        "traffic": get_traffic_history(15),
    }
    
    is_mc_enabled = await service.get_setting(session, "ENABLE_MISSION_CONTROL", "false")
    if str(is_mc_enabled).lower() == "true":
        import psutil
        import httpx
        import asyncio
        
        active_backends = 0
        total_models = 0
        
        async def check_backend(b):
            nonlocal active_backends, total_models
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    r = await client.get(f"{b.base_url.rstrip('/')}/")
                    active_backends += 1
            except (httpx.RequestError, httpx.TimeoutException):
                pass
            total_models += len(b.models or [])

        await asyncio.gather(*(check_backend(b) for b in backends))
        
        cpu_usage = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        
        result["active_backends"] = active_backends
        result["total_models_available"] = total_models
        result["system"] = {
            "cpu_percent": cpu_usage,
            "memory_percent": memory.percent
        }
    
    return result

@router.get("/metrics/stream")
async def stream_metrics(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin),
) -> StreamingResponse:
    """Server-Sent Events stream for dashboard metrics.

    Pushes JSON metrics every 3 seconds. The client uses EventSource
    instead of polling, saving ~90% of HTTP overhead.

    The session above is the one FastAPI already created for the
    authentication dependency chain (require_admin -> current_active_user ->
    get_user_db -> get_session); declaring it here just gives us a handle on
    it.  Dependencies declared with `yield` are not torn down until the
    response completes, and an SSE response only completes when the client
    disconnects -- so without the explicit close below, every open dashboard
    tab pins one pooled DB connection for as long as it stays open.
    """
    import asyncio
    import json

    await session.close()
    from ..tracking import get_active_ips, get_traffic_history
    from ..proxy_helpers import get_circuit_breaker

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            data = {
                "active_ips": get_active_ips(),
                "total_active_ips": len(get_active_ips()),
                "traffic": get_traffic_history(15),
                "circuits": get_circuit_breaker().get_status(),
            }
            yield f"data: {json.dumps(data)}\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/models")
async def list_all_models(
    service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(current_active_user),
    pagination: tuple = Depends(pagination_params),
) -> dict:
    skip, limit = pagination
    backends = await service.list_backends(session)
    models = []
    for b in backends:
        for model_name in (b.models or []):
            models.append({
                "model": model_name,
                "backend": b.name,
                "backend_type": b.backend_type.value,
                "base_url": b.base_url,
            })
    total = len(models)
    models = models[skip : skip + limit]
    return {"models": models, "total": total}
