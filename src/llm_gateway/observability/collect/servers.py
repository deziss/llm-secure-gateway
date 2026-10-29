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
import time
from typing import Any, Awaitable, Callable

import httpx

from . import promtext

logger = logging.getLogger(__name__)

SUPPORTED_TYPES = {"vllm", "llamacpp", "ollama"}

# name -> (previous scrape time, generation counter, prompt counter)
_prev_counters: dict[str, tuple[float, float | None, float | None]] = {}


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


async def scrape_vllm(client: httpx.AsyncClient, target: dict, now: float) -> dict[str, Any]:
    root = server_root(target["base_url"])
    samples = promtext.parse((await _get(client, f"{root}/metrics", target.get("api_key"))).text)
    if not any(s.name.startswith("vllm:") for s in samples):
        raise ScrapeUnsupported("no vllm:* metrics at /metrics")
    kv = promtext.total(samples, "vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc")  # 0..1 ratio
    gen = promtext.total(samples, "vllm:generation_tokens_total")
    prompt = promtext.total(samples, "vllm:prompt_tokens_total")
    gen_s, prompt_s = _rate(target["name"], now, gen, prompt)
    return {
        "running": _int(promtext.total(samples, "vllm:num_requests_running")),
        "waiting": _int(promtext.total(samples, "vllm:num_requests_waiting")),
        "kv_cache_pct": kv * 100.0 if kv is not None else None,
        "gen_tok_s": gen_s,
        "prompt_tok_s": prompt_s,
        "loaded_models": [],
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
