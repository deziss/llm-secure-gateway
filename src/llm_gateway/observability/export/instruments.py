"""OpenTelemetry instruments for the gateway.

Counters and histograms are written per request by ``record_request``. Observable gauges
read the live store and the process snapshot at collection time, so they cost nothing
between scrapes or export ticks.

Existing 8 instruments keep their names and labels, with ``server`` and ``server_type``
added. New per-request instruments and all gauges carry only ``server``, ``model`` and
``server_type``, with no ``user_name`` (cardinality).

Final Prometheus names (the OTel exporter adds no unit suffixes because every instrument
here uses an empty unit; counters get ``_total`` through prometheus_client):
  llm_gateway_requests_total, llm_gateway_requests_success_total,
  llm_gateway_requests_failed_total, llm_gateway_prompt_tokens_total,
  llm_gateway_completion_tokens_total, llm_gateway_total_tokens_total,
  llm_gateway_request_duration_seconds, llm_gateway_backend_latency_seconds,
  llm_gateway_time_to_first_token_seconds, llm_gateway_generation_tokens_per_second,
  llm_gateway_model_requests{state}, llm_gateway_model_output_tokens_per_second,
  llm_gateway_inflight_buffer_bytes, llm_gateway_process_memory_bytes{type},
  llm_gateway_process_threads, llm_gateway_asyncio_tasks
"""
from __future__ import annotations

import logging
import time

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation

logger = logging.getLogger(__name__)

_I: dict = {}
_PROC_CACHE: dict = {"at": 0.0, "snap": None}
_PROC_TTL_S = 1.0


def _proc_snapshot() -> dict:
    """process_snapshot() shared by the gauge callbacks within one collection."""
    now = time.monotonic()
    if _PROC_CACHE["snap"] is None or now - _PROC_CACHE["at"] > _PROC_TTL_S:
        from ..collect.process import process_snapshot
        _PROC_CACHE["snap"] = process_snapshot()
        _PROC_CACHE["at"] = now
    return _PROC_CACHE["snap"]


def _cb_model_requests(options: CallbackOptions):
    from ..store import get_live_stats
    for row in get_live_stats().snapshot():
        base = {"server": row["server"], "model": row["model"]}
        yield Observation(row["waiting"], {**base, "state": "waiting"})
        yield Observation(row["generating"], {**base, "state": "generating"})


def _cb_output_tps(options: CallbackOptions):
    from ..store import get_live_stats
    for row in get_live_stats().snapshot():
        yield Observation(row["tok_s_throughput"], {"server": row["server"], "model": row["model"]})


def _cb_inflight(options: CallbackOptions):
    from ..store import get_live_stats
    for row in get_live_stats().snapshot():
        yield Observation(row["inflight_bytes"], {"server": row["server"], "model": row["model"]})


def _cb_memory(options: CallbackOptions):
    snap = _proc_snapshot()
    if snap.get("rss_bytes") is not None:
        yield Observation(snap["rss_bytes"], {"type": "rss"})
    if snap.get("uss_bytes") is not None:
        yield Observation(snap["uss_bytes"], {"type": "uss"})


def _cb_threads(options: CallbackOptions):
    snap = _proc_snapshot()
    if snap.get("num_threads") is not None:
        yield Observation(snap["num_threads"])


def _cb_tasks(options: CallbackOptions):
    snap = _proc_snapshot()
    if snap.get("asyncio_tasks") is not None:
        yield Observation(snap["asyncio_tasks"])


def setup_instruments(meter=None) -> None:
    """Create every instrument on ``meter`` (default: the global meter). Safe to call once per provider."""
    meter = meter or metrics.get_meter("llm_gateway_metrics")
    _I["meter"] = meter

    # Existing counters (names unchanged)
    _I["req"] = meter.create_counter("llm_gateway_requests_total", description="Total LLM Gateway requests")
    _I["ok"] = meter.create_counter("llm_gateway_requests_success_total", description="Total successful requests")
    _I["fail"] = meter.create_counter("llm_gateway_requests_failed_total", description="Total failed requests")
    _I["prompt"] = meter.create_counter("llm_gateway_prompt_tokens_total", description="Total prompt tokens used")
    _I["completion"] = meter.create_counter("llm_gateway_completion_tokens_total", description="Total completion tokens used")
    _I["total"] = meter.create_counter("llm_gateway_total_tokens_total", description="Total tokens used (prompt + completion)")
    _I["duration"] = meter.create_histogram("llm_gateway_request_duration_seconds", description="Total gateway request duration in seconds")
    _I["backend_latency"] = meter.create_histogram("llm_gateway_backend_latency_seconds", description="Backend response latency in seconds")

    # New per-request histograms
    _I["ttft"] = meter.create_histogram("llm_gateway_time_to_first_token_seconds", description="Time from dispatch to first upstream byte, seconds")
    _I["tps"] = meter.create_histogram("llm_gateway_generation_tokens_per_second", description="Per-request decode speed, output tokens per second")

    # Observable gauges (read at collection time)
    meter.create_observable_gauge("llm_gateway_model_requests", callbacks=[_cb_model_requests],
                                  description="Requests waiting for a first byte or generating, per server and model")
    meter.create_observable_gauge("llm_gateway_model_output_tokens_per_second", callbacks=[_cb_output_tps],
                                  description="Aggregate output tokens per second over 60 s, per server and model")
    meter.create_observable_gauge("llm_gateway_inflight_buffer_bytes", callbacks=[_cb_inflight],
                                  description="Response bytes buffered by in-flight requests, per server and model")
    meter.create_observable_gauge("llm_gateway_process_memory_bytes", callbacks=[_cb_memory],
                                  description="Gateway process memory, type=rss|uss")
    meter.create_observable_gauge("llm_gateway_process_threads", callbacks=[_cb_threads],
                                  description="Gateway process OS thread count")
    meter.create_observable_gauge("llm_gateway_asyncio_tasks", callbacks=[_cb_tasks],
                                  description="Asyncio tasks on the gateway event loop")

    try:
        from .server_gauges import register_server_gauges  # owned by the servers/GPU work
        register_server_gauges(meter)
    except ImportError:
        logger.debug("server gauges module not available; skipping")
    except Exception:
        logger.warning("failed to register server gauges", exc_info=True)


def is_ready() -> bool:
    return "req" in _I


def record_request(
    *,
    backend_url: str,
    model: str,
    endpoint_uri: str,
    user_name: str,
    success: bool = True,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    request_duration: float = 0.0,
    backend_latency: float = 0.0,
    server: str = "",
    server_type: str = "",
    time_to_first_token: float = 0.0,
    generation_tps: float = 0.0,
) -> None:
    if not is_ready():
        return
    server = server or backend_url
    attrs = {
        "backend_url": backend_url,
        "model": model,
        "endpoint_uri": endpoint_uri,
        "user_name": user_name,
        "server": server,
        "server_type": server_type or "unknown",
    }
    _I["req"].add(1, attributes=attrs)
    (_I["ok"] if success else _I["fail"]).add(1, attributes=attrs)
    _I["prompt"].add(prompt_tokens, attributes=attrs)
    _I["completion"].add(completion_tokens, attributes=attrs)
    total = total_tokens if total_tokens > 0 else (prompt_tokens + completion_tokens)
    _I["total"].add(total, attributes=attrs)
    if request_duration > 0:
        _I["duration"].record(request_duration, attributes=attrs)
    if backend_latency > 0:
        _I["backend_latency"].record(backend_latency, attributes=attrs)

    lab = {"server": server, "model": model, "server_type": server_type or "unknown"}
    if time_to_first_token > 0:
        _I["ttft"].record(time_to_first_token, attributes=lab)
    if generation_tps > 0:
        _I["tps"].record(generation_tps, attributes=lab)
