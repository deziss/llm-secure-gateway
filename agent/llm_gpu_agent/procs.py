"""Process helpers: model hint from a command line, parent walk, container id.

Model hints are only a guess used for attribution. The gateway matches them against
the models configured on each server, so a wrong hint shows up as "host" attribution
rather than a wrong number.
"""
from __future__ import annotations

import os
import re
from typing import Callable, Optional

import psutil

_CONTAINER_RE = re.compile(r"[0-9a-f]{64}")
MAX_PARENT_DEPTH = 4  # vLLM v1 keeps VRAM in an EngineCore child; the API pid is one or two levels up


def _tail(model: str) -> Optional[str]:
    if model.startswith("/") or model.endswith(".gguf"):
        base = os.path.basename(model)
        if base.startswith("sha256-"):  # ollama blob path: not a model name
            return None
        return base[: -len(".gguf")] if base.endswith(".gguf") else base
    return model or None


def _is_python_module_launch(argv: list[str]) -> bool:
    """True for `python[3.x] [opts] -m <module> ...`, where -m names a module, not a model."""
    return bool(argv) and os.path.basename(argv[0]).startswith("python") and "-m" in argv


def model_hint(argv: list[str]) -> Optional[str]:
    """Best model name for a command line, or None.

    Priority: --served-model-name (vLLM, what clients call), --alias (llama.cpp),
    --model / -m, then `vllm serve <model>`.
    """
    if not argv:
        return None

    def value_of(*flags: str) -> Optional[str]:
        for i, a in enumerate(argv):
            for f in flags:
                if a == f and i + 1 < len(argv):
                    return argv[i + 1]
                if a.startswith(f + "="):
                    return a.split("=", 1)[1]
        return None

    served = value_of("--served-model-name")
    if served:
        first = served.split(",")[0].strip()
        if first:
            return first
    alias = value_of("--alias")
    if alias:
        return alias
    model = value_of("--model")
    if model:
        return _tail(model)
    # `-m` is llama.cpp's model flag, but `python -m uvicorn ...` uses it for a module name.
    if not _is_python_module_launch(argv):
        model = value_of("-m")
        if model:
            return _tail(model)
    if "serve" in argv:
        i = argv.index("serve")
        if any(os.path.basename(a) == "vllm" for a in argv[:i]) and i + 1 < len(argv):
            nxt = argv[i + 1]
            if not nxt.startswith("-"):
                return _tail(nxt)
    return None


def resolve_hint(pid: int, lookup: Callable = psutil.Process, max_depth: int = MAX_PARENT_DEPTH) -> Optional[str]:
    """Model hint for a GPU process, walking up parents when the process itself has none."""
    cur = pid
    for _ in range(max_depth):
        try:
            proc = lookup(cur)
            argv = proc.cmdline()
            ppid = proc.ppid()
        except (psutil.Error, OSError):
            return None
        hint = model_hint(argv)
        if hint:
            return hint
        if ppid <= 1 or ppid == cur:
            return None
        cur = ppid
    return None


def proc_name(pid: int, lookup: Callable = psutil.Process) -> Optional[str]:
    try:
        return lookup(pid).name()
    except (psutil.Error, OSError):
        return None


def container_id(pid: int, proc_root: str = "/proc") -> Optional[str]:
    """12-char container id from /proc/<pid>/cgroup (docker, containerd, podman, k8s)."""
    try:
        with open(os.path.join(proc_root, str(pid), "cgroup"), encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    m = _CONTAINER_RE.search(text)
    return m.group(0)[:12] if m else None


def enrich(pid: int, lookup: Callable = psutil.Process, proc_root: str = "/proc") -> dict:
    """name, model_hint, container_id for one GPU process. Never raises."""
    return {
        "name": proc_name(pid, lookup),
        "model_hint": resolve_hint(pid, lookup),
        "container_id": container_id(pid, proc_root),
    }
