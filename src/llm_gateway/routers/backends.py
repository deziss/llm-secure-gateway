import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ..models import LLMBackend
from ..services import ConfigService, get_config_service
from ..database import get_session
from ..pagination import pagination_params
from .admin import require_admin, current_active_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/backends", tags=["admin-backends"])

class BackendUpdate(BaseModel):
    base_url: Optional[str] = None
    fallback_urls: Optional[List[str]] = None
    api_key: Optional[str] = None
    models: Optional[List[str]] = None
    allowed_endpoints: Optional[List[str]] = None

@router.post("", response_model=LLMBackend)
async def register_backend(
    backend: LLMBackend,
    service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin)
) -> LLMBackend:
    try:
        new_backend = await service.register_backend(session, backend)
        await session.commit()
        await session.refresh(new_backend)
        # Email Alert
        from ..services.email_service import get_email_service
        try:
            get_email_service().send_admin_alert_background(
                "New Backend Added",
                f"Name: {backend.name}\nType: {backend.backend_type}\nURL: {backend.base_url}"
            )
        except Exception as e:
            logger.warning("Failed to send backend alert: %s", e)
        return new_backend
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"Backend with name {backend.name} already exists")
    except Exception as e:
        logger.error("Error registering backend %s: %s", backend.name, e)
        raise HTTPException(status_code=500, detail="Error registering backend")

@router.get("", response_model=List[LLMBackend])
async def list_backends(
    service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(current_active_user),
    pagination: tuple = Depends(pagination_params),
) -> list:
    skip, limit = pagination
    return await service.list_backends(session, skip=skip, limit=limit)

@router.patch("/{name}", response_model=LLMBackend)
async def update_backend(
    name: str,
    update: BackendUpdate,
    service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin)
) -> LLMBackend:
    updates = update.dict(exclude_unset=True)
    backend = await service.update_backend(session, name, updates)
    if not backend:
        raise HTTPException(status_code=404, detail="Backend not found")
    await session.commit()
    await session.refresh(backend)
    return backend

@router.delete("/{name}")
async def delete_backend(
    name: str,
    service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin)
) -> dict:
    success = await service.delete_backend(session, name)
    if not success:
        raise HTTPException(status_code=404, detail="Backend not found")
    await session.commit()
    return {"status": "deleted"}

@router.get("/{name}/health")
async def backend_health(
    name: str,
    service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin)
) -> dict:
    import httpx
    backend = await service.get_backend(session, name)
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend '{name}' not found")

    url = f"{backend.base_url.rstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for path in ["/api/tags", "/v1/models", "/health", "/"]:
                try:
                    r = await client.get(f"{url}{path}")
                    if r.status_code < 500:
                        return {
                            "backend": name,
                            "status": "healthy",
                            "base_url": backend.base_url,
                            "backend_type": backend.backend_type.value,
                            "checked_path": path,
                            "http_status": r.status_code,
                            "models": backend.models,
                        }
                except Exception:
                    continue
    except Exception:
        pass

    return {
        "backend": name,
        "status": "unreachable",
        "base_url": backend.base_url,
        "backend_type": backend.backend_type.value,
        "models": backend.models,
    }

@router.post("/{name}/sync-models")
async def sync_backend_models(
    name: str,
    service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin)
) -> dict:
    import httpx
    backend = await service.get_backend(session, name)
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend '{name}' not found")

    url = backend.base_url.rstrip('/')
    models_found = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            btype = backend.backend_type.value
            if btype == "ollama":
                r = await client.get(f"{url}/api/tags")
                if r.status_code == 200:
                    data = r.json()
                    models_found = [m["name"] for m in data.get("models", [])]
            elif btype in ("openai", "vllm", "groq", "anthropic", "google", "custom"):
                headers = {}
                if backend.api_key:
                    headers["Authorization"] = f"Bearer {backend.api_key}"
                r = await client.get(f"{url}/v1/models", headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    models_found = [m["id"] for m in data.get("data", [])]
    except Exception as e:
        logger.error("Failed to reach backend %s: %s", name, e)
        raise HTTPException(status_code=502, detail="Backend unreachable")

    if models_found:
        await service.update_backend(session, name, {"models": models_found})
        await session.commit()
        return {"backend": name, "synced": True, "models": models_found, "count": len(models_found)}
    else:
        return {"backend": name, "synced": False, "models": backend.models, "message": "Could not fetch models from backend"}

class ModelNameRequest(BaseModel):
    name: str

@router.post("/{name}/pull")
async def pull_backend_model(
    name: str,
    req: ModelNameRequest,
    service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin)
) -> StreamingResponse:
    import httpx
    import json
    from fastapi.responses import StreamingResponse
    backend = await service.get_backend(session, name)
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend '{name}' not found")

    url = backend.base_url.rstrip('/')
    btype = backend.backend_type.value if hasattr(backend.backend_type, 'value') else backend.backend_type
    if btype != "ollama":
        raise HTTPException(status_code=400, detail="Pulling models is only supported for Ollama backends")

    async def stream_pull():
        async with httpx.AsyncClient(timeout=300.0) as client:
            headers = {}
            if backend.api_key:
                 headers["Authorization"] = f"Bearer {backend.api_key.get_secret_value() if hasattr(backend.api_key, 'get_secret_value') else backend.api_key}"
            try:
                async with client.stream("POST", f"{url}/api/pull", json={"name": req.name, "stream": True}, headers=headers) as response:
                    if response.status_code >= 400:
                        err_bytes = await response.aread()
                        err_msg = err_bytes.decode("utf-8", errors="ignore").strip() or "Backend returned an error"
                        logger.warning("Backend pull failed (%d): %s", response.status_code, err_msg)
                        yield json.dumps({"error": err_msg}) + "\n"
                        return
                    async for chunk in response.aiter_text():
                        yield chunk
            except Exception as e:
                yield json.dumps({"error": "Backend unreachable"}) + "\n"

    return StreamingResponse(stream_pull(), media_type="application/x-ndjson")

@router.delete("/{name}/models/{model_name:path}")
async def delete_backend_model(
    name: str,
    model_name: str,
    service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin)
) -> dict:
    import httpx
    backend = await service.get_backend(session, name)
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend '{name}' not found")

    url = backend.base_url.rstrip('/')
    btype = backend.backend_type.value if hasattr(backend.backend_type, 'value') else backend.backend_type
    if btype != "ollama":
        raise HTTPException(status_code=400, detail="Deleting models is only supported for Ollama backends")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {}
            if backend.api_key:
                 headers["Authorization"] = f"Bearer {backend.api_key.get_secret_value() if hasattr(backend.api_key, 'get_secret_value') else backend.api_key}"
            r = await client.request("DELETE", f"{url}/api/delete", json={"name": model_name}, headers=headers)
            if r.status_code >= 400:
                raise HTTPException(status_code=r.status_code, detail="Backend returned an error")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to reach backend %s: %s", name, e)
        raise HTTPException(status_code=502, detail="Backend unreachable")

    if backend.models and model_name in backend.models:
        new_models = [m for m in backend.models if m != model_name]
        await service.update_backend(session, name, {"models": new_models})
        await session.commit()

    return {"status": "deleted", "model": model_name}
