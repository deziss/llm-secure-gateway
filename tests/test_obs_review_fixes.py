"""Review fixes: upstream-status success flag, key eviction and cap, tracemalloc off the event loop,
cancel flag for on_complete, tracker safety net, URL redaction, poller skip on queued cancel."""
import asyncio
import threading
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from starlette.background import BackgroundTask

from llm_gateway.observability import store as store_mod
from llm_gateway.observability.collect import poller, process
from llm_gateway.observability.collect.requests import finish_open_tracker, track_dispatch
from llm_gateway.observability.export.sinks import SinkStatus, redact_url
from llm_gateway.observability.store import LiveStats


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class _Resp:
    def __init__(self, status, chunks=(b'{"done": true}',)):
        self.status_code = status
        self._chunks = chunks
        self.headers = {"content-type": "application/json"}

    async def aiter_raw(self):
        for c in self._chunks:
            yield c


def _span():
    s = MagicMock()
    s.is_recording.return_value = False
    return s


async def _stream(resp, **kw):
    from llm_gateway.telemetry.streaming import stream_with_telemetry
    async for _ in stream_with_telemetry(resp, 0, "http://u", "m", "/api/chat", "o", _span(), **kw):
        pass


# 1. upstream status ------------------------------------------------------
@pytest.mark.parametrize("status,ok", [(200, True), (399, True), (400, False), (500, False)])
async def test_clean_stream_with_upstream_error_status_is_a_failure(monkeypatch, status, ok):
    from llm_gateway.telemetry import streaming
    seen = {}
    monkeypatch.setattr(streaming, "record_request_metrics", lambda **kw: seen.update(kw))
    st = LiveStats()
    tracker = track_dispatch("s", "vllm", "m", store=st)
    await _stream(_Resp(status), tracker=tracker)
    assert seen["success"] is ok
    store_mod_row = st.snapshot()[0]
    assert store_mod_row["errors_60s"] == (0 if ok else 1)


# 5. cancelled flag -------------------------------------------------------
async def test_on_complete_gets_cancelled_flag_only_if_declared(monkeypatch):
    from llm_gateway.telemetry import streaming
    monkeypatch.setattr(streaming, "record_request_metrics", lambda **kw: None)

    class _Cancel(_Resp):
        async def aiter_raw(self):
            yield b"x"
            raise GeneratorExit

    got, legacy = [], []

    async def new_cb(p, c, chunks, cancelled=False):
        got.append(cancelled)

    async def old_cb(p, c, chunks):
        legacy.append(True)

    for cb in (new_cb, old_cb):
        with pytest.raises(GeneratorExit):
            await _stream(_Cancel(200), on_complete=cb)
    await _stream(_Resp(200), on_complete=new_cb)
    assert got == [True, False]
    assert legacy == [True]


# 3. store eviction and cap -----------------------------------------------
def test_idle_keys_are_evicted_but_busy_ones_stay():
    clk = _Clock()
    st = LiveStats(clock=clk)
    st.dispatch(("s", "vllm", "idle"))
    st.finish(("s", "vllm", "idle"), was_generating=False, success=True)
    st.dispatch(("s", "vllm", "busy"))  # still waiting
    clk.t += store_mod.IDLE_EVICT_S + 1
    st.snapshot()
    assert ("s", "vllm", "idle") not in st._keys
    assert ("s", "vllm", "busy") in st._keys


def test_models_per_server_are_capped_into_other(monkeypatch):
    monkeypatch.setattr(store_mod, "MAX_MODELS_PER_SERVER", 3)
    st = LiveStats(clock=_Clock())
    trackers = [track_dispatch("s", "vllm", f"m{i}", store=st) for i in range(6)]
    trackers.append(track_dispatch("t", "vllm", "m0", store=st))  # another server has its own budget
    models = {(r["server"], r["model"]) for r in st.snapshot()}
    assert models == {("s", "m0"), ("s", "m1"), ("s", "m2"), ("s", "other"), ("t", "m0")}
    for t in trackers:
        t.finish()
    assert all(r["waiting"] == 0 for r in st.snapshot())


# 4. tracemalloc off the loop ---------------------------------------------
def test_process_snapshot_never_takes_tracemalloc_snapshot_synchronously(monkeypatch):
    callers = []
    done = threading.Event()

    class _Snap:
        def statistics(self, _):
            return []

    def fake_take():
        callers.append(threading.current_thread())
        done.set()
        return _Snap()

    monkeypatch.setattr(process.tracemalloc, "take_snapshot", fake_take)
    monkeypatch.setattr(process.tracemalloc, "is_tracing", lambda: True)
    monkeypatch.setitem(process._tm_state, "enabled", True)
    monkeypatch.setitem(process._tm_state, "taken_at", None)
    monkeypatch.setitem(process._tm_state, "refreshing", False)
    process.process_snapshot()
    assert done.wait(5)
    assert callers and all(t is not threading.main_thread() for t in callers)
    for _ in range(100):  # wait for the worker to publish
        if process._tm_state["taken_at"]:
            break
        threading.Event().wait(0.02)
    assert process.tracemalloc_state()["taken_at"] is not None
    monkeypatch.setitem(process._tm_state, "enabled", False)


# 6. tracker safety net ----------------------------------------------------
def test_tracker_is_closed_by_response_background_task():
    st = LiveStats()
    tracker = track_dispatch("s", "vllm", "m", store=st)
    assert st.snapshot()[0]["waiting"] == 1
    app = FastAPI()

    @app.get("/x")
    async def x():
        async def gen():  # never touches the tracker
            yield b"hi"
        return StreamingResponse(gen(), background=BackgroundTask(finish_open_tracker, tracker))

    assert TestClient(app).get("/x").text == "hi"
    assert st.snapshot()[0]["waiting"] == 0
    assert tracker.finished
    finish_open_tracker(tracker)  # idempotent
    finish_open_tracker(None)


# 7. redaction ---------------------------------------------------------------
def test_redact_url_strips_userinfo_and_query():
    assert redact_url("https://user:pw@host:4318/v1/traces?api_key=abc#f") == "https://host:4318/v1/traces"
    assert redact_url("connect to http://a:b@h/x?token=1 failed") == "connect to http://h/x failed"
    assert redact_url(None) is None


def test_sink_status_redacts_endpoint_and_errors():
    s = SinkStatus(name="otlp", endpoint="https://u:p@h/v1?k=secret")
    s.fail(1, "HTTPSConnectionPool(https://u:p@h/v1?k=secret): refused")
    d = s.as_dict()
    assert "secret" not in str(d) and "u:p" not in str(d)


# 8. poller deadline ---------------------------------------------------------
async def test_target_queued_on_semaphore_at_deadline_is_skipped_not_backed_off(monkeypatch):
    poller._servers.clear(); poller._hosts.clear(); poller._backoff.clear(); poller._targets.clear()
    monkeypatch.setenv("OBS_POLL_INTERVAL", "1")
    n = poller.MAX_CONCURRENCY + 1
    targets = [{"name": f"s{i}", "server_type": "vllm", "base_url": f"http://h{i}.invalid", "api_key": None,
                "models": ["m"], "gpu_host": None, "gpu_indices": None} for i in range(n)]

    async def fake_read():
        return targets, [], {}

    async def blackhole(request):
        await asyncio.sleep(30)

    monkeypatch.setattr(poller, "_read_config", fake_read)
    client = httpx.AsyncClient(transport=httpx.MockTransport(blackhole))
    await poller.poll_once(interval=1.0, client=client)
    await client.aclose()
    statuses = [s["status"] for s in poller.get_snapshot()["servers"]]
    assert statuses.count("unreachable") == poller.MAX_CONCURRENCY
    assert statuses.count("skipped") == 1
    assert len(poller._backoff) == poller.MAX_CONCURRENCY
