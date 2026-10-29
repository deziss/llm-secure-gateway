"""Gateway process statistics via psutil, plus an optional tracemalloc top-N.

``process_snapshot()`` is cheap enough to call every tick. tracemalloc is expensive
(roughly 10-30% CPU while tracing), so it is off unless enabled via
``POST /admin/observability/tracemalloc`` or ``OBS_TRACEMALLOC=true``, and its top-20
snapshot is refreshed at most every 30 seconds.
"""
from __future__ import annotations

import asyncio
import gc
import logging
import os
import threading
import time
import tracemalloc
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

TRACEMALLOC_TOP_N = 20
TRACEMALLOC_INTERVAL_S = 30.0

_lock = threading.Lock()
_tm_state: dict[str, Any] = {"enabled": False, "top": [], "taken_at": None, "taken_mono": 0.0}
_proc = None


def _process():
    global _proc
    if _proc is None:
        import psutil
        _proc = psutil.Process(os.getpid())
    return _proc


def _task_count() -> Optional[int]:
    """Number of asyncio tasks on the running loop, or None outside the loop thread."""
    try:
        return len(asyncio.all_tasks())
    except RuntimeError:
        return None


def set_tracemalloc(enabled: bool) -> None:
    with _lock:
        if enabled and not tracemalloc.is_tracing():
            tracemalloc.start(25)
        elif not enabled and tracemalloc.is_tracing():
            tracemalloc.stop()
            _tm_state["top"] = []
            _tm_state["taken_at"] = None
            _tm_state["taken_mono"] = 0.0
        _tm_state["enabled"] = bool(enabled)
    logger.info("tracemalloc %s", "enabled" if enabled else "disabled")


def _take_top() -> None:
    """Runs on a worker thread: snapshot + statistics are slow and must not block the event loop."""
    try:
        snap = tracemalloc.take_snapshot()
        stats = snap.statistics("lineno")[:TRACEMALLOC_TOP_N]
        top = [
            {
                "site": f"{s.traceback[0].filename}:{s.traceback[0].lineno}",
                "size_bytes": s.size,
                "count": s.count,
            }
            for s in stats
        ]
        with _lock:
            if _tm_state["enabled"]:
                _tm_state["top"] = top
                _tm_state["taken_mono"] = time.monotonic()
                _tm_state["taken_at"] = datetime.now(timezone.utc).isoformat()
    except Exception:
        logger.debug("tracemalloc snapshot failed", exc_info=True)
    finally:
        with _lock:
            _tm_state["refreshing"] = False


def _refresh_tracemalloc() -> None:
    """Start a background refresh when the cached one is stale. Caller holds ``_lock``."""
    if not _tm_state["enabled"] or not tracemalloc.is_tracing() or _tm_state.get("refreshing"):
        return
    now = time.monotonic()
    if _tm_state["taken_at"] is not None and now - _tm_state["taken_mono"] < TRACEMALLOC_INTERVAL_S:
        return
    _tm_state["refreshing"] = True
    threading.Thread(target=_take_top, name="tracemalloc-top", daemon=True).start()


def tracemalloc_state() -> dict:
    """Cached top-N (never blocks on take_snapshot); a stale cache triggers a background refresh."""
    with _lock:
        _refresh_tracemalloc()
        return {
            "enabled": bool(_tm_state["enabled"] and tracemalloc.is_tracing()),
            "top": list(_tm_state["top"]),
            "taken_at": _tm_state["taken_at"],
        }


def process_snapshot() -> dict:
    """Current process figures. Fields that cannot be read are None, never an exception."""
    out: dict[str, Any] = {
        "pid": os.getpid(),
        "rss_bytes": None,
        "uss_bytes": None,
        "num_threads": None,
        "threads": [],
        "num_fds": None,
        "asyncio_tasks": _task_count(),
        "gc_counts": list(gc.get_count()),
        "cpu_percent": None,
    }
    try:
        p = _process()
    except Exception:  # psutil missing or process gone
        out["tracemalloc"] = tracemalloc_state()
        return out

    try:
        out["rss_bytes"] = int(p.memory_info().rss)
    except Exception:
        pass
    try:
        out["uss_bytes"] = int(p.memory_full_info().uss)
    except Exception:  # needs ptrace-level access on some kernels
        pass
    try:
        out["num_threads"] = p.num_threads()
        out["threads"] = [
            {"id": t.id, "user_s": round(t.user_time, 3), "system_s": round(t.system_time, 3)}
            for t in p.threads()
        ]
    except Exception:
        pass
    try:
        out["num_fds"] = p.num_fds()
    except Exception:  # not available on Windows
        pass
    try:
        out["cpu_percent"] = p.cpu_percent(interval=None)
    except Exception:
        pass
    out["tracemalloc"] = tracemalloc_state()
    return out
