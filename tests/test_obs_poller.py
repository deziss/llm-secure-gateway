"""Poller: one overall deadline per tick, DB closed before I/O, backoff, circuit skip, GPU agent."""
import asyncio
import time

import httpx
import pytest

from llm_gateway.observability.collect import gpu, poller, servers

VLLM_TEXT = """vllm:num_requests_running{model_name="qwen"} 3
vllm:num_requests_waiting{model_name="qwen"} 1
vllm:kv_cache_usage_perc{model_name="qwen"} 0.42
vllm:generation_tokens_total{model_name="qwen"} 1000
vllm:prompt_tokens_total{model_name="qwen"} 500
"""


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    poller._servers.clear()
    poller._hosts.clear()
    poller._backoff.clear()
    poller._targets.clear()
    monkeypatch.setenv("OBS_POLL_INTERVAL", "1")
    monkeypatch.delenv("GPU_AGENT_TOKEN", raising=False)
    yield


def _server(name, url, gpu_host=None, models=("m",), stype="vllm"):
    return {"name": name, "server_type": stype, "base_url": url, "api_key": None,
            "models": list(models), "gpu_host": gpu_host, "gpu_indices": None}


def _config(targets, hosts=(), state=None):
    async def fake_read():
        if state is not None:
            state["config_done"] = True
        return list(targets), list(hosts), {}
    return fake_read


async def test_black_holed_targets_cannot_stall_the_tick(monkeypatch):
    """Three backends that never answer: the tick must end at the 0.8 x interval deadline."""
    state = {"config_done": False, "io_before_config": False}

    async def blackhole(request):
        if not state["config_done"]:
            state["io_before_config"] = True  # DB session must be closed before any I/O
        await asyncio.sleep(30)

    targets = [_server(f"slow-{i}", f"http://blackhole-{i}.invalid:8000") for i in range(3)]
    monkeypatch.setattr(poller, "_read_config", _config(targets, state=state))
    client = httpx.AsyncClient(transport=httpx.MockTransport(blackhole))

    t0 = time.monotonic()
    await poller.poll_once(interval=1.0, client=client)
    elapsed = time.monotonic() - t0
    await client.aclose()

    assert elapsed < 1.5, f"tick took {elapsed:.2f}s, deadline is 0.8s"
    assert state["io_before_config"] is False
    snap = {s["name"]: s for s in poller.get_snapshot()["servers"]}
    assert set(snap) == {"slow-0", "slow-1", "slow-2"}
    assert all(s["status"] == "unreachable" and "deadline" in s["error"] for s in snap.values())
    assert all(f"server:slow-{i}" in poller._backoff for i in range(3))


async def test_backoff_skips_target_until_next_attempt(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(poller, "_clock", lambda: clock["now"])
    calls = {"n": 0}

    async def failing(request):
        calls["n"] += 1
        return httpx.Response(500)

    monkeypatch.setattr(poller, "_read_config", _config([_server("flaky", "http://flaky.invalid:8000")]))
    client = httpx.AsyncClient(transport=httpx.MockTransport(failing))

    await poller.poll_once(interval=1.0, client=client)          # attempt 1 -> fails, next_at = 101
    assert calls["n"] == 1
    assert poller.get_snapshot()["servers"][0]["status"] == "unreachable"

    clock["now"] = 100.5
    await poller.poll_once(interval=1.0, client=client)          # still backing off: no request
    assert calls["n"] == 1
    assert poller.get_snapshot()["servers"][0]["status"] == "backoff"

    clock["now"] = 101.5
    await poller.poll_once(interval=1.0, client=client)          # attempt 2 -> next delay 2 s
    assert calls["n"] == 2
    clock["now"] = 102.5
    await poller.poll_once(interval=1.0, client=client)          # 102.5 < 103.5: skipped
    assert calls["n"] == 2
    await client.aclose()


async def test_open_circuit_is_skipped_without_request(monkeypatch):
    class Open:
        def is_open(self, url):
            return True

    hit = {"n": 0}

    async def handler(request):
        hit["n"] += 1
        return httpx.Response(200, text=VLLM_TEXT)

    monkeypatch.setattr(poller, "get_circuit_breaker", lambda: Open())
    monkeypatch.setattr(poller, "_read_config", _config([_server("tripped", "http://tripped.invalid:8000")]))
    await poller.poll_once(interval=1.0, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert hit["n"] == 0
    assert poller.get_snapshot()["servers"][0]["status"] == "circuit_open"


async def test_vllm_scrape_and_counter_rate(monkeypatch):
    counter = {"gen": 1000.0}

    def text():
        return VLLM_TEXT.replace("1000", str(int(counter["gen"])))

    async def handler(request):
        assert request.url.path == "/metrics"
        return httpx.Response(200, text=text())

    target = _server("vllm-rate", "http://vllm-rate.invalid:8000/v1")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    first = await servers.scrape_server(client, target, now=100.0)
    assert first["status"] == "ok" and first["running"] == 3 and first["waiting"] == 1
    assert first["kv_cache_pct"] == pytest.approx(42.0)
    assert first["gen_tok_s"] is None  # no previous sample yet

    counter["gen"] = 1600.0
    second = await servers.scrape_server(client, target, now=110.0)
    assert second["gen_tok_s"] == pytest.approx(60.0)  # (1600-1000)/10 s
    await client.aclose()


async def test_unsupported_and_unknown_types_do_not_error(monkeypatch):
    async def handler(request):
        return httpx.Response(200, text="<html>no metrics here</html>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await servers.scrape_server(client, _server("openai-x", "http://x", stype="openai"), now=1.0)
    assert out["status"] == "unsupported"
    out = await servers.scrape_server(client, _server("vllm-html", "http://y"), now=1.0)
    assert out["status"] == "unsupported" and "vllm" in out["error"]
    await client.aclose()


async def test_agent_host_is_read_with_token_and_processes_attributed(monkeypatch):
    monkeypatch.setenv("GPU_AGENT_TOKEN", "tok-123")
    seen = {}

    async def handler(request):
        if request.url.path == "/v1/gpu":
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"gpus": [{
                "index": 0, "uuid": "GPU-0", "name": "A100", "vendor": "nvidia", "util_pct": 90,
                "mem_used_mib": 20000, "mem_total_mib": 81920,
                "processes": [{"pid": 11, "name": "VLLM::Worker", "used_mib": 19000,
                               "model_hint": "Qwen/Qwen2.5-7B", "container_id": None}],
            }]})
        if request.url.host == "vllm-a.invalid":
            return httpx.Response(200, text=VLLM_TEXT)
        return httpx.Response(404)

    targets = [_server("vllm-a", "http://vllm-a.invalid:8000", gpu_host="gpu-a", models=["Qwen/Qwen2.5-7B"])]
    hosts = [{"name": "gpu-a", "url": "http://gpu-a.invalid:9400", "source": "agent", "enabled": True}]
    monkeypatch.setattr(poller, "_read_config", _config(targets, hosts))
    await poller.poll_once(interval=1.0, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    assert seen["auth"] == "Bearer tok-123"
    snap = poller.get_snapshot()
    host = snap["gpu_hosts"][0]
    assert host["status"] == "ok" and host["gpus"][0]["processes"][0]["server"] == "vllm-a"
    srv = snap["servers"][0]
    gpus, how = gpu.server_gpus(srv, snap["gpu_hosts"])
    assert how == "process" and gpus[0]["index"] == 0


async def test_agent_without_token_is_unsupported_not_a_crash(monkeypatch):
    hosts = [{"name": "gpu-b", "url": "http://gpu-b.invalid:9400", "source": "agent", "enabled": True}]
    monkeypatch.setattr(poller, "_read_config", _config([], hosts))
    await poller.poll_once(interval=1.0, client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: None)))
    assert poller.get_snapshot()["gpu_hosts"][0]["status"] == "unsupported"


async def test_disabled_host_is_not_polled(monkeypatch):
    hits = {"n": 0}

    async def handler(request):
        hits["n"] += 1
        return httpx.Response(200, text="")

    hosts = [{"name": "off", "url": "http://off.invalid:9400", "source": "dcgm", "enabled": False}]
    monkeypatch.setattr(poller, "_read_config", _config([], hosts))
    await poller.poll_once(interval=1.0, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert hits["n"] == 0
    assert poller.get_snapshot()["gpu_hosts"][0]["status"] == "disabled"
