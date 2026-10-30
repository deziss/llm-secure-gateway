"""GPU sources, parsed into one shape, plus GPU-to-server attribution.

Every source yields GPU dicts with the same keys (see `_gpu`). Exporters without a
per-process list (dcgm, nvidia_gpu_exporter, AMD) report `processes: None`, which the
UI shows as "n/a". Only the llm-gpu-agent gives per-process data.

Units: utilisation is percent (0..100) everywhere; memory is MiB; power is watts.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from . import promtext
from .groups import normalize

logger = logging.getLogger(__name__)

MIB = 1024 * 1024

# AMD device-metrics-exporter metric names. NOT yet verified against a live exporter:
# confirm these (and the gpu_id label) against the exporter docs before relying on them.
# VRAM values are assumed to be MB and are treated as MiB.
AMD_METRICS = {
    "util": "gpu_gfx_activity",
    "vram_used": "gpu_used_vram",
    "vram_total": "gpu_total_vram",
    "temp": "gpu_edge_temperature",
    "power": "gpu_average_socket_power",
}
AMD_ID_LABEL = "gpu_id"

DCGM_METRICS = {
    "util": "DCGM_FI_DEV_GPU_UTIL",
    "fb_used": "DCGM_FI_DEV_FB_USED",
    "fb_free": "DCGM_FI_DEV_FB_FREE",
    "temp": "DCGM_FI_DEV_GPU_TEMP",
    "power": "DCGM_FI_DEV_POWER_USAGE",
}

NVIDIA_EXPORTER_METRICS = {
    "util": "nvidia_smi_utilization_gpu_ratio",   # 0..1 ratio
    "mem_used": "nvidia_smi_memory_used_bytes",
    "mem_total": "nvidia_smi_memory_total_bytes",
    "temp": "nvidia_smi_temperature_gpu",
    "power": "nvidia_smi_power_draw_watts",
    "power_limit": "nvidia_smi_power_limit_watts",
}

SOURCES = ("agent", "dcgm", "nvidia_smi_exporter", "amd_exporter")


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN -> None


def _gpu(host: str, index: Any, *, uuid: str | None = None, name: str | None = None, vendor: str,
         util_pct: Any = None, mem_used_mib: Any = None, mem_total_mib: Any = None,
         temp_c: Any = None, power_w: Any = None, power_limit_w: Any = None,
         processes: list | None = None) -> dict:
    used = _num(mem_used_mib)
    total = _num(mem_total_mib)
    mem_pct = (used / total * 100.0) if (used is not None and total) else None
    try:
        idx = int(index)
    except (TypeError, ValueError):
        idx = index
    return {
        "host": host,
        "index": idx,
        "uuid": uuid or None,
        "name": name or None,
        "vendor": vendor,
        "util_pct": _num(util_pct),
        "mem_util_pct": mem_pct,
        "mem_used_mib": used,
        "mem_total_mib": total,
        "temp_c": _num(temp_c),
        "power_w": _num(power_w),
        "power_limit_w": _num(power_limit_w),
        "processes": processes,
    }


def parse_agent(obj: dict, host: str) -> list[dict]:
    """llm-gpu-agent /v1/gpu JSON. Processes are nested under each GPU."""
    out = []
    for g in (obj or {}).get("gpus") or []:
        procs = []
        for p in g.get("processes") or []:
            procs.append({
                "pid": p.get("pid"),
                "name": p.get("name"),
                "used_mib": _num(p.get("used_mib")),
                "model_hint": p.get("model_hint") or None,
                "container_id": p.get("container_id") or None,
                "server": None,
            })
        out.append(_gpu(
            host, g.get("index"), uuid=g.get("uuid"), name=g.get("name"), vendor=g.get("vendor") or "nvidia",
            util_pct=g.get("util_pct"), mem_used_mib=g.get("mem_used_mib"), mem_total_mib=g.get("mem_total_mib"),
            temp_c=g.get("temp_c"), power_w=g.get("power_w"), power_limit_w=g.get("power_limit_w"),
            processes=procs,
        ))
    return out


def parse_dcgm(text: str, host: str) -> list[dict]:
    devs = promtext.by_device(promtext.parse(text), "gpu", DCGM_METRICS)
    out = []
    for dev, e in devs.items():
        lab = e["labels"]
        used, free = _num(e.get("fb_used")), _num(e.get("fb_free"))
        total = used + free if (used is not None and free is not None) else None
        out.append(_gpu(host, dev, uuid=lab.get("UUID"), name=lab.get("modelName"), vendor="nvidia",
                        util_pct=e.get("util"), mem_used_mib=used, mem_total_mib=total,
                        temp_c=e.get("temp"), power_w=e.get("power")))
    return out


def parse_nvidia_exporter(text: str, host: str) -> list[dict]:
    devs = promtext.by_device(promtext.parse(text), "index", NVIDIA_EXPORTER_METRICS)
    out = []
    for dev, e in devs.items():
        lab = e["labels"]
        util = _num(e.get("util"))
        used = _num(e.get("mem_used"))
        total = _num(e.get("mem_total"))
        out.append(_gpu(host, dev, uuid=lab.get("uuid"), name=lab.get("name"), vendor="nvidia",
                        util_pct=util * 100.0 if util is not None else None,
                        mem_used_mib=used / MIB if used is not None else None,
                        mem_total_mib=total / MIB if total is not None else None,
                        temp_c=e.get("temp"), power_w=e.get("power"), power_limit_w=e.get("power_limit")))
    return out


def parse_amd(text: str, host: str) -> list[dict]:
    devs = promtext.by_device(promtext.parse(text), AMD_ID_LABEL, AMD_METRICS)
    out = []
    for dev, e in devs.items():
        lab = e["labels"]
        out.append(_gpu(host, dev, uuid=lab.get("gpu_uuid") or lab.get("uuid"), name=lab.get("card_model") or lab.get("model"),
                        vendor="amd", util_pct=e.get("util"), mem_used_mib=e.get("vram_used"),
                        mem_total_mib=e.get("vram_total"), temp_c=e.get("temp"), power_w=e.get("power")))
    return out


PARSERS = {
    "dcgm": parse_dcgm,
    "nvidia_smi_exporter": parse_nvidia_exporter,
    "amd_exporter": parse_amd,
}


def _same_model(a: str, b: str) -> bool:
    """Model names compared on letters and digits only ("Qwen3-30B-A3B" == "qwen-3-30b-a3b")."""
    ka, kb = re.sub(r"[^a-z0-9]", "", normalize(a)), re.sub(r"[^a-z0-9]", "", normalize(b))
    return bool(ka) and ka == kb


def attribute_processes(targets: list[dict], hosts: list[dict]) -> None:
    """Set process["server"] in place: a process belongs to a server when its model_hint
    normalises to one of that server's models and the server is bound to the same host."""
    by_host: dict[str, list[dict]] = {}
    for t in targets:
        if t.get("gpu_host"):
            by_host.setdefault(t["gpu_host"], []).append(t)
    for host in hosts:
        servers = by_host.get(host.get("name"), [])
        for gpu_row in host.get("gpus") or []:
            for p in gpu_row.get("processes") or []:
                p["server"] = None
                hint = p.get("model_hint") or ""
                if not hint:
                    continue
                for t in servers:
                    if any(_same_model(hint, m) for m in t.get("models") or []):
                        p["server"] = t["name"]
                        break


def server_gpus(server: dict, hosts: list[dict]) -> tuple[list[dict], str]:
    """GPUs backing one model server, and how they were chosen.

    Returns (gpus, attribution) with attribution one of:
      "none"    no gpu_host, or the host is unknown
      "pinned"  gpu_indices set on the server
      "process" GPUs where this server's agent processes run
      "host"    every GPU on the host (cannot tell which one the server uses)
    """
    host_name = server.get("gpu_host")
    if not host_name:
        return [], "none"
    host = next((h for h in hosts if h.get("name") == host_name), None)
    if host is None:
        return [], "none"
    gpus = host.get("gpus") or []
    indices = server.get("gpu_indices")
    if indices is not None:
        wanted = {int(i) for i in indices}
        return [g for g in gpus if g.get("index") in wanted], "pinned"
    picked = [
        g for g in gpus
        if any(p.get("server") == server.get("name") for p in (g.get("processes") or []))
    ]
    if picked:
        return picked, "process"
    return gpus, "host"


def model_gpus(server: dict, model: str, hosts: list[dict]) -> tuple[list[dict], str]:
    """GPUs running one model of a server: GPUs with a process whose model hint matches the
    model and that belongs to this server. Falls back to server_gpus() when no process matches
    (pinned indices, other processes of the server, or the whole host)."""
    host_name = server.get("gpu_host")
    host = next((h for h in hosts if h.get("name") == host_name), None) if host_name else None
    if host is not None and server.get("gpu_indices") is None:
        picked = [
            g for g in host.get("gpus") or []
            if any(p.get("server") == server.get("name") and _same_model(p.get("model_hint") or "", model)
                   for p in (g.get("processes") or []))
        ]
        if picked:
            return picked, "process"
        if any(p.get("server") == server.get("name")
               for g in host.get("gpus") or [] for p in (g.get("processes") or [])):
            # The server's other models are visible on this host but this one is not, so it
            # runs somewhere the agent cannot see. Showing the whole host would be wrong.
            return [], "none"
    return server_gpus(server, hosts)


def summarize(gpus: list[dict], attribution: str) -> list[dict]:
    """Compact per-GPU view used by the live payload's instance rows."""
    return [
        {
            "host": g.get("host"),
            "index": g.get("index"),
            "name": g.get("name"),
            "util_pct": g.get("util_pct"),
            "mem_used_mib": g.get("mem_used_mib"),
            "mem_total_mib": g.get("mem_total_mib"),
        }
        for g in gpus
    ]
