"""HTTP server + background poller.

Endpoints (Bearer GPU_AGENT_TOKEN required on everything except /healthz):
  GET /v1/gpu    JSON snapshot (what the gateway reads)
  GET /metrics   Prometheus text, llm_gpu_* names with a host label
  GET /healthz   liveness, no auth, no data

A background thread refreshes the snapshot every --interval seconds (default 2), so request
latency does not depend on NVML or rocm-smi call time.
"""
from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import urlsplit

from . import __version__
from .collect import MIB, AmdCollector, NvidiaCollector
from .procs import enrich

logger = logging.getLogger("llm_gpu_agent")
TOKEN_ENV = "GPU_AGENT_TOKEN"


def collect_once(nvidia: Optional[NvidiaCollector], amd: Optional[AmdCollector], host: str,
                 lookup=None, proc_root: str = "/proc") -> dict:
    gpus: list[dict] = []
    for c in (nvidia, amd):
        if c is not None:
            gpus.extend(c.sample())
    enrich_kw = {"proc_root": proc_root}
    if lookup is not None:
        enrich_kw["lookup"] = lookup
    cache: dict[int, dict] = {}
    for g in gpus:
        procs = []
        for p in g.pop("processes", []) or []:
            pid = p["pid"]
            if pid not in cache:
                cache[pid] = enrich(pid, **enrich_kw)
            info = cache[pid]
            procs.append({
                "pid": pid,
                "name": info["name"],
                "used_mib": p.get("used_mib"),
                "model_hint": info["model_hint"],
                "container_id": info["container_id"],
            })
        g["processes"] = procs
    return {
        "host": host,
        "agent_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gpus": gpus,
    }


def _esc(v: Any) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def metrics_text(doc: dict) -> str:
    host = doc.get("host", "")
    lines: list[str] = []

    def emit(name: str, help_text: str, rows: list[tuple[dict, float]]) -> None:
        if not rows:
            return
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        for labels, value in rows:
            lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items() if v is not None)
            lines.append(f"{name}{{{lab}}} {value}")

    gpus = doc.get("gpus", [])

    def base(g: dict) -> dict:
        return {"host": host, "gpu": g.get("index"), "uuid": g.get("uuid") or "",
                "name": g.get("name") or "", "vendor": g.get("vendor") or ""}

    emit("llm_gpu_utilization_ratio", "GPU utilisation 0..1",
         [(base(g), g["util_pct"] / 100.0) for g in gpus if g.get("util_pct") is not None])
    emit("llm_gpu_memory_used_bytes", "GPU memory in use",
         [(base(g), g["mem_used_mib"] * MIB) for g in gpus if g.get("mem_used_mib") is not None])
    emit("llm_gpu_memory_total_bytes", "GPU memory capacity",
         [(base(g), g["mem_total_mib"] * MIB) for g in gpus if g.get("mem_total_mib") is not None])
    emit("llm_gpu_temperature_celsius", "GPU temperature",
         [(base(g), g["temp_c"]) for g in gpus if g.get("temp_c") is not None])
    emit("llm_gpu_power_watts", "GPU power draw",
         [(base(g), g["power_w"]) for g in gpus if g.get("power_w") is not None])
    proc_rows = []
    for g in gpus:
        for p in g.get("processes") or []:
            if p.get("used_mib") is None:
                continue
            proc_rows.append(({"host": host, "gpu": g.get("index"), "pid": p.get("pid"),
                               "process": p.get("name") or "", "model": p.get("model_hint") or ""},
                              p["used_mib"] * MIB))
    emit("llm_gpu_process_memory_bytes", "GPU memory used by one process", proc_rows)
    return "\n".join(lines) + "\n"


class AgentState:
    """Latest snapshot, refreshed by the poller thread."""

    def __init__(self, token: str, host: str) -> None:
        self.token = token
        self.host = host
        self._lock = threading.Lock()
        self._doc: dict = {"host": host, "agent_version": __version__, "generated_at": None, "gpus": []}

    def set(self, doc: dict) -> None:
        with self._lock:
            self._doc = doc

    def get(self) -> dict:
        with self._lock:
            return self._doc


class _Poller(threading.Thread):
    def __init__(self, state: AgentState, collect, interval: float) -> None:
        super().__init__(name="gpu-poller", daemon=True)
        self.state = state
        self.collect = collect
        self.interval = interval
        self._stop_evt = threading.Event()

    def run(self) -> None:
        while not self._stop_evt.is_set():
            started = time.monotonic()
            try:
                self.state.set(self.collect())
            except Exception:
                logger.exception("gpu poll failed")
            self._stop_evt.wait(max(0.1, self.interval - (time.monotonic() - started)))

    def stop(self) -> None:
        self._stop_evt.set()


class _Handler(BaseHTTPRequestHandler):
    server_version = f"llm-gpu-agent/{__version__}"

    def log_message(self, fmt: str, *args: Any) -> None:  # never log request headers (they hold the token)
        logger.debug("%s %s", self.address_string(), fmt % args)

    def _send(self, code: int, body: bytes, ctype: str, extra: Optional[dict] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "").encode()
        expected = f"Bearer {self.server.state.token}".encode()
        return hmac.compare_digest(supplied, expected)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        path = urlsplit(self.path).path
        if path == "/healthz":
            return self._send(200, b'{"ok": true}', "application/json")
        if path not in ("/v1/gpu", "/metrics"):
            return self._send(404, b'{"error": "not found"}', "application/json")
        if not self._authorized():
            return self._send(401, b'{"error": "unauthorized"}', "application/json",
                              {"WWW-Authenticate": 'Bearer realm="llm-gpu-agent"'})
        doc = self.server.state.get()
        if path == "/v1/gpu":
            return self._send(200, json.dumps(doc).encode(), "application/json")
        return self._send(200, metrics_text(doc).encode(), "text/plain; version=0.0.4; charset=utf-8")


def make_server(host: str, port: int, state: AgentState) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), _Handler)
    srv.daemon_threads = True
    srv.state = state  # type: ignore[attr-defined]
    return srv


def build_collectors(enable_nvidia: bool = True, enable_amd: bool = True):
    nvidia = amd = None
    if enable_nvidia:
        try:
            nvidia = NvidiaCollector()
        except Exception as e:
            logger.info("NVIDIA not available: %s", e)
    if enable_amd:
        c = AmdCollector()
        if c.available:
            amd = c
        else:
            logger.info("AMD not available (no amdsmi, no rocm-smi)")
    return nvidia, amd


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="llm-gpu-agent", description=__doc__.splitlines()[0])
    p.add_argument("--host", default=os.getenv("GPU_AGENT_BIND", "0.0.0.0"))
    p.add_argument("--port", type=int, default=int(os.getenv("GPU_AGENT_PORT", "9400")))
    p.add_argument("--interval", type=float, default=2.0, help="seconds between GPU polls")
    p.add_argument("--host-label", default=os.getenv("GPU_AGENT_HOST_LABEL") or socket.gethostname())
    p.add_argument("--no-nvidia", action="store_true")
    p.add_argument("--no-amd", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    token = os.getenv(TOKEN_ENV, "")
    if not token:
        print(f"{TOKEN_ENV} must be set; the agent refuses to run without a token", file=sys.stderr)
        return 2

    nvidia, amd = build_collectors(not args.no_nvidia, not args.no_amd)
    if nvidia is None and amd is None:
        logger.warning("no GPU backend found; serving an empty GPU list")

    state = AgentState(token, args.host_label)
    poller = _Poller(state, lambda: collect_once(nvidia, amd, args.host_label), args.interval)
    poller.state.set(poller.collect())
    poller.start()

    srv = make_server(args.host, args.port, state)
    logger.info("llm-gpu-agent %s on %s:%d (host=%s)", __version__, args.host, args.port, args.host_label)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()
        srv.server_close()
        if nvidia is not None:
            nvidia.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
