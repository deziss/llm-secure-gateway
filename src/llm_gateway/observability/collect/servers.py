"""Model-server scrapers: vLLM /metrics, llama.cpp /metrics (+ /slots), Ollama /api/ps.

Each scraper returns a dict of ServerStat fields. Failures become a status instead of
raising: "unreachable" (network, timeout, HTTP error, auth), "unsupported" (server type
has no metrics source, or the endpoint is not exposed). Metric values are gateway-side
reads only; nothing is sent to the server except GET requests.

Counter-based rates (vLLM generation/prompt tokens) are computed from the previous
successful scrape of the same server, so the first scrape reports None.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import httpx

from . import promtext

logger = logging.getLogger(__name__)

SUPPORTED_TYPES = {"vllm", "llamacpp", "ollama"}

# name -> (previous scrape time, generation counter, prompt counter)
_prev_counters: dict[str, tuple[float, float | None, float | None]] = {}
# (server, metric, label value) -> (previous scrape time, counter)
_prev_series: dict[tuple[str, str, str], tuple[float, float]] = {}


class ScrapeUnsupported(Exception):
    """The server is reachable but does not expose the data we need."""


def server_root(base_url: str) -> str:
    """Metrics and /api/ps live at the server root, not under /v1."""
    base = (base_url or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base


def _headers(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _int(v: float | None) -> int | None:
    return int(v) if v is not None else None


def _rate(name: str, now: float, gen: float | None, prompt: float | None) -> tuple[float | None, float | None]:
    prev = _prev_counters.get(name)
    _prev_counters[name] = (now, gen, prompt)
    if prev is None:
        return None, None
    dt = now - prev[0]
    if dt <= 0:
        return None, None

    def rate(cur: float | None, old: float | None) -> float | None:
        if cur is None or old is None or cur < old:  # missing, or the counter reset (restart)
            return None
        return (cur - old) / dt

    return rate(gen, prev[1]), rate(prompt, prev[2])


async def _get(client: httpx.AsyncClient, url: str, api_key: str | None) -> httpx.Response:
    r = await client.get(url, headers=_headers(api_key))
    if r.status_code in (401, 403):
        raise httpx.HTTPError(f"auth rejected (HTTP {r.status_code})")
    r.raise_for_status()
    return r


def _series_rate(server: str, metric: str, label: str, now: float, value: float | None) -> float | None:
    """Per-second rate of one labelled counter series since the previous scrape."""
    key = (server, metric, label)
    prev = _prev_series.get(key)
    if value is None:
        return None
    _prev_series[key] = (now, value)
    if prev is None:
        return None
    dt = now - prev[0]
    if dt <= 0 or value < prev[1]:  # first sample, clock skew, or counter reset
        return None
    return (value - prev[1]) / dt


def _by_label(samples: list, name: str, label: str) -> dict[str, float]:
    """Sum one metric's samples per value of `label` (samples without the label are skipped)."""
    out: dict[str, float] = {}
    for smp in samples:
        if smp.name == name and label in smp.labels:
            out[smp.labels[label]] = out.get(smp.labels[label], 0.0) + smp.value
    return out


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def match_engine_model(engine_url: str, models: list[str]) -> str | None:
    """Best-effort engine -> model mapping for a vLLM router.

    The production-stack router labels per-engine metrics with the engine URL only. Engine
    hostnames are usually named after the model ("llama31.internal" serves "llama-3.1-8b"),
    so match the first hostname label against model names with punctuation removed. Returns
    None when nothing or more than one model matches equally well.
    """
    host = urlparse(engine_url).hostname or engine_url
    key = _alnum(host.split(".")[0])
    if not key:
        return None
    scored: list[tuple[int, str]] = []
    for m in models:
        norm = _alnum(m)
        if norm and (norm.startswith(key) or key.startswith(norm)):
            common = len(next((norm[:i] for i in range(min(len(norm), len(key)), 0, -1)
                               if norm[:i] == key[:i]), ""))
            scored.append((common, m))
    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


def _is_router(samples: list) -> bool:
    names = {smp.name for smp in samples}
    return "vllm:healthy_pods_total" in names or "vllm:num_incoming_requests_total" in names


def _scrape_router(target: dict, samples: list, now: float) -> dict[str, Any]:
    """vLLM production-stack router: per-engine series labelled server=<engine url>,
    plus per-model incoming request counters labelled model=<name>."""
    name = target["name"]
    models = list(target.get("models") or [])
    running = _by_label(samples, "vllm:num_requests_running", "server")
    waiting = _by_label(samples, "vllm:num_requests_waiting", "server")
    prefill = _by_label(samples, "vllm:num_prefill_requests", "server")
    decoding = _by_label(samples, "vllm:num_decoding_requests", "server")
    qps = _by_label(samples, "vllm:current_qps", "server")
    itl = _by_label(samples, "vllm:avg_itl", "server")
    healthy = _by_label(samples, "vllm:healthy_pods_total", "server")
    out_tok = _by_label(samples, "vllm:output_tokens_total", "server")
    in_tok = _by_label(samples, "vllm:input_tokens_total", "server")
    incoming = _by_label(samples, "vllm:num_incoming_requests_total", "model")

    def _nonneg(v: float | None) -> float | None:
        return v if v is not None and v >= 0 else None  # the router reports -1 for "no data"

    engines = []
    for url in sorted(set(running) | set(out_tok) | set(healthy)):
        engines.append({
            "url": url,
            "model": match_engine_model(url, models),
            "healthy": bool(healthy.get(url)) if url in healthy else None,
            "running": _int(running.get(url)),
            "waiting": _int(waiting.get(url)),
            "prefill": _int(prefill.get(url)),
            "decoding": _int(decoding.get(url)),
            "qps": _nonneg(qps.get(url)),
            "itl_ms": _nonneg(itl.get(url)) * 1000.0 if _nonneg(itl.get(url)) is not None else None,
            "out_tok_s": _series_rate(name, "out", url, now, out_tok.get(url)),
            "in_tok_s": _series_rate(name, "in", url, now, in_tok.get(url)),
        })

    # One engine and one model left over after name matching: they belong together.
    unmatched_engines = [e for e in engines if e["model"] is None]
    unmatched_models = [m for m in models if m not in {e["model"] for e in engines}]
    if len(unmatched_engines) == 1 and len(unmatched_models) == 1:
        unmatched_engines[0]["model"] = unmatched_models[0]

    model_stats: dict[str, dict[str, Any]] = {}
    for m in models:
        mine = [e for e in engines if e["model"] == m]
        rps = _series_rate(name, "incoming", m, now, incoming.get(m))

        def _sum(field: str) -> float | None:
            vals = [e[field] for e in mine if e[field] is not None]
            return sum(vals) if vals else None

        itls = [e["itl_ms"] for e in mine if e["itl_ms"] is not None]
        model_stats[m] = {
            "rps": rps,
            "running": _int(_sum("running")),
            "waiting": _int(_sum("waiting")),
            "out_tok_s": _sum("out_tok_s"),
            "itl_ms": sum(itls) / len(itls) if itls else None,
            "kv_cache_pct": None,
            "engines": len(mine),
        }

    def _total(field: str) -> float | None:
        vals = [e[field] for e in engines if e[field] is not None]
        return sum(vals) if vals else None

    return {
        "kind": "vllm-router",
        "running": _int(_total("running")),
        "waiting": _int(_total("waiting")),
        "kv_cache_pct": None,
        "gen_tok_s": _total("out_tok_s"),
        "prompt_tok_s": _total("in_tok_s"),
        "loaded_models": [],
        "engines": engines,
        "model_stats": model_stats,
    }


async def scrape_vllm(client: httpx.AsyncClient, target: dict, now: float) -> dict[str, Any]:
    root = server_root(target["base_url"])
    samples = promtext.parse((await _get(client, f"{root}/metrics", target.get("api_key"))).text)
    if not any(s.name.startswith("vllm:") for s in samples):
        raise ScrapeUnsupported("no vllm:* metrics at /metrics")
    if _is_router(samples):
        return _scrape_router(target, samples, now)
    kv = promtext.total(samples, "vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc")  # 0..1 ratio
    gen = promtext.total(samples, "vllm:generation_tokens_total")
    prompt = promtext.total(samples, "vllm:prompt_tokens_total")
    gen_s, prompt_s = _rate(target["name"], now, gen, prompt)

    # A vLLM engine labels its series with model_name; one server can serve several names.
    model_stats: dict[str, dict[str, Any]] = {}
    gen_by = _by_label(samples, "vllm:generation_tokens_total", "model_name")
    req_by = _by_label(samples, "vllm:request_success_total", "model_name")
    kv_by = _by_label(samples, "vllm:kv_cache_usage_perc", "model_name") or \
        _by_label(samples, "vllm:gpu_cache_usage_perc", "model_name")
    run_by = _by_label(samples, "vllm:num_requests_running", "model_name")
    wait_by = _by_label(samples, "vllm:num_requests_waiting", "model_name")
    for m in sorted(set(run_by) | set(gen_by)):
        model_stats[m] = {
            "rps": _series_rate(target["name"], "success", m, now, req_by.get(m)),
            "running": _int(run_by.get(m)),
            "waiting": _int(wait_by.get(m)),
            "out_tok_s": _series_rate(target["name"], "gen", m, now, gen_by.get(m)),
            "itl_ms": None,
            "kv_cache_pct": kv_by[m] * 100.0 if kv_by.get(m) is not None else None,
            "engines": 1,
        }
    return {
        "kind": "vllm",
        "running": _int(promtext.total(samples, "vllm:num_requests_running")),
        "waiting": _int(promtext.total(samples, "vllm:num_requests_waiting")),
        "kv_cache_pct": kv * 100.0 if kv is not None else None,
        "gen_tok_s": gen_s,
        "prompt_tok_s": prompt_s,
        "loaded_models": [],
        "engines": [],
        "model_stats": model_stats,
    }


async def scrape_llamacpp(client: httpx.AsyncClient, target: dict, now: float) -> dict[str, Any]:
    root = server_root(target["base_url"])
    key = target.get("api_key")
    try:
        samples = promtext.parse((await _get(client, f"{root}/metrics", key)).text)
        if any(s.name.startswith("llamacpp:") for s in samples):
            return {
                "running": _int(promtext.total(samples, "llamacpp:requests_processing")),
                "waiting": _int(promtext.total(samples, "llamacpp:requests_deferred")),
                "kv_cache_pct": None,
                "gen_tok_s": promtext.total(samples, "llamacpp:predicted_tokens_seconds"),
                "prompt_tok_s": None,
                "loaded_models": [],
            }
    except httpx.HTTPStatusError:
        pass  # /metrics not enabled on this build: fall through to /slots
    try:
        slots = (await _get(client, f"{root}/slots", key)).json()
    except httpx.HTTPStatusError as e:
        raise ScrapeUnsupported("start llama-server with --metrics or --slots") from e
    if not isinstance(slots, list):
        raise ScrapeUnsupported("unexpected /slots payload")
    busy = sum(1 for s in slots if isinstance(s, dict) and s.get("is_processing"))
    return {"running": busy, "waiting": None, "kv_cache_pct": None, "gen_tok_s": None,
            "prompt_tok_s": None, "loaded_models": []}


async def scrape_ollama(client: httpx.AsyncClient, target: dict, now: float) -> dict[str, Any]:
    root = server_root(target["base_url"])
    data = (await _get(client, f"{root}/api/ps", target.get("api_key"))).json()
    loaded = [
        {
            "name": m.get("name") or m.get("model"),
            "size_bytes": _int(m.get("size")),
            "vram_bytes": _int(m.get("size_vram")),
        }
        for m in (data.get("models") or []) if isinstance(m, dict)
    ]
    # Ollama has no queue or token-rate metric; those stay gateway-side only.
    return {"running": None, "waiting": None, "kv_cache_pct": None, "gen_tok_s": None,
            "prompt_tok_s": None, "loaded_models": loaded}


_SCRAPERS: dict[str, Callable[[httpx.AsyncClient, dict, float], Awaitable[dict[str, Any]]]] = {
    "vllm": scrape_vllm,
    "llamacpp": scrape_llamacpp,
    "ollama": scrape_ollama,
}


def base_stat(target: dict) -> dict[str, Any]:
    return {
        "name": target["name"],
        "server_type": target.get("server_type"),
        "url": target.get("base_url"),
        "status": "unreachable",
        "fetched_at": None,
        "error": None,
        "running": None,
        "waiting": None,
        "kv_cache_pct": None,
        "gen_tok_s": None,
        "prompt_tok_s": None,
        "loaded_models": [],
        "kind": target.get("server_type"),
        "models": list(target.get("models") or []),
        "engines": [],
        "model_stats": {},
        "gpu_host": target.get("gpu_host"),
        "gpu_indices": target.get("gpu_indices"),
    }


async def scrape_server(client: httpx.AsyncClient, target: dict, now: float | None = None) -> dict[str, Any]:
    """One scrape. Never raises for network or protocol problems; see module docstring."""
    now = time.time() if now is None else now
    stat = base_stat(target)
    scraper = _SCRAPERS.get(target.get("server_type") or "")
    if scraper is None:
        stat["status"] = "unsupported"
        stat["error"] = f"no metrics source for server type {target.get('server_type')!r}"
        return stat
    try:
        stat.update(await scraper(client, target, now))
        stat["status"] = "ok"
    except ScrapeUnsupported as e:
        stat["status"] = "unsupported"
        stat["error"] = str(e)
    except httpx.TimeoutException:
        stat["error"] = "timeout"
    except httpx.HTTPStatusError as e:
        stat["error"] = f"HTTP {e.response.status_code}"
    except httpx.HTTPError as e:
        stat["error"] = str(e)[:200] or type(e).__name__
    except (ValueError, TypeError, AttributeError) as e:
        stat["status"] = "unsupported"
        stat["error"] = f"unexpected response: {type(e).__name__}"
    return stat
