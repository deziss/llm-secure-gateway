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
from ..services.snippet_service import generate_client_snippets
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
    user = Depends(require_manager)
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
        
        # Copy plain values out of the ORM rows before any await: the probes
        # yield to other requests, and an expired/detached row then raises
        # DetachedInstanceError on attribute access (seen in production logs).
        targets = [(b.base_url, len(b.models or [])) for b in backends]

        async def check_backend(base_url, n_models):
            nonlocal active_backends, total_models
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    await client.get(f"{base_url.rstrip('/')}/")
                    active_backends += 1
            except (httpx.RequestError, httpx.TimeoutException):
                pass
            total_models += n_models

        await asyncio.gather(*(check_backend(u, n) for u, n in targets))
        
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
    user = Depends(require_manager),
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


@router.get("/api/client-snippets")
@router.get("/client-snippets")
async def get_client_snippets(
    request: Request,
    key: Optional[str] = None,
    model: Optional[str] = "default",
    user = Depends(current_active_user),
) -> dict[str, str]:
    """Return ready-to-run client setup snippets (Claude Code, Cursor, SDKs, cURL)."""
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if forwarded_proto and forwarded_host:
        base_url = f"{forwarded_proto}://{forwarded_host}".rstrip("/")
    else:
        base_url = str(request.base_url).rstrip("/")

    return generate_client_snippets(
        base_url=base_url,
        api_key=key or "",
        model=model or "default",
    )


@router.post("/retention/run")
async def run_retention_now(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Manually trigger governance retention cleanup (admin only)."""
    from ..services.retention_service import run_retention_cleanup

    counts = await run_retention_cleanup(session=session)
    try:
        from ..audit import get_audit_logger

        get_audit_logger().log(
            "retention_cleanup",
            getattr(user, "email", str(user)),
            "retention_cleanup",
            "allow",
            metadata={"trigger": "manual", "deleted": counts},
        )
    except Exception:  # pragma: no cover - audit must never break the endpoint
        pass
    return {"deleted": counts, "total": sum(counts.values())}


# ── Policy profiles (per-owner governance overrides) ─────────────────────

class PolicyProfileUpsert(BaseModel):
    allowed_models: Optional[List[str]] = None
    denied_models: Optional[List[str]] = None
    daily_budget_usd: Optional[float] = None
    enforce_guardrails: Optional[bool] = None
    pii_masking: Optional[bool] = None
    max_concurrent_requests: Optional[int] = None
    description: Optional[str] = None


def _profile_dict(p) -> dict:
    return {
        "owner_id": p.owner_id,
        "allowed_models": p.allowed_models,
        "denied_models": p.denied_models,
        "daily_budget_usd": p.daily_budget_usd,
        "enforce_guardrails": p.enforce_guardrails,
        "pii_masking": p.pii_masking,
        "max_concurrent_requests": p.max_concurrent_requests,
        "description": p.description,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("/policy-profiles")
async def list_policy_profiles(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List all per-owner policy profiles."""
    from sqlalchemy import select

    from ..models import PolicyProfile

    result = await session.execute(select(PolicyProfile).order_by(PolicyProfile.owner_id))
    return {"profiles": [_profile_dict(p) for p in result.scalars().all()]}


@router.put("/policy-profiles/{owner_id}")
async def upsert_policy_profile(
    owner_id: str,
    body: PolicyProfileUpsert,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Create or update the policy profile for an owner (admin only)."""
    from sqlalchemy import select

    from ..models import Owner, PolicyProfile, _utcnow
    from ..services.policy_profiles import invalidate_profile_cache

    owner_exists = await session.get(Owner, owner_id)
    if owner_exists is None:
        raise HTTPException(status_code=404, detail=f"Owner '{owner_id}' not found")

    result = await session.execute(
        select(PolicyProfile).where(PolicyProfile.owner_id == owner_id)
    )
    profile = result.scalars().first()
    data = body.model_dump()

    if profile is None:
        profile = PolicyProfile(owner_id=owner_id, **data)
        session.add(profile)
    else:
        for key, value in data.items():
            setattr(profile, key, value)
        profile.updated_at = _utcnow()

    await session.commit()
    await session.refresh(profile)
    invalidate_profile_cache(owner_id)
    return _profile_dict(profile)


@router.delete("/policy-profiles/{owner_id}")
async def delete_policy_profile(
    owner_id: str,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Delete the policy profile for an owner (admin only)."""
    from sqlalchemy import select

    from ..models import PolicyProfile
    from ..services.policy_profiles import invalidate_profile_cache

    result = await session.execute(
        select(PolicyProfile).where(PolicyProfile.owner_id == owner_id)
    )
    profile = result.scalars().first()
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Policy profile for '{owner_id}' not found")

    await session.delete(profile)
    await session.commit()
    invalidate_profile_cache(owner_id)
    return {"deleted": True, "owner_id": owner_id}


# ── Semantic prompt-validation exemplars (Know Your Prompts/Vectors) ─────

class PromptExemplarCreate(BaseModel):
    text: str
    kind: str = "bad"  # "bad" = block-alike, "good" = approved usage
    label: Optional[str] = None
    source: Optional[str] = None


def _exemplar_dict(e) -> dict:
    return {
        "id": e.id,
        "kind": e.kind,
        "text": e.text,
        "label": e.label,
        "source": e.source,
        "is_active": e.is_active,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.get("/prompt-exemplars")
async def list_prompt_exemplars(
    kind: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List semantic prompt-validation exemplars (admin/manager UI)."""
    from sqlalchemy import select

    from ..models import PromptExemplar

    query = select(PromptExemplar).order_by(PromptExemplar.created_at.desc())
    if kind in ("bad", "good"):
        query = query.where(PromptExemplar.kind == kind)
    result = await session.execute(query.limit(500))
    return {"exemplars": [_exemplar_dict(e) for e in result.scalars().all()]}


@router.post("/prompt-exemplars")
async def create_prompt_exemplar(
    body: PromptExemplarCreate,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Store a new exemplar. 'bad' texts are flagged on similarity; 'good'
    texts define approved usage for allowlist/both modes."""
    from ..models import PromptExemplar
    from ..services.prompt_validation_service import invalidate_exemplar_cache

    kind = body.kind.strip().lower()
    if kind not in ("bad", "good"):
        raise HTTPException(status_code=422, detail="kind must be 'bad' or 'good'")
    if len(body.text.strip()) < 8:
        raise HTTPException(status_code=422, detail="exemplar text must be at least 8 chars")

    exemplar = PromptExemplar(kind=kind, text=body.text.strip(), label=body.label, source=body.source)
    session.add(exemplar)
    await session.commit()
    await session.refresh(exemplar)
    invalidate_exemplar_cache()
    return _exemplar_dict(exemplar)


@router.delete("/prompt-exemplars/{exemplar_id}")
async def delete_prompt_exemplar(
    exemplar_id: str,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    from sqlalchemy import select

    from ..models import PromptExemplar
    from ..services.prompt_validation_service import invalidate_exemplar_cache

    result = await session.execute(select(PromptExemplar).where(PromptExemplar.id == exemplar_id))
    exemplar = result.scalars().first()
    if exemplar is None:
        raise HTTPException(status_code=404, detail="Exemplar not found")
    await session.delete(exemplar)
    await session.commit()
    invalidate_exemplar_cache()
    return {"deleted": True, "id": exemplar_id}


@router.post("/prompt-exemplars/validate")
async def trial_prompt_validation(
    body: dict,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Dry-run: score a prompt against current exemplars without gating."""
    from ..services.prompt_validation_service import load_exemplars, compare_prompt

    prompt = str(body.get("prompt") or "")
    try:
        threshold = float(body.get("threshold") or 0.60)
    except (TypeError, ValueError):
        threshold = 0.60
    mode = str(body.get("mode") or "denylist").lower()
    if mode not in ("denylist", "allowlist", "both"):
        mode = "denylist"
    exemplars = await load_exemplars(session)
    verdict = compare_prompt(prompt, exemplars, threshold=threshold, mode=mode)
    verdict["exemplar_count"] = len(exemplars)
    verdict.pop("reasons", None)
    verdict["reasons"] = verdict.get("reasons", [])
    if verdict.get("max_bad"):
        verdict["max_bad"] = {"similarity": round(verdict["max_bad"][0], 4), "label": verdict["max_bad"][1]}
    if verdict.get("max_good"):
        verdict["max_good"] = {"similarity": round(verdict["max_good"][0], 4), "label": verdict["max_good"][1]}
    return verdict


# Standalone router so /api/client-snippets works directly at root level
client_snippets_router = APIRouter(dependencies=[Depends(current_active_user)])
client_snippets_router.add_api_route(
    "/api/client-snippets",
    get_client_snippets,
    methods=["GET"],
    tags=["client-snippets"],
)

