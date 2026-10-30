"""/admin/observability/* — live model, group, server, GPU and gateway-process data.

Read endpoints need manager or admin (require_manager). Changes need admin (require_admin).
The browser never talks to model servers or GPU agents directly (CSP 'self' only); everything
is proxied through here and the poller snapshot.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
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


_GATEWAY_ZERO = {
    "rps_10s": 0.0, "rps_60s": 0.0, "requests_60s": 0, "errors_60s": 0, "waiting": 0, "generating": 0,
    "tok_s_throughput": 0.0, "tok_s_decode": None, "ttft_p50_ms": None, "inflight_bytes": 0,
    "tokens_estimated": False,
}


def with_server_rows(models: list[dict], servers: list[dict]) -> list[dict]:
    """Every configured (server, model) pair gets a row, not only those with gateway traffic.

    Clients often call model servers directly, so gateway counters stay at zero while the
    server is busy. Server-side figures come from the poller: per-model when the server
    reports them (vLLM model_name labels, router per-engine series), otherwise the server
    totals when it serves exactly one model.
    """
    by_key = {(m.get("server"), m.get("model")): {**m, "gateway_traffic": True} for m in models}
    for srv in servers:
        names = list(srv.get("models") or [])
        names += [m for m in (srv.get("model_stats") or {}) if m not in names]
        for model in names:
            by_key.setdefault((srv["name"], model), {
                "server": srv["name"], "server_type": srv.get("server_type"), "model": model,
                **_GATEWAY_ZERO, "gateway_traffic": False,
            })
    srv_by_name = {s["name"]: s for s in servers}
    out = []
    for (server, model), row in by_key.items():
        srv = srv_by_name.get(server) or {}
        stats = (srv.get("model_stats") or {}).get(model)
        if stats is not None:
            server_side = {
                "server_rps": stats.get("rps"), "server_running": stats.get("running"),
                "server_waiting": stats.get("waiting"), "server_tok_s": stats.get("out_tok_s"),
                "kv_cache_pct": stats.get("kv_cache_pct"), "itl_ms": stats.get("itl_ms"),
                "engines": stats.get("engines"),
            }
            server_side["server_scope"] = "model"
        elif srv:
            # No per-model figures: show the server's totals, shared by all of its models.
            server_side = {
                "server_rps": None, "server_running": srv.get("running"),
                "server_waiting": srv.get("waiting"), "server_tok_s": srv.get("gen_tok_s"),
                "kv_cache_pct": srv.get("kv_cache_pct"), "itl_ms": None, "engines": None,
                "server_scope": "server",
            }
        else:
            server_side = {"server_rps": None, "server_running": None, "server_waiting": None,
                           "server_tok_s": None, "kv_cache_pct": None, "itl_ms": None, "engines": None,
                           "server_scope": None}
        row.update(server_side)
        row["server_status"] = srv.get("status")
        out.append(row)
    out.sort(key=lambda r: (str(r.get("server")), str(r.get("model"))))
    return out


def _totals(instances: list[dict]) -> dict:
    gpus: dict[tuple, dict] = {}
    for inst in instances:
        for g in inst["gpus"]:
            gpus[(g["host"], g["index"])] = g  # dedupe: two instances can share one GPU
    # VRAM used: a model's own process memory when known, else its GPUs' used memory (deduped).
    own = [i["model_vram_mib"] for i in instances if i.get("model_vram_mib") is not None]
    shared: dict[tuple, float] = {}
    for inst in instances:
        if inst.get("model_vram_mib") is None:
            for g in inst["gpus"]:
                if g["mem_used_mib"] is not None:
                    shared[(g["host"], g["index"])] = g["mem_used_mib"]
    used = own + list(shared.values())
    total = [g["mem_total_mib"] for g in gpus.values() if g["mem_total_mib"] is not None]
    util = [g["util_pct"] for g in gpus.values() if g["util_pct"] is not None]
    # Server-wide figures repeat on every model row of that server: count them once per server.
    seen_servers: set = set()
    srv_wait = []
    for i in instances:
        if i.get("server_scope") == "server":
            if i["server"] in seen_servers:
                continue
            seen_servers.add(i["server"])
        if i["server_waiting"] is not None:
            srv_wait.append(i["server_waiting"])
    vram_used = sum(used) if used else None
    vram_total = sum(total) if total else None
    def _best(inst: dict, server_field: str, gateway_field: str) -> float:
        # Per-model server figures cover direct clients too; server-wide ones can't be split
        # across models, so those rows use what the gateway saw.
        v = inst.get(server_field) if inst.get("server_scope") == "model" else None
        return float(v) if v is not None else float(inst.get(gateway_field) or 0.0)

    return {
        "rps_60s": round(sum(_best(i, "server_rps", "rps_60s") for i in instances), 3),
        "tok_s": round(sum(_best(i, "server_tok_s", "tok_s_throughput") for i in instances), 2),
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
    # "Qwen3-30B-A3B" and "qwen-3-30b-a3b" are the same model: bucket on letters and digits
    # only, and label the bucket with the first spelling seen.
    label_for: dict[str, str] = {}
    for m in models:
        raw = group_key(m.get("model") or "", overrides)
        group = label_for.setdefault(re.sub(r"[^a-z0-9]", "", raw.lower()) or raw, raw)
        out_models.append({**m, "group": group})
        srv = by_name.get(m.get("server"), {})
        gpus, attribution = gpu_mod.model_gpus(srv, m.get("model") or "", hosts) if srv else ([], "none")
        own_vram = gpu_mod.model_vram_mib(srv, m.get("model") or "", hosts) if srv else None
        grouped.setdefault(group, []).append({
            "server": m.get("server"),
            "model": m.get("model"),
            "server_type": m.get("server_type"),
            "rps_60s": m.get("rps_60s", 0.0),
            "tok_s_throughput": m.get("tok_s_throughput"),
            "tok_s_decode": m.get("tok_s_decode"),
            "waiting": m.get("waiting"),
            "generating": m.get("generating"),
            "server_rps": m.get("server_rps"),
            "server_tok_s": m.get("server_tok_s"),
            "itl_ms": m.get("itl_ms"),
            "server_running": m.get("server_running"),
            "server_waiting": m.get("server_waiting"),
            "kv_cache_pct": m.get("kv_cache_pct"),
            "server_scope": m.get("server_scope"),
            "gpus": gpu_mod.summarize(gpus, attribution),
            "gpu_attribution": attribution,
            "model_vram_mib": own_vram,
            "gpu_note": gpu_mod.gpu_note(srv, m.get("model") or "", hosts, attribution) if srv else "Unknown server",
            "has_traffic": bool(m.get("gateway_traffic")) or bool(m.get("server_running") or m.get("server_waiting")),
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
    models = with_server_rows(models, servers)
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


# --------------------------------------------------------------------------- #
# Integrations (export to Langfuse, SigNoz, OpenObserve, Grafana, Phoenix, ...)
# --------------------------------------------------------------------------- #
class IntegrationIn(BaseModel):
    enabled: bool = True
    config: dict[str, str] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)   # non-empty value = set/replace
    clear_secrets: List[str] = Field(default_factory=list)  # explicit removal; omission keeps the stored value


def _prom_card() -> dict:
    import os
    from .export.prometheus import prometheus_enabled, prometheus_status

    token_set = bool(os.getenv("OBS_METRICS_TOKEN", "").strip())
    scrape = (
        "scrape_configs:\n"
        "  - job_name: llm-gateway\n"
        "    metrics_path: /metrics\n"
        + ("    authorization:\n      type: Bearer\n      credentials: <OBS_METRICS_TOKEN>\n" if token_set else "")
        + "    static_configs:\n      - targets: ['<gateway-host>:6130']\n"
    )
    return {
        "name": "prometheus", "builtin": True, "title": "Prometheus (pull)",
        "description": "Scrape GET /metrics with Prometheus, Grafana Agent/Alloy or VictoriaMetrics. Always on unless OBS_PROMETHEUS=false.",
        "docs": "https://prometheus.io/docs/prometheus/latest/configuration/configuration/#scrape_config",
        "signals": ["metrics"], "enabled": prometheus_enabled(), "source": "env",
        "token_set": token_set, "scrape_config": scrape,
        "howto": [
            "Add the scrape job below to prometheus.yml (replace the host).",
            "Grafana: add that Prometheus as a data source, then import grafana/llm-gateway-dashboard.json from the repo.",
            "Control: OBS_PROMETHEUS, OBS_METRICS_TOKEN and OBS_METRICS_ALLOW_ANONYMOUS are environment settings.",
        ],
        "status": prometheus_status(),
    }


async def _load_rows(session) -> dict:
    from sqlmodel import select
    from ..models import ObsIntegration

    rows = (await session.execute(select(ObsIntegration))).scalars().all()
    return {r.name: r for r in rows}


def _catalog(rows: dict) -> list[dict]:
    from .export import manager
    from .export.presets import META
    from .export.sinks import configured_names, sinks_status

    runtime = manager.runtime_status()
    traces_only = {"phoenix", "langfuse", "langsmith"}
    env_names = {s["name"] for s in sinks_status() if s.get("source") == "env" and s["name"] != "prometheus"}
    out = []
    for name in sorted(META):
        meta = META[name]
        row = rows.get(name)
        env = manager.row_env(row) if row else {}
        fields = []
        for f in meta["fields"]:
            item = dict(f)
            if f["secret"]:
                item["set"] = bool(env.get(f["key"]))
            else:
                item["value"] = env.get(f["key"], "")
            fields.append(item)
        rt = runtime.get(name) or {}
        out.append({
            "name": name, "builtin": False, "title": meta["title"], "description": meta["description"],
            "docs": meta["docs"], "howto": meta["howto"], "fields": fields,
            "signals": ["traces"] if name in traces_only else ["traces", "metrics"],
            "configured": row is not None, "enabled": bool(row.enabled) if row else False,
            "active": bool(rt.get("active")), "mode": rt.get("mode"),
            "status": rt.get("status"), "error": rt.get("error"),
            "env_configured": name in env_names or name in configured_names(),
            "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        })
    return out


@router.get("/integrations")
async def list_integrations(session: AsyncSession = Depends(get_session), user=Depends(require_manager)) -> dict:
    rows = await _load_rows(session)
    await session.close()
    return {"integrations": [_prom_card()] + _catalog(rows)}


def _validate_input(name: str, body: IntegrationIn) -> None:
    from .export import manager

    allowed = manager.allowed_keys(name)
    if not allowed:
        raise HTTPException(status_code=404, detail=f"unknown integration '{name}'")
    for key, value in list(body.config.items()) + list(body.secrets.items()):
        if key not in allowed:
            raise HTTPException(status_code=422, detail=f"unknown field {key!r} for {name}")
        if len(value) > 2048:
            raise HTTPException(status_code=422, detail=f"{key} is too long")
    for key in body.secrets:
        if not allowed[key]["secret"]:
            raise HTTPException(status_code=422, detail=f"{key} is not a secret field; send it under config")
    for key in body.config:
        if allowed[key]["secret"]:
            raise HTTPException(status_code=422, detail=f"{key} is a secret field; send it under secrets")
    for key in body.clear_secrets:
        if key not in allowed or not allowed[key]["secret"]:
            raise HTTPException(status_code=422, detail=f"cannot clear {key!r}")
    for key, value in body.config.items():
        v = value.strip()
        if v and ("ENDPOINT" in key or key.endswith("_URL") or key.endswith("_HOST")):
            try:
                _check_url(v)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"{allowed[key]['label']} must be an http(s) URL") from None


def _merged_env(name: str, row, body: IntegrationIn) -> dict:
    """Stored values with the request applied on top (blank secrets keep the stored value)."""
    from .export import manager

    env = manager.row_env(row) if row else {}
    for key, value in body.config.items():
        if value.strip():
            env[key] = value.strip()
        else:
            env.pop(key, None)
    for key, value in body.secrets.items():
        if value.strip():
            env[key] = value.strip()
    for key in body.clear_secrets:
        env.pop(key, None)
    return env


@router.put("/integrations/{name}")
async def save_integration(name: str, body: IntegrationIn, session: AsyncSession = Depends(get_session),
                           user=Depends(require_admin)) -> dict:
    from .export import manager
    from .export.presets import PresetError, resolve
    from ..models import ObsIntegration

    _validate_input(name, body)
    allowed = manager.allowed_keys(name)
    row = await session.get(ObsIntegration, name)
    env = _merged_env(name, row, body)
    try:
        resolve(name, env)  # reject incomplete config before storing it
    except PresetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    plain = {k: v for k, v in env.items() if not allowed.get(k, {}).get("secret")}
    secret = {k: v for k, v in env.items() if allowed.get(k, {}).get("secret")}
    if row is None:
        row = ObsIntegration(name=name)
    row.enabled = body.enabled
    row.config = plain
    row.secrets_enc = manager._encrypt_secrets(secret)
    row.updated_at = _utcnow()
    row.updated_by = getattr(user, "email", None)
    session.add(row)
    await session.commit()
    await session.close()
    await manager.load_and_apply()
    return {"saved": name, "enabled": body.enabled}


class IntegrationToggle(BaseModel):
    enabled: bool


@router.post("/integrations/{name}/enabled")
async def toggle_integration(name: str, body: IntegrationToggle, session: AsyncSession = Depends(get_session),
                             user=Depends(require_admin)) -> dict:
    from .export import manager
    from ..models import ObsIntegration

    row = await session.get(ObsIntegration, name)
    if row is None:
        raise HTTPException(status_code=404, detail="integration is not configured yet")
    row.enabled = body.enabled
    row.updated_at = _utcnow()
    session.add(row)
    await session.commit()
    await session.close()
    await manager.load_and_apply()
    return {"name": name, "enabled": body.enabled}


@router.delete("/integrations/{name}")
async def delete_integration(name: str, session: AsyncSession = Depends(get_session),
                             user=Depends(require_admin)) -> dict:
    from .export import manager
    from ..models import ObsIntegration

    row = await session.get(ObsIntegration, name)
    if row is None:
        raise HTTPException(status_code=404, detail="integration is not configured")
    await session.delete(row)
    await session.commit()
    await session.close()
    await manager.load_and_apply()
    return {"deleted": name}


def _test_span_bytes() -> bytes:
    """One OTLP-encoded span. Built directly (not through a TracerProvider) so it works even when
    OTEL_SDK_DISABLED is set in the process, which would turn real spans into no-ops."""
    import os
    import time as _time
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags

    now = _time.time_ns()
    ctx = SpanContext(trace_id=int.from_bytes(os.urandom(16), "big") or 1, span_id=int.from_bytes(os.urandom(8), "big") or 1,
                      is_remote=False, trace_flags=TraceFlags(TraceFlags.SAMPLED))
    span = ReadableSpan(
        name="llm-gateway integration test", context=ctx, parent=None,
        resource=Resource.create({"service.name": "llm-gateway"}),
        attributes={"openinference.span.kind": "LLM", "llm.model_name": "integration-test",
                    "gen_ai.system": "llm-gateway",
                    "input.value": "Integration test from the LLM Gateway admin page."},
        kind=SpanKind.INTERNAL, status=Status(StatusCode.OK), start_time=now - 1_000_000, end_time=now,
        instrumentation_scope=InstrumentationScope("llm_gateway.integration_test"),
    )
    return encode_spans([span]).SerializePartialToString()


@router.post("/integrations/{name}/test")
async def test_integration(name: str, body: IntegrationIn | None = None, session: AsyncSession = Depends(get_session),
                           user=Depends(require_admin)) -> dict:
    """Send one real span to the integration's traces endpoint and report what the backend said.
    Uses the submitted (unsaved) values on top of the stored ones, so a form can be tried first."""
    import time as _time

    import httpx
    from ..models import ObsIntegration
    from .export.presets import PresetError, resolve
    from .export.sinks import redact_url

    body = body or IntegrationIn()
    _validate_input(name, body)
    row = await session.get(ObsIntegration, name)
    await session.close()
    try:
        preset = resolve(name, _merged_env(name, row, body))
    except PresetError as exc:
        return {"ok": False, "stage": "config", "message": str(exc)}
    if preset.traces is None:
        return {"ok": False, "stage": "config", "message": "this integration has no traces endpoint to test"}
    headers = {**preset.traces.headers, "Content-Type": "application/x-protobuf"}
    t0 = _time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), follow_redirects=False) as client:
            r = await client.post(preset.traces.endpoint, content=_test_span_bytes(), headers=headers)
    except httpx.TimeoutException:
        return {"ok": False, "stage": "network", "message": "timed out connecting to the endpoint",
                "endpoint": redact_url(preset.traces.endpoint)}
    except httpx.HTTPError as exc:
        return {"ok": False, "stage": "network", "message": redact_url(f"{type(exc).__name__}: {exc}")[:300],
                "endpoint": redact_url(preset.traces.endpoint)}
    ms = round((_time.monotonic() - t0) * 1000)
    hint = {401: "credentials rejected", 403: "credentials rejected or not allowed",
            404: "endpoint path not found (check the URL / region)", 415: "backend refused protobuf OTLP"}.get(r.status_code)
    return {"ok": 200 <= r.status_code < 300, "stage": "http", "status_code": r.status_code, "latency_ms": ms,
            "endpoint": redact_url(preset.traces.endpoint),
            "message": "span accepted" if r.is_success else (hint or r.text[:200] or f"HTTP {r.status_code}")}
