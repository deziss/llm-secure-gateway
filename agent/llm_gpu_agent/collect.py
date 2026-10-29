"""GPU collectors: NVIDIA via NVML (pynvml), AMD via amdsmi or the rocm-smi CLI.

Each collector returns a list of GPU dicts:
  index, uuid, name, vendor, util_pct, mem_used_mib, mem_total_mib,
  temp_c, power_w, power_limit_w, processes: [{pid, used_mib}]
Values that the driver does not provide are None. Collectors never raise from sample().
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from typing import Any, Optional

logger = logging.getLogger(__name__)

MIB = 1024 * 1024
_NOT_AVAILABLE_BYTES = 2**60  # NVML returns a huge sentinel when per-process memory is unknown


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _text(v: Any) -> Optional[str]:
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    return str(v) if v else None


class NvidiaCollector:
    """NVML via the `pynvml` module (nvidia-ml-py). Pass `nvml` to inject a fake in tests."""

    def __init__(self, nvml: Any = None) -> None:
        if nvml is None:
            import pynvml as nvml  # type: ignore[import-not-found]
        self._nvml = nvml
        nvml.nvmlInit()
        self._count = nvml.nvmlDeviceGetCount()

    def close(self) -> None:
        _safe(self._nvml.nvmlShutdown)

    def sample(self) -> list[dict]:
        n = self._nvml
        out: list[dict] = []
        for i in range(self._count):
            try:
                h = n.nvmlDeviceGetHandleByIndex(i)
                util = _safe(lambda: n.nvmlDeviceGetUtilizationRates(h).gpu)
                mem = _safe(lambda: n.nvmlDeviceGetMemoryInfo(h))
                procs = []
                for p in _safe(lambda: n.nvmlDeviceGetComputeRunningProcesses(h), []) or []:
                    used = getattr(p, "usedGpuMemory", None)
                    procs.append({
                        "pid": int(p.pid),
                        "used_mib": used / MIB if used and used < _NOT_AVAILABLE_BYTES else None,
                    })
                out.append({
                    "index": i,
                    "uuid": _text(_safe(lambda: n.nvmlDeviceGetUUID(h))),
                    "name": _text(_safe(lambda: n.nvmlDeviceGetName(h))),
                    "vendor": "nvidia",
                    "util_pct": float(util) if util is not None else None,
                    "mem_used_mib": mem.used / MIB if mem else None,
                    "mem_total_mib": mem.total / MIB if mem else None,
                    "temp_c": _safe(lambda: n.nvmlDeviceGetTemperature(h, 0)),
                    "power_w": _safe(lambda: n.nvmlDeviceGetPowerUsage(h) / 1000.0),
                    "power_limit_w": _safe(lambda: n.nvmlDeviceGetEnforcedPowerLimit(h) / 1000.0),
                    "processes": procs,
                })
            except Exception as e:  # one bad device must not hide the others
                logger.warning("nvml read failed for gpu %s: %s", i, e)
        return out


class AmdCollector:
    """AMD via `amdsmi` when importable, else the `rocm-smi --json` CLI (no process list)."""

    ROCM_SMI_CMD = ["rocm-smi", "--showuse", "--showmeminfo", "vram", "--showpids", "--json"]

    def __init__(self, amdsmi: Any = None, rocm_smi_path: Optional[str] = None) -> None:
        self._amdsmi = None
        self._rocm = None
        try:
            if amdsmi is None:
                import amdsmi  # type: ignore[import-not-found]
            amdsmi.amdsmi_init()
            self._amdsmi = amdsmi
            self._handles = list(amdsmi.amdsmi_get_processor_handles())
        except ImportError:
            self._rocm = rocm_smi_path or shutil.which("rocm-smi")
        except Exception as e:
            logger.warning("amdsmi init failed (%s); trying rocm-smi", e)
            self._rocm = rocm_smi_path or shutil.which("rocm-smi")

    @property
    def available(self) -> bool:
        return self._amdsmi is not None or self._rocm is not None

    def sample(self) -> list[dict]:
        if self._amdsmi is not None:
            return self._sample_amdsmi()
        if self._rocm:
            return self._sample_rocm()
        return []

    def _sample_amdsmi(self) -> list[dict]:
        a = self._amdsmi
        out = []
        for i, h in enumerate(self._handles):
            act = _safe(lambda: a.amdsmi_get_gpu_activity(h), {}) or {}
            vram = _safe(lambda: a.amdsmi_get_gpu_vram_usage(h), {}) or {}
            power = _safe(lambda: a.amdsmi_get_power_info(h), {}) or {}
            total = vram.get("vram_total")
            used = vram.get("vram_used")
            out.append({
                "index": i,
                "uuid": _text(_safe(lambda: a.amdsmi_get_gpu_device_uuid(h))),
                "name": _text(_safe(lambda: a.amdsmi_get_gpu_asic_info(h).get("market_name"))),
                "vendor": "amd",
                "util_pct": act.get("gfx_activity"),
                "mem_used_mib": float(used) if used is not None else None,   # amdsmi reports MB
                "mem_total_mib": float(total) if total is not None else None,
                "temp_c": None,
                "power_w": power.get("average_socket_power"),
                "power_limit_w": None,
                "processes": [],  # per-process list not collected on amdsmi yet
            })
        return out

    def _sample_rocm(self) -> list[dict]:
        try:
            res = subprocess.run([self._rocm, *self.ROCM_SMI_CMD[1:]], capture_output=True, text=True,
                                 timeout=5, check=False)
            data = json.loads(res.stdout or "{}")
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            logger.warning("rocm-smi failed: %s", e)
            return []
        out = []
        for key, fields in data.items():
            m = re.fullmatch(r"card(\d+)", key)
            if not m or not isinstance(fields, dict):
                continue

            def field(prefix: str) -> Optional[float]:
                for k, v in fields.items():
                    if k.startswith(prefix):
                        try:
                            return float(v)
                        except (TypeError, ValueError):
                            return None
                return None

            total_b = field("VRAM Total Memory (B)")
            used_b = field("VRAM Total Used Memory (B)")
            out.append({
                "index": int(m.group(1)),
                "uuid": None,
                "name": None,
                "vendor": "amd",
                "util_pct": field("GPU use (%)"),
                "mem_used_mib": used_b / MIB if used_b is not None else None,
                "mem_total_mib": total_b / MIB if total_b is not None else None,
                "temp_c": None,
                "power_w": None,
                "power_limit_w": None,
                "processes": [],
            })
        return out
