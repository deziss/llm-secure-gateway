"""Lifespan poller: scrapes model servers and GPU hosts into an in-memory snapshot.

Rules (see the module-level comments for why):
- DB is read in one short session, closed BEFORE any network I/O.
- One overall deadline per tick (0.8 x interval). Targets still running at the
  deadline are cancelled and recorded as "unreachable" (several real backends
  black-hole packets, so a single slow target must never stall the tick).
- Per-target connect 1.5 s / read 2 s, at most 8 targets in flight.
- Per-target exponential backoff after a failure (interval x 2^n, capped at 60 s).
- Targets whose URL has an open circuit in proxy_helpers are skipped.
- With REDIS_URL set, each tick also publishes this replica's LiveStats to
  obs:live:{instance} (TTL 10 s) so /admin/observability/live can merge replicas.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx
from sqlmodel import select

from ...models import GpuHost, ModelServer
from ...proxy_helpers import get_circuit_breaker
from . import gpu as gpu_mod
from .groups import group_key, load_overrides
from .servers import scrape_server

logger = logging.getLogger(__name__)

MAX_CONCURRENCY = 8
MAX_BACKOFF_S = 60.0
CONNECT_TIMEOUT_S = 1.5
READ_TIMEOUT_S = 2.0
LIVE_KEY_PREFIX = "obs:live:"
LIVE_TTL_S = 10

# Module-level state. Tests may monkeypatch _clock and the _read_config / _publish hooks.
_clock: Callable[[], float] = time.monotonic
_servers: dict[str, dict] = {}
_hosts: dict[str, dict] = {}
_backoff: dict[str, dict] = {}          # job key -> {"failures", "next_at", "error"}
_targets: list[dict] = []               # last config read (models, gpu binding)
_overrides: dict[str, list[str]] | None = None   # None until the first config read
_redis: Any = None


def _interval() -> float:
    try:
        return max(1.0, float(os.getenv("OBS_POLL_INTERVAL", "5")))
    except ValueError:
        return 5.0


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts, tz=timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Public read API
# --------------------------------------------------------------------------- #
def get_snapshot() -> dict:
    """{servers: [ServerStat], gpu_hosts: [GpuHost]} as of the last completed tick."""
    return {
        "servers": [dict(s) for s in _servers.values()],
        "gpu_hosts": [dict(h) for h in _hosts.values()],
    }


def get_overrides() -> dict[str, list[str]] | None:
    return None if _overrides is None else dict(_overrides)


def model_groups() -> list[tuple[str, str, str]]:
    """(server, model, group) for every configured model; used by llm_model_group_info."""
    ov = _overrides or {}
    return [(t["name"], m, group_key(m, ov)) for t in _targets for m in t.get("models") or []]


# --------------------------------------------------------------------------- #
# Config read (short DB session, closed before I/O)
# --------------------------------------------------------------------------- #
async def _read_config() -> tuple[list[dict], list[dict], dict[str, list[str]]]:
    from ...database import get_session_context

    async with get_session_context() as session:
        servers = (await session.execute(select(ModelServer))).scalars().all()
        hosts = (await session.execute(select(GpuHost))).scalars().all()
        overrides = await load_overrides(session)
        targets = [
            {
                "name": s.name,
                "server_type": getattr(s.backend_type, "value", s.backend_type),
                "base_url": s.base_url,
                "api_key": s.api_key,
                "models": list(s.models or []),
                "gpu_host": s.gpu_host,
                "gpu_indices": list(s.gpu_indices) if s.gpu_indices is not None else None,
            }
            for s in servers
        ]
        host_rows = [
            {"name": h.name, "url": h.url, "source": h.source, "enabled": bool(h.enabled)}
            for h in hosts
        ]
    return targets, host_rows, overrides


# --------------------------------------------------------------------------- #
# GPU host fetch
# --------------------------------------------------------------------------- #
def _metrics_url(url: str) -> str:
    base = url.rstrip("/")
    return base if base.endswith("/metrics") else f"{base}/metrics"


def _agent_url(url: str) -> str:
    base = url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return f"{base}/v1/gpu"


async def _fetch_host(client: httpx.AsyncClient, host: dict, token: str | None) -> dict:
    """Returns the update for _hosts[name]: {status, error, gpus}."""
    src = host["source"]
    if src == "agent":
        if not token:
            return {"status": "unsupported", "error": "GPU_AGENT_TOKEN is not set on the gateway", "gpus": []}
        r = await client.get(_agent_url(host["url"]), headers={"Authorization": f"Bearer {token}"})
        if r.status_code in (401, 403):
            return {"status": "unreachable", "error": f"agent rejected token (HTTP {r.status_code})", "gpus": []}
        r.raise_for_status()
        gpus = gpu_mod.parse_agent(r.json(), host["name"])
    elif src in gpu_mod.PARSERS:
        r = await client.get(_metrics_url(host["url"]))
        r.raise_for_status()
        gpus = gpu_mod.PARSERS[src](r.text, host["name"])
    else:
        return {"status": "unsupported", "error": f"unknown source {src!r}", "gpus": []}
    return {"status": "ok", "error": None, "gpus": gpus}


def _http_failure(e: BaseException) -> dict:
    if isinstance(e, httpx.TimeoutException):
        return {"status": "unreachable", "error": "timeout"}
    if isinstance(e, httpx.HTTPStatusError):
        return {"status": "unreachable", "error": f"HTTP {e.response.status_code}"}
    return {"status": "unreachable", "error": (str(e)[:200] or type(e).__name__)}


# --------------------------------------------------------------------------- #
# Job runner: backoff, circuit skip, deadline
# --------------------------------------------------------------------------- #
def _carry(base: dict, prev: dict, status: str, error: str | None, kind: str) -> dict:
    """Skipped target: keep its last fetched_at (UI shows staleness). GPU hosts also keep
    their last GPU list, since the GPUs did not go away; server metrics are cleared."""
    out = {**base, "status": status, "error": error, "fetched_at": prev.get("fetched_at")}
    if kind == "gpu":
        out["gpus"] = prev.get("gpus") or []
    return out


async def _run_job(key: str, kind: str, name: str, url: str | None, base: dict,
                   sem: asyncio.Semaphore, fn: Callable[[], Awaitable[dict]]) -> None:
    store = _servers if kind == "server" else _hosts
    now = _clock()
    bo = _backoff.get(key)
    prev = store.get(name, {})
    if bo and now < bo["next_at"]:
        store[name] = _carry(base, prev, "backoff", bo["error"], kind)
        return
    if url and get_circuit_breaker().is_open(url):
        store[name] = _carry(base, prev, "circuit_open", "circuit breaker open", kind)
        return

    async def _finish(result: dict) -> None:
        result = {**base, **result, "fetched_at": _iso()}
        if result.get("status") == "unreachable":
            failures = (bo["failures"] if bo else 0) + 1
            delay = min(MAX_BACKOFF_S, _interval() * (2 ** min(failures - 1, 6)))
            _backoff[key] = {"failures": failures, "next_at": _clock() + delay, "error": result.get("error")}
        else:
            _backoff.pop(key, None)
        store[name] = result

    started = False
    try:
        async with sem:
            started = True
            result = await fn()
    except asyncio.CancelledError:
        if not started:
            # Cancelled while still queued on the semaphore: it was never probed, so it is not
            # unreachable and must not be backed off. Keep the previous data.
            store[name] = {**prev, "status": "skipped", "error": None} if prev else \
                _carry(base, prev, "skipped", None, kind)
            raise
        # Tick deadline reached while this target was in flight.
        await _finish({"status": "unreachable", "error": "poll deadline exceeded"})
        raise
    except httpx.HTTPError as e:
        result = _http_failure(e)
    except Exception as e:  # defensive: a parser bug must not kill the tick
        logger.warning("observability: %s %s failed: %s", kind, name, e)
        result = {"status": "unreachable", "error": f"{type(e).__name__}: {str(e)[:160]}"}
    await _finish(result)


async def poll_once(interval: float | None = None, client: httpx.AsyncClient | None = None) -> None:
    """One tick. Safe to call directly from tests."""
    global _overrides, _targets
    interval = interval or _interval()
    deadline = 0.8 * interval
    try:
        targets, hosts, overrides = await _read_config()
    except Exception:
        logger.exception("observability: config read failed; keeping last snapshot")
        return
    _targets = targets
    _overrides = overrides

    # Drop state for servers and hosts that were deleted.
    live_servers = {t["name"] for t in targets}
    live_hosts = {h["name"] for h in hosts}
    for n in list(_servers):
        if n not in live_servers:
            _servers.pop(n, None)
            _backoff.pop(f"server:{n}", None)
    for n in list(_hosts):
        if n not in live_hosts:
            _hosts.pop(n, None)
            _backoff.pop(f"gpu:{n}", None)

    own = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S))
    token = os.getenv("GPU_AGENT_TOKEN") or None
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    jobs: list[asyncio.Task] = []
    try:
        for t in targets:
            base = {
                "name": t["name"], "server_type": t["server_type"], "url": t["base_url"],
                "status": "unreachable", "error": None, "running": None, "waiting": None,
                "kv_cache_pct": None, "gen_tok_s": None, "prompt_tok_s": None, "loaded_models": [],
                "kind": t["server_type"], "models": list(t.get("models") or []), "engines": [], "model_stats": {},
                "gpu_host": t["gpu_host"], "gpu_indices": t["gpu_indices"],
            }
            jobs.append(asyncio.ensure_future(_run_job(
                f"server:{t['name']}", "server", t["name"], t["base_url"], base, sem,
                lambda t=t: scrape_server(client, t, time.time()),
            )))
        for h in hosts:
            base = {"name": h["name"], "source": h["source"], "url": h["url"], "enabled": h["enabled"],
                    "status": "disabled" if not h["enabled"] else "unreachable", "error": None, "gpus": []}
            if not h["enabled"]:
                _hosts[h["name"]] = {**base, "fetched_at": _iso()}
                continue
            jobs.append(asyncio.ensure_future(_run_job(
                f"gpu:{h['name']}", "gpu", h["name"], None, base, sem,
                lambda h=h: _fetch_host(client, h, token),
            )))
        if jobs:
            _done, pending = await asyncio.wait(jobs, timeout=deadline)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
    finally:
        if own:
            await client.aclose()

    # Attribution is pure (no I/O). It mutates the process dicts shared with _hosts.
    gpu_mod.attribute_processes(targets, list(_hosts.values()))


# --------------------------------------------------------------------------- #
# Multi-replica publish / read (optional REDIS_URL)
# --------------------------------------------------------------------------- #
def _instance() -> str:
    try:
        from .. import instance_id  # observability/__init__.py

        return instance_id()
    except Exception:
        return socket.gethostname()


async def _get_redis():
    global _redis
    url = os.getenv("REDIS_URL")
    if not url:
        return None
    if _redis is None:
        try:
            import redis.asyncio as aioredis  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("REDIS_URL is set but the redis package is not installed")
            return None
        _redis = aioredis.from_url(url, decode_responses=True, socket_timeout=1.0, socket_connect_timeout=1.0)
    return _redis


async def publish_replica() -> None:
    """Write this replica's LiveStats + process snapshot for the cluster view."""
    client = await _get_redis()
    if client is None:
        return
    from ..store import get_live_stats
    from .process import process_snapshot

    payload = {
        "instance": _instance(),
        "generated_at": _iso(),
        "models": get_live_stats().snapshot(),
        "process": process_snapshot(),
    }
    await asyncio.wait_for(
        client.set(f"{LIVE_KEY_PREFIX}{payload['instance']}", json.dumps(payload, default=str), ex=LIVE_TTL_S),
        timeout=2.0,
    )


async def read_replica_payloads() -> dict[str, dict] | None:
    """{instance: payload} for every live replica, or None when Redis is not configured/reachable."""
    client = await _get_redis()
    if client is None:
        return None
    try:
        async def _read() -> dict[str, dict]:
            out: dict[str, dict] = {}
            keys = [k async for k in client.scan_iter(match=f"{LIVE_KEY_PREFIX}*", count=100)]
            for key in keys:
                raw = await client.get(key)
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except ValueError:
                    continue
                out[str(data.get("instance") or key[len(LIVE_KEY_PREFIX):])] = data
            return out

        return await asyncio.wait_for(_read(), timeout=2.0)
    except Exception as e:
        logger.warning("observability: replica read from Redis failed: %s", e)
        return None


# --------------------------------------------------------------------------- #
# Loop
# --------------------------------------------------------------------------- #
async def poller_loop() -> None:
    """Forever. Started from the app lifespan; cancel on shutdown."""
    interval = _interval()
    client = httpx.AsyncClient(timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S))
    try:
        while True:
            started = _clock()
            try:
                await poll_once(interval, client=client)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("observability: poll tick failed")
            try:
                await publish_replica()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("observability: replica publish skipped: %s", e)
            await asyncio.sleep(max(0.5, interval - (_clock() - started)))
    finally:
        await client.aclose()
