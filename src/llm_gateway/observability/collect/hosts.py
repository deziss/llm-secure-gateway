"""Node/CPU/RAM/disk/container telemetry sources, parsed into one shape.

Two exporter families are supported, both scraped from their ``/metrics`` endpoint:

* ``node_exporter`` — host CPU load, memory, swap, filesystems, network and uptime.
* ``cadvisor``      — per-container memory and cumulative CPU on the host.

``linux_agent`` is reserved for a future push agent; the poller reports it as
"unsupported" until a parser exists here.

Prometheus exposition gives *cumulative counters*; a single scrape cannot yield a
rate. Everything derived here is therefore computed from one sample only:

cpu_pct approximation (node_exporter)
    ``rate(node_cpu_seconds_total[5m])`` needs two scrapes, so instead we use
    ``cpu_pct = clamp(node_load1 / cpu_count * 100, 0..100)`` where ``cpu_count``
    is the number of distinct ``cpu`` label values seen on ``node_cpu_seconds_total``.
    Load-average based, so it lags a burst and counts runnable-but-not-running tasks.

cpu_seconds_total (cadvisor)
    ``container_cpu_usage_seconds_total`` is cumulative CPU-seconds since the
    container started — NOT a percentage. It is exposed as a raw counter
    (``cpu_seconds_total``) and the UI labels it "CPU-sec (cum.)". A true cpu %
    would need two samples (or container_spec_cpu_quota, deliberately not used here
    to keep the parser minimal).

Returned shapes (all missing values are None / []):
    node_exporter -> {cpu_pct, cpu_count, load1, load5, load15,
                      mem_total_bytes, mem_available_bytes, mem_used_bytes, mem_used_pct,
                      swap_total_bytes, swap_used_bytes, swap_used_pct,
                      disk: {mountpoint, size_bytes, avail_bytes, used_pct},
                      disks: [...same...],
                      net_rx_bytes_total, net_tx_bytes_total,
                      uptime_s, kernel, nodename, containers}
    cadvisor      -> {containers: [{name, id, mem_used_bytes, cpu_seconds_total}],
                      container_count}  (host-level fields stay None; merged by the poller)

Units: percentages 0..100, sizes bytes, times seconds, cpu_seconds_total cumulative.
"""
from __future__ import annotations

import math
import time
from typing import Any

from . import promtext

SOURCES = ("node_exporter", "cadvisor", "linux_agent")

# CPU modes seen on node_cpu_seconds_total; every mode carries a `cpu` label so any
# of them works for counting cores.
_CPU_MODES = ("idle", "user", "system", "nice", "iowait", "irq", "softirq", "steal", "guest")

# Filesystems that are not real disks; skipped when picking the root/most-used mount.
_PSEUDO_FSTYPES = {"tmpfs", "devtmpfs", "squashfs", "overlay", "fuse.gvfsd-fuse", "fuse.portal",
                   "ramfs", "hugetlbfs", "nsfs", "autofs", "mqueue", "debugfs", "tracefs",
                   "securityfs", "pstore", "bpf", "configfs", "devpts", "proc", "sysfs", "cgroup",
                   "cgroup2", "efivarfs", "binfmt_misc", "rpc_pipefs", "selinuxfs", "snapfuse"}

# cgroup prefixes that are real workload containers, not system slices.
_CONTAINER_PREFIXES = ("/docker", "/kubepods", "/containerd", "/cri-containerd",
                       "/crio", "/system.slice/docker", "/ecs")


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN -> None


def _clamp(v: float | None, lo: float = 0.0, hi: float = 100.0) -> float | None:
    return None if v is None else max(lo, min(hi, v))


def _sum(samples: list[promtext.Sample], name: str, skip_label: str | None = None,
         skip_values: tuple[str, ...] = ()) -> float | None:
    """Sum all samples of `name`, optionally skipping one label value (e.g. loopback)."""
    total, seen = 0.0, False
    for s in samples:
        if s.name != name or math.isnan(s.value):
            continue
        if skip_label is not None and s.labels.get(skip_label) in skip_values:
            continue
        total += s.value
        seen = True
    return total if seen else None


# --------------------------------------------------------------------------- #
# node_exporter
# --------------------------------------------------------------------------- #
def parse_node_exporter(text: str, host: str, now_ts: float | None = None) -> dict:
    """Parse a node_exporter scrape into one node dict. Pure: same text -> same output."""
    samples = promtext.parse(text)
    out: dict = {
        "host": host,
        "cpu_pct": None, "cpu_count": None,
        "load1": None, "load5": None, "load15": None,
        "mem_total_bytes": None, "mem_available_bytes": None,
        "mem_used_bytes": None, "mem_used_pct": None,
        "swap_total_bytes": None, "swap_used_bytes": None, "swap_used_pct": None,
        "disk": None, "disks": [],
        "net_rx_bytes_total": None, "net_tx_bytes_total": None,
        "uptime_s": None, "kernel": None, "nodename": None,
        "containers": [],
        "source": "node_exporter",
    }

    # CPU count = number of distinct `cpu` label values on node_cpu_seconds_total.
    cpus = {s.labels.get("cpu") for s in samples
            if s.name == "node_cpu_seconds_total" and "cpu" in s.labels
            and s.labels.get("mode") in _CPU_MODES}
    cpu_count = len({c for c in cpus if c is not None}) or None
    out["cpu_count"] = cpu_count

    def _load(name: str) -> float | None:
        for s in samples:  # older node_exporter split load per-cpu; prefer the aggregate
            if s.name == name and s.labels.get("cpu", "system") == "system":
                return _num(s.value)
        return None

    load1 = _load("node_load1")
    out["load1"], out["load5"], out["load15"] = load1, _load("node_load5"), _load("node_load15")
    # Single-sample approximation, documented at module top: load1 per core, capped at 100 %.
    if load1 is not None and cpu_count:
        out["cpu_pct"] = _clamp(load1 / cpu_count * 100.0)

    # Memory: used = total - available ("available" already excludes reclaimable cache).
    total = _num(promtext.total(samples, "node_memory_MemTotal_bytes"))
    avail = _num(promtext.total(samples, "node_memory_MemAvailable_bytes"))
    out["mem_total_bytes"], out["mem_available_bytes"] = total, avail
    if total is not None and avail is not None:
        used = max(0.0, total - avail)
        out["mem_used_bytes"] = used
        out["mem_used_pct"] = _clamp(used / total * 100.0) if total else None

    swap_total = _num(promtext.total(samples, "node_memory_SwapTotal_bytes"))
    swap_free = _num(promtext.total(samples, "node_memory_SwapFree_bytes"))
    out["swap_total_bytes"] = swap_total
    if swap_total and swap_free is not None:
        out["swap_used_bytes"] = max(0.0, swap_total - swap_free)
        out["swap_used_pct"] = _clamp((swap_total - swap_free) / swap_total * 100.0)

    # Filesystems: one entry per real mount; `disk` is / when present, else most-used mount.
    mounts: dict[str, dict] = {}
    for s in samples:
        if s.name == "node_filesystem_avail_bytes":
            mp = s.labels.get("mountpoint") or ""
            if s.labels.get("fstype") in _PSEUDO_FSTYPES or not mp:
                continue
            entry = mounts.setdefault(mp, {"mountpoint": mp, "size_bytes": None,
                                           "avail_bytes": None, "used_pct": None})
            entry["avail_bytes"] = _num(s.value)
    for s in samples:
        entry = mounts.get(s.labels.get("mountpoint") or "")
        if entry is None:
            continue
        if s.name == "node_filesystem_size_bytes":
            entry["size_bytes"] = _num(s.value)
    for entry in mounts.values():
        if entry["size_bytes"] and entry["avail_bytes"] is not None:
            used = max(0.0, entry["size_bytes"] - entry["avail_bytes"])
            entry["used_pct"] = _clamp(used / entry["size_bytes"] * 100.0)
    disks = [d for d in mounts.values() if d["size_bytes"]]
    disks.sort(key=lambda d: (d["mountpoint"] != "/", -(d["used_pct"] or 0.0)))
    out["disks"] = disks
    out["disk"] = disks[0] if disks else None

    # Network: raw counter totals, loopback excluded.
    out["net_rx_bytes_total"] = _sum(samples, "node_network_receive_bytes_total", "device", ("lo",))
    out["net_tx_bytes_total"] = _sum(samples, "node_network_transmit_bytes_total", "device", ("lo",))

    # Uptime from boot time (needs wall clock now; `now_ts` is injectable for tests).
    boot = _num(promtext.total(samples, "node_boot_time_seconds"))
    if boot:
        now = time.time() if now_ts is None else now_ts
        up = now - boot
        out["uptime_s"] = up if up >= 0 else None
    for s in samples:
        if s.name == "node_uname_info":
            out["kernel"] = s.labels.get("release") or None
            out["nodename"] = s.labels.get("nodename") or None
            break
    return out


# --------------------------------------------------------------------------- #
# cadvisor
# --------------------------------------------------------------------------- #
def _short_name(labels: dict, raw_name: str) -> str:
    """Best human label for a container: pod name > image > last path/id segment."""
    pod = labels.get("container_label_io_kubernetes_pod_name")
    if pod:
        ns = labels.get("container_label_io_kubernetes_pod_namespace")
        return f"{ns}/{pod}" if ns else pod
    img = (labels.get("image") or "").split("/")[-1].split(":")[0]
    seg = raw_name.rstrip("/").split("/")[-1]
    if img and img != "pause":
        return f"{img} ({seg[:12]})" if seg else img
    return seg[:64] if seg else "container"


def _is_workload(raw_name: str, labels: dict) -> bool:
    """Keep workload containers; drop the root cgroup, bare slices and pod sandboxes."""
    if not raw_name or raw_name == "/":
        return False
    if raw_name in ("/kubepods.slice", "/docker", "/kubepods", "/system.slice",
                    "/init.scope", "/user.slice", "/kubepods.slice/kubepods-burstable.slice",
                    "/kubepods.slice/kubepods-besteffort.slice"):
        return False
    if (raw_name.startswith("/kubepods-pod")
            or "/kubepods-pod" in raw_name
            or raw_name.startswith("/kubepods/burstable/pod")
            or raw_name.startswith("/kubepods/besteffort/pod")):
        return False  # pod sandbox rows: their children carry the real containers
    if labels.get("container_label_io_cri_containerd_kind") == "sandbox":
        return False
    if any(raw_name.startswith(p) for p in _CONTAINER_PREFIXES):
        return True
    # cgroup v2 flat layout (/system.slice/<name>.scope) and docker-alias `name` labels.
    return raw_name.startswith("/system.slice/") or "/docker/" in raw_name or bool(labels.get("name"))


def parse_cadvisor(text: str, host: str) -> dict:
    """Parse a cAdvisor scrape into top-20 containers by working-set memory. Pure."""
    samples = promtext.parse(text)
    by_id: dict[str, dict] = {}
    for s in samples:
        if s.name not in ("container_memory_working_set_bytes",
                          "container_cpu_usage_seconds_total"):
            continue
        raw_name = s.labels.get("name") or s.labels.get("id") or ""
        if not _is_workload(raw_name, s.labels):
            continue
        entry = by_id.setdefault(raw_name, {
            "name": _short_name(s.labels, raw_name),
            "id": raw_name.rsplit("/", 1)[-1][:24],
            "mem_used_bytes": None,
            "cpu_seconds_total": None,
        })
        v = _num(s.value)
        if s.name == "container_memory_working_set_bytes":
            entry["mem_used_bytes"] = v
        else:
            entry["cpu_seconds_total"] = v
    containers = [c for c in by_id.values()
                  if c["mem_used_bytes"] is not None or c["cpu_seconds_total"] is not None]
    containers.sort(key=lambda c: -(c["mem_used_bytes"] or 0.0))
    return {
        "host": host,
        "source": "cadvisor",
        "containers": containers[:20],
        "container_count": len(containers),
    }


PARSERS = {
    "node_exporter": parse_node_exporter,
    "cadvisor": parse_cadvisor,
}


def parse(source: str, text: str, host: str) -> dict:
    """Dispatch on source; unknown sources raise ValueError (poller reports "unsupported")."""
    fn = PARSERS.get(source)
    if fn is None:
        raise ValueError(f"unknown node source {source!r}")
    return fn(text, host)
