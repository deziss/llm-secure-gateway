"""OTel observable gauges for model servers, GPUs and model groups.

Reads the poller snapshot at collection time, so the values are exactly what the
admin page shows. All instruments use unit "" so the Prometheus exporter does not
append a unit suffix; final names match the contract in the v0.14.0 plan.
"""
from __future__ import annotations

import logging

from ..collect import poller
from ..collect.gpu import MIB

logger = logging.getLogger(__name__)


def _attrs(**kw) -> dict:
    return {k: ("" if v is None else str(v)) for k, v in kw.items()}


def _servers_rows() -> list[tuple[float, dict]]:
    rows: list[tuple[float, dict]] = []
    for s in poller.get_snapshot()["servers"]:
        name = s.get("name")
        if s.get("running") is not None:
            rows.append((float(s["running"]), _attrs(server=name, state="running")))
        if s.get("waiting") is not None:
            rows.append((float(s["waiting"]), _attrs(server=name, state="waiting")))
    return rows


def _kv_rows() -> list[tuple[float, dict]]:
    return [
        (s["kv_cache_pct"] / 100.0, _attrs(server=s.get("name")))
        for s in poller.get_snapshot()["servers"] if s.get("kv_cache_pct") is not None
    ]


def _gen_rows() -> list[tuple[float, dict]]:
    return [
        (float(s["gen_tok_s"]), _attrs(server=s.get("name")))
        for s in poller.get_snapshot()["servers"] if s.get("gen_tok_s") is not None
    ]


def _up_rows() -> list[tuple[float, dict]]:
    return [
        (1.0 if s.get("status") == "ok" else 0.0, _attrs(server=s.get("name")))
        for s in poller.get_snapshot()["servers"]
    ]


def _gpu_rows(field: str, scale: float = 1.0) -> list[tuple[float, dict]]:
    rows = []
    for h in poller.get_snapshot()["gpu_hosts"]:
        for g in h.get("gpus") or []:
            v = g.get(field)
            if v is None:
                continue
            rows.append((float(v) * scale, _attrs(host=g.get("host"), gpu=g.get("index"),
                                                  uuid=g.get("uuid"), name=g.get("name"),
                                                  vendor=g.get("vendor"))))
    return rows


def _gpu_mem_rows(field: str) -> list[tuple[float, dict]]:
    return _gpu_rows(field, MIB)


def _gpu_process_rows() -> list[tuple[float, dict]]:
    rows = []
    for h in poller.get_snapshot()["gpu_hosts"]:
        for g in h.get("gpus") or []:
            for p in g.get("processes") or []:
                if p.get("used_mib") is None:
                    continue
                rows.append((float(p["used_mib"]) * MIB, _attrs(
                    host=g.get("host"), gpu=g.get("index"), pid=p.get("pid"), process=p.get("name"),
                    model=p.get("model_hint"), server=p.get("server"))))
    return rows


def _group_rows() -> list[tuple[float, dict]]:
    return [(1.0, _attrs(server=srv, model=model, group=group)) for srv, model, group in poller.model_groups()]


def register_server_gauges(meter) -> None:
    """Register the server, GPU and group gauges on an OTel meter."""
    from opentelemetry.metrics import Observation

    def _cb(fn):
        def callback(options):
            try:
                return [Observation(v, attrs) for v, attrs in fn()]
            except Exception:
                logger.debug("server gauge callback failed", exc_info=True)
                return []
        return callback

    specs = [
        ("llm_server_requests", "Requests running or waiting on a model server", _servers_rows),
        ("llm_server_kv_cache_usage_ratio", "KV cache usage of a model server (0..1)", _kv_rows),
        ("llm_server_generation_tokens_per_second", "Server-reported generation tokens per second", _gen_rows),
        ("llm_server_up", "1 when the last scrape of the model server succeeded", _up_rows),
        ("llm_gpu_utilization_ratio", "GPU utilisation (0..1)", lambda: _gpu_rows("util_pct", 0.01)),
        ("llm_gpu_memory_used_bytes", "GPU memory in use", lambda: _gpu_mem_rows("mem_used_mib")),
        ("llm_gpu_memory_total_bytes", "GPU memory capacity", lambda: _gpu_mem_rows("mem_total_mib")),
        ("llm_gpu_temperature_celsius", "GPU temperature", lambda: _gpu_rows("temp_c")),
        ("llm_gpu_power_watts", "GPU power draw", lambda: _gpu_rows("power_w")),
        ("llm_gpu_process_memory_bytes", "GPU memory used by one process (agent sources only)", _gpu_process_rows),
        ("llm_model_group_info", "Maps server+model to its model group (join helper)", _group_rows),
    ]
    for name, desc, fn in specs:
        meter.create_observable_gauge(name, callbacks=[_cb(fn)], description=desc, unit="")
