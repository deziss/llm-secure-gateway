"""/admin/observability endpoints: guards, payload shape, GPU dedupe, validation."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from llm_gateway.auth.models import Role
from llm_gateway.auth.users import current_active_user
from llm_gateway.database import get_session
from llm_gateway.observability import api
from llm_gateway.observability.collect import poller


class FakeSession:
    def __init__(self):
        self.rows = {}
        self.added = []
        self.closed = False

    async def get(self, model, key):
        return self.rows.get(key)

    def add(self, obj):
        self.added.append(obj)
        key = getattr(obj, "name", None) or getattr(obj, "key", None)
        if key:
            self.rows[key] = obj

    async def commit(self):
        pass

    async def delete(self, obj):
        self.rows.pop(getattr(obj, "name", None) or getattr(obj, "key", None), None)

    async def execute(self, *a, **k):
        raise AssertionError("unexpected query in test")

    async def close(self):
        self.closed = True


def _app(role, session=None, superuser=False):
    app = FastAPI()
    app.include_router(api.router)
    user = SimpleNamespace(id="u1", role=role, is_superuser=superuser)
    session = session or FakeSession()
    app.dependency_overrides[current_active_user] = lambda: user

    async def _sess():
        yield session

    app.dependency_overrides[get_session] = _sess
    return app


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def snapshot(monkeypatch):
    gpus = [{"host": "gpu-a", "index": 0, "name": "A100", "uuid": "GPU-0", "vendor": "nvidia",
             "util_pct": 80.0, "mem_util_pct": 25.0, "mem_used_mib": 20000.0, "mem_total_mib": 81920.0,
             "temp_c": 60, "power_w": 200, "power_limit_w": 400,
             "processes": [{"pid": 11, "name": "w", "used_mib": 19000.0, "model_hint": "qwen2.5-7b",
                            "container_id": None, "server": "vllm-a"}]},
            {"host": "gpu-a", "index": 1, "name": "A100", "uuid": "GPU-1", "vendor": "nvidia",
             "util_pct": 20.0, "mem_util_pct": 0.0, "mem_used_mib": 0.0, "mem_total_mib": 81920.0,
             "temp_c": 40, "power_w": 60, "power_limit_w": 400, "processes": []}]
    servers = [
        {"name": "vllm-a", "server_type": "vllm", "url": "http://a", "status": "ok", "fetched_at": "t",
         "error": None, "running": 3, "waiting": 2, "kv_cache_pct": 42.0, "gen_tok_s": 60.0,
         "prompt_tok_s": 10.0, "loaded_models": [], "gpu_host": "gpu-a", "gpu_indices": None},
        {"name": "vllm-b", "server_type": "vllm", "url": "http://b", "status": "unreachable", "fetched_at": "t",
         "error": "timeout", "running": None, "waiting": None, "kv_cache_pct": None, "gen_tok_s": None,
         "prompt_tok_s": None, "loaded_models": [], "gpu_host": "gpu-a", "gpu_indices": None},
    ]
    hosts = [{"name": "gpu-a", "source": "agent", "url": "http://g", "enabled": True, "status": "ok",
              "fetched_at": "t", "error": None, "gpus": gpus}]
    monkeypatch.setattr(poller, "get_snapshot", lambda: {"servers": servers, "gpu_hosts": hosts})
    monkeypatch.setattr(poller, "get_overrides", lambda: None)

    async def no_replicas():
        return None

    monkeypatch.setattr(poller, "read_replica_payloads", no_replicas)
    monkeypatch.setattr(api, "_instance", lambda: "gw-test-1")
    monkeypatch.setattr(api, "_sinks", lambda: [{"name": "prometheus", "signals": ["metrics"]}])
    monkeypatch.setattr(api, "_local_process", lambda: {"pid": 1, "rss_bytes": 123})

    def models():
        base = {"tok_s_decode": 30.0, "ttft_p50_ms": 120.0, "rps_10s": 1.0, "requests_60s": 60,
                "errors_60s": 0, "inflight_bytes": 0, "tokens_estimated": False}
        return [
            {**base, "server": "vllm-a", "server_type": "vllm", "model": "qwen2.5:7b", "rps_60s": 1.0,
             "waiting": 1, "generating": 2, "tok_s_throughput": 40.0},
            {**base, "server": "vllm-a", "server_type": "vllm", "model": "Qwen/Qwen2.5-7B-Instruct",
             "rps_60s": 0.5, "waiting": 0, "generating": 1, "tok_s_throughput": 20.0},
        ]

    monkeypatch.setattr(api, "_local_models", models)

    async def fake_overrides(session):
        return {"qwen-7b": ["qwen2.5*"]}

    monkeypatch.setattr(api, "load_overrides", fake_overrides)
    return servers, hosts


async def test_live_payload_shape_and_grouping(snapshot):
    async with _client(_app(Role.MANAGER)) as c:
        r = await c.get("/admin/observability/live")
    assert r.status_code == 200
    body = r.json()
    for key in ("instance", "replicas", "scope", "generated_at", "process", "models", "groups",
                "servers", "gpus", "sinks"):
        assert key in body
    assert body["scope"] == "replica" and body["replicas"] == ["gw-test-1"]
    assert body["models"][0]["group"] == "qwen-7b"

    (group,) = body["groups"]
    assert group["group"] == "qwen-7b"
    inst = {(i["server"], i["model"]): i for i in group["instances"]}
    first = inst[("vllm-a", "qwen2.5:7b")]
    assert first["gpu_attribution"] == "process"
    assert [g["index"] for g in first["gpus"]] == [0]
    assert first["server_running"] == 3 and first["kv_cache_pct"] == 42.0

    t = group["totals"]
    assert t["instance_count"] == 2 and t["gpu_count"] == 1          # both instances on GPU 0, counted once
    assert t["vram_used_mib"] == 20000.0 and t["vram_total_mib"] == 81920.0
    assert t["server_waiting"] == 2  # vllm-a's queue counted once, not per model
    assert t["waiting"] == 1 and t["generating"] == 3
    assert t["tok_s"] == pytest.approx(60.0)
    assert t["vram_pct"] == pytest.approx(20000 / 81920 * 100)


async def test_viewer_is_forbidden_on_every_observability_route(snapshot):
    async with _client(_app(Role.VIEWER)) as c:
        assert (await c.get("/admin/observability/live")).status_code == 403
        assert (await c.get("/admin/observability/groups")).status_code == 403
        assert (await c.get("/admin/observability/gpu-hosts")).status_code == 403
        assert (await c.get("/admin/observability/stream")).status_code == 403
        assert (await c.post("/admin/observability/tracemalloc", json={"enable": True})).status_code == 403


async def test_manager_can_read_but_not_change(snapshot):
    async with _client(_app(Role.MANAGER)) as c:
        assert (await c.put("/admin/observability/groups", json={"overrides": {}})).status_code == 403
        assert (await c.post("/admin/observability/gpu-hosts",
                             json={"name": "x", "url": "http://x", "source": "agent"})).status_code == 403
        assert (await c.post("/admin/observability/tracemalloc", json={"enable": True})).status_code == 403


async def test_admin_tracemalloc_toggle(snapshot, monkeypatch):
    from llm_gateway.observability.collect import process

    calls = []
    monkeypatch.setattr(process, "set_tracemalloc", lambda enabled: calls.append(enabled))
    async with _client(_app(Role.ADMIN)) as c:
        r = await c.post("/admin/observability/tracemalloc", json={"enable": True})
    assert r.status_code == 200 and r.json() == {"enabled": True} and calls == [True]


async def test_admin_groups_put_validates_and_stores(snapshot):
    session = FakeSession()
    async with _client(_app(Role.ADMIN, session)) as c:
        bad = await c.put("/admin/observability/groups", json={"overrides": {"g": []}})
        assert bad.status_code == 422
        ok = await c.put("/admin/observability/groups", json={"overrides": {"llama": ["llama3*"]}})
    assert ok.status_code == 200 and ok.json() == {"overrides": {"llama": ["llama3*"]}}
    assert session.rows["OBS_MODEL_GROUPS"].value == '{"llama": ["llama3*"]}'


async def test_gpu_host_crud_validation_and_conflict(snapshot):
    session = FakeSession()
    async with _client(_app(Role.ADMIN, session)) as c:
        bad_url = await c.post("/admin/observability/gpu-hosts",
                               json={"name": "g1", "url": "ftp://x", "source": "agent"})
        assert bad_url.status_code == 422
        bad_src = await c.post("/admin/observability/gpu-hosts",
                               json={"name": "g1", "url": "http://x:9400", "source": "magic"})
        assert bad_src.status_code == 422
        created = await c.post("/admin/observability/gpu-hosts",
                               json={"name": "g1", "url": "http://x:9400", "source": "dcgm"})
        assert created.status_code == 201 and created.json()["source"] == "dcgm"
        dup = await c.post("/admin/observability/gpu-hosts",
                           json={"name": "g1", "url": "http://y:9400", "source": "agent"})
        assert dup.status_code == 409
        missing = await c.put("/admin/observability/gpu-hosts/nope", json={"enabled": False})
        assert missing.status_code == 404


async def test_server_gpu_binding_requires_existing_host_and_server(snapshot):
    session = FakeSession()
    async with _client(_app(Role.ADMIN, session)) as c:
        r = await c.put("/admin/observability/servers/ghost/gpu", json={"gpu_host": None, "gpu_indices": None})
        assert r.status_code == 404
        bad_idx = await c.put("/admin/observability/servers/ghost/gpu",
                              json={"gpu_host": None, "gpu_indices": [0, 0]})
        assert bad_idx.status_code == 422


async def test_stream_closes_db_session_up_front(snapshot):
    """Called directly: httpx's ASGI transport buffers whole bodies, so an endless SSE
    stream cannot be driven through it. The handler must release the session before returning."""
    session = FakeSession()

    class Req:
        async def is_disconnected(self):
            return True

    resp = await api.stream(request=Req(), session=session, user=None)
    assert session.closed is True
    assert resp.media_type == "text/event-stream"
