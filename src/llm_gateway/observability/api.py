"""/admin/observability/* — live model, group, server, GPU and gateway-process data.

Read endpoints need manager or admin (require_manager). Changes need admin (require_admin).
The browser never talks to model servers or GPU agents directly (CSP 'self' only); everything
is proxied through here and the poller snapshot.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
from datetime import datetime, timezone
from typing import List, Literal, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import GpuHost, ModelServer, SystemSetting, _utcnow
from ..routers.admin import require_admin, require_manager
from ..services.config_service import _backends_cache, _settings_cache
from .collect import gpu as gpu_mod
from .collect import poller
from .collect.groups import OVERRIDES_KEY, group_key, load_overrides, validate_overrides

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/observability", tags=["observability"])

GPU_SOURCES = Literal["agent", "dcgm", "nvidia_smi_exporter", "amd_exporter"]


# --------------------------------------------------------------------------- #
# Payload assembly
# --------------------------------------------------------------------------- #
def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_models() -> list[dict]:
    try:
        from .store import get_live_stats

        return list(get_live_stats().snapshot())
    except Exception:
        logger.debug("live stats unavailable", exc_info=True)
        return []


def _local_process() -> dict:
    try:
        from .collect.process import process_snapshot

        return process_snapshot()
    except Exception:
        logger.debug("process snapshot unavailable", exc_info=True)
        return {}


def _sinks() -> list[dict]:
    try:
        from .export.sinks import sinks_status

        return list(sinks_status())
    except Exception:
        logger.debug("sink status unavailable", exc_info=True)
        return []


def _instance() -> str:
    try:
        from . import instance_id

        return instance_id()
    except Exception:
        return socket.gethostname()


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def merge_models(replica_rows: list[list[dict]]) -> list[dict]:
    """Sum counts and rates per (server, model) across replicas."""
    acc: dict[tuple[str, str], dict] = {}
    decode: dict[tuple[str, str], list[float]] = {}
    ttft: dict[tuple[str, str], list[float]] = {}
    for rows in replica_rows:
        for r in rows:
            key = (r.get("server"), r.get("model"))
            m = acc.setdefault(key, {
                "server": r.get("server"), "server_type": r.get("server_type"), "model": r.get("model"),
                "rps_10s": 0.0, "rps_60s": 0.0, "requests_60s": 0, "errors_60s": 0,
                "waiting": 0, "generating": 0, "tok_s_throughput": 0.0, "inflight_bytes": 0,
                "tokens_estimated": False, "tok_s_decode": None, "ttft_p50_ms": None,
            })
            for f in ("rps_10s", "rps_60s", "tok_s_throughput"):
                m[f] += float(r.get(f) or 0.0)
            for f in ("requests_60s", "errors_60s", "waiting", "generating", "inflight_bytes"):
                m[f] += int(r.get(f) or 0)
            m["tokens_estimated"] = m["tokens_estimated"] or bool(r.get("tokens_estimated"))
            if r.get("tok_s_decode") is not None:
                decode.setdefault(key, []).append(float(r["tok_s_decode"]))
            if r.get("ttft_p50_ms") is not None:
                ttft.setdefault(key, []).append(float(r["ttft_p50_ms"]))
    for key, m in acc.items():
        m["tok_s_decode"] = _mean(decode.get(key, []))
        m["ttft_p50_ms"] = _mean(ttft.get(key, []))
    return list(acc.values())


def _totals(instances: list[dict]) -> dict:
    gpus: dict[tuple, dict] = {}
    for inst in instances:
        for g in inst["gpus"]:
            gpus[(g["host"], g["index"])] = g  # dedupe: two instances can share one GPU
    servers: dict[str, dict] = {}
    for inst in instances:
        servers.setdefault(inst["server"], inst)  # server queue is shared by its models
    used = [g["mem_used_mib"] for g in gpus.values() if g["mem_used_mib"] is not None]
    total = [g["mem_total_mib"] for g in gpus.values() if g["mem_total_mib"] is not None]
    util = [g["util_pct"] for g in gpus.values() if g["util_pct"] is not None]
    srv_wait = [i["server_waiting"] for i in servers.values() if i["server_waiting"] is not None]
    vram_used = sum(used) if used else None
    vram_total = sum(total) if total else None
    return {
        "rps_60s": round(sum(i["rps_60s"] for i in instances), 3),
        "tok_s": round(sum(i["tok_s_throughput"] or 0.0 for i in instances), 2),
        "waiting": sum(i["waiting"] or 0 for i in instances),
        "generating": sum(i["generating"] or 0 for i in instances),
        "server_waiting": sum(srv_wait) if srv_wait else None,
        "vram_used_mib": vram_used,
        "vram_total_mib": vram_total,
        "vram_pct": (vram_used / vram_total * 100.0) if (vram_used is not None and vram_total) else None,
        "gpu_util_avg_pct": _mean(util),
        "gpu_count": len(gpus),
        "instance_count": len(instances),
    }


def build_groups(models: list[dict], servers: list[dict], hosts: list[dict],
                 overrides: dict[str, list[str]]) -> tuple[list[dict], list[dict]]:
    """Attach `group` to each model row and build the collage (one entry per group)."""
    by_name = {s["name"]: s for s in servers}
    grouped: dict[str, list[dict]] = {}
    out_models = []
    for m in models:
        group = group_key(m.get("model") or "", overrides)
        out_models.append({**m, "group": group})
        srv = by_name.get(m.get("server"), {})
        gpus, attribution = gpu_mod.server_gpus(srv, hosts) if srv else ([], "none")
        grouped.setdefault(group, []).append({
            "server": m.get("server"),
            "model": m.get("model"),
            "server_type": m.get("server_type"),
            "rps_60s": m.get("rps_60s", 0.0),
            "tok_s_throughput": m.get("tok_s_throughput"),
            "tok_s_decode": m.get("tok_s_decode"),
            "waiting": m.get("waiting"),
            "generating": m.get("generating"),
            "server_running": srv.get("running"),
            "server_waiting": srv.get("waiting"),
            "kv_cache_pct": srv.get("kv_cache_pct"),
            "gpus": gpu_mod.summarize(gpus, attribution),
            "gpu_attribution": attribution,
        })
    groups = [
        {"group": g, "instances": insts, "totals": _totals(insts)}
        for g, insts in sorted(grouped.items())
    ]
    return out_models, groups


async def _cluster_view(local_models: list[dict], instance: str) -> tuple[str, list[str], list[dict]]:
    replicas = await poller.read_replica_payloads()
    if replicas is None:
        return "replica", [instance], local_models
    payloads = {k: v for k, v in replicas.items() if k != instance}
    rows = [local_models] + [p.get("models") or [] for p in payloads.values()]
    return "cluster", sorted({instance, *payloads}), merge_models(rows)


async def build_payload(overrides: dict[str, list[str]]) -> dict:
    snap = poller.get_snapshot()
    servers, hosts = snap["servers"], snap["gpu_hosts"]
    instance = _instance()
    local_models = _local_models()
    process = _local_process()
    scope, replicas, models = await _cluster_view(local_models, instance)
    out_models, groups = build_groups(models, servers, hosts, overrides)
    return {
        "instance": instance,
        "replicas": replicas,
        "scope": scope,
        "generated_at": _iso_now(),
        "process": process,
        "models": out_models,
        "groups": groups,
        "servers": servers,
        "gpus": hosts,
        "sinks": _sinks(),
    }


# --------------------------------------------------------------------------- #
# Live + stream
# --------------------------------------------------------------------------- #
@router.get("/live")
async def live(session: AsyncSession = Depends(get_session), user=Depends(require_manager)) -> dict:
    overrides = await load_overrides(session)
    await session.close()  # no DB connection held during the Redis read
    return await build_payload(overrides)


@router.get("/stream")
async def stream(request: Request, session: AsyncSession = Depends(get_session),
                 user=Depends(require_manager)) -> StreamingResponse:
    """SSE, one LivePayload every 3 s. Session closed up front (see routers/admin.py)."""
    overrides = await load_overrides(session)
    await session.close()

    async def events():
        while True:
            if await request.is_disconnected():
                break
            current = poller.get_overrides()
            payload = await build_payload(overrides if current is None else current)
            yield f"data: {json.dumps(payload, default=str)}\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class TracemallocIn(BaseModel):
    enable: bool


@router.post("/tracemalloc")
async def tracemalloc_toggle(body: TracemallocIn, user=Depends(require_admin)) -> dict:
    try:
        from .collect.process import set_tracemalloc
    except ImportError as e:
        raise HTTPException(status_code=503, detail="process collector not available") from e
    set_tracemalloc(body.enable)
    return {"enabled": body.enable}


# --------------------------------------------------------------------------- #
# Model groups (OBS_MODEL_GROUPS)
# --------------------------------------------------------------------------- #
class GroupsIn(BaseModel):
    overrides: dict[str, List[str]] = Field(default_factory=dict)


async def _save_setting(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(SystemSetting, key)
    if row is None:
        session.add(SystemSetting(key=key, value=value))
    else:
        row.value = value
        row.updated_at = _utcnow()
        session.add(row)
    await session.commit()
    _settings_cache.invalidate(key)


@router.get("/groups")
async def get_groups(session: AsyncSession = Depends(get_session), user=Depends(require_manager)) -> dict:
    return {"overrides": await load_overrides(session)}


@router.put("/groups")
async def put_groups(body: GroupsIn, session: AsyncSession = Depends(get_session),
                     user=Depends(require_admin)) -> dict:
    try:
        clean = validate_overrides(body.overrides)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    await _save_setting(session, OVERRIDES_KEY, json.dumps(clean, sort_keys=True))
    return {"overrides": clean}


# --------------------------------------------------------------------------- #
# GPU hosts
# --------------------------------------------------------------------------- #
_NAME_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"


def _check_url(v: str) -> str:
    v = (v or "").strip()
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("url must be an http(s) URL")
    return v


class GpuHostIn(BaseModel):
    name: str = Field(pattern=_NAME_PATTERN)
    url: str
    source: GPU_SOURCES
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def _url(cls, v):
        return _check_url(v)


class GpuHostUpdate(BaseModel):
    url: Optional[str] = None
    source: Optional[GPU_SOURCES] = None
    enabled: Optional[bool] = None

    @field_validator("url")
    @classmethod
    def _url(cls, v):
        return None if v is None else _check_url(v)


def _host_out(h: GpuHost) -> dict:
    return {
        "name": h.name,
        "url": h.url,
        "source": h.source,
        "enabled": bool(h.enabled),
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }


@router.get("/gpu-hosts")
async def list_gpu_hosts(session: AsyncSession = Depends(get_session), user=Depends(require_manager)) -> list:
    from sqlmodel import select

    rows = (await session.execute(select(GpuHost).order_by(GpuHost.name))).scalars().all()
    return [_host_out(h) for h in rows]


@router.post("/gpu-hosts", status_code=201)
async def create_gpu_host(body: GpuHostIn, session: AsyncSession = Depends(get_session),
                          user=Depends(require_admin)) -> dict:
    if await session.get(GpuHost, body.name):
        raise HTTPException(status_code=409, detail="a GPU host with this name already exists")
    row = GpuHost(name=body.name, url=body.url, source=body.source, enabled=body.enabled)
    session.add(row)
    await session.commit()
    return _host_out(row)


@router.put("/gpu-hosts/{name}")
async def update_gpu_host(name: str, body: GpuHostUpdate, session: AsyncSession = Depends(get_session),
                          user=Depends(require_admin)) -> dict:
    row = await session.get(GpuHost, name)
    if row is None:
        raise HTTPException(status_code=404, detail="GPU host not found")
    for field in ("url", "source", "enabled"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
    session.add(row)
    await session.commit()
    return _host_out(row)


@router.delete("/gpu-hosts/{name}")
async def delete_gpu_host(name: str, session: AsyncSession = Depends(get_session),
                          user=Depends(require_admin)) -> dict:
    row = await session.get(GpuHost, name)
    if row is None:
        raise HTTPException(status_code=404, detail="GPU host not found")
    await session.execute(
        update(ModelServer).where(ModelServer.gpu_host == name).values(gpu_host=None, gpu_indices=None)
    )
    await session.delete(row)
    await session.commit()
    _backends_cache.clear()
    return {"deleted": name}


class ServerGpuIn(BaseModel):
    gpu_host: Optional[str] = Field(default=None, pattern=_NAME_PATTERN)
    gpu_indices: Optional[List[int]] = None

    @field_validator("gpu_indices")
    @classmethod
    def _indices(cls, v):
        if v is None:
            return None
        if any(i < 0 for i in v) or len(set(v)) != len(v):
            raise ValueError("gpu_indices must be unique non-negative integers")
        return v


@router.put("/servers/{name}/gpu")
async def set_server_gpu(name: str, body: ServerGpuIn, session: AsyncSession = Depends(get_session),
                         user=Depends(require_admin)) -> dict:
    server = await session.get(ModelServer, name)
    if server is None:
        raise HTTPException(status_code=404, detail="model server not found")
    if body.gpu_host is not None and await session.get(GpuHost, body.gpu_host) is None:
        raise HTTPException(status_code=422, detail="gpu_host does not exist")
    server.gpu_host = body.gpu_host
    server.gpu_indices = body.gpu_indices if body.gpu_host else None
    session.add(server)
    await session.commit()
    _backends_cache.clear()
    return {"name": name, "gpu_host": server.gpu_host, "gpu_indices": server.gpu_indices}
