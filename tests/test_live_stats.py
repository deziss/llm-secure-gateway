"""LiveStats with an injectable clock: rps windows, waiting -> generating, tok/s, rollover."""
import pytest

from llm_gateway.observability.store import LiveStats
from llm_gateway.observability.collect.requests import track_dispatch, track_done

KEY = ("gpu-a", "vllm", "llama3")


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _row(snap):
    assert len(snap) == 1
    return snap[0]


def test_rps_windows_over_10s_and_60s():
    clk = Clock()
    st = LiveStats(clock=clk)
    for _ in range(20):
        st.dispatch(KEY)
    row = _row(st.snapshot())
    assert row["requests_60s"] == 20
    assert row["rps_10s"] == pytest.approx(2.0)
    assert row["rps_60s"] == pytest.approx(20 / 60, abs=1e-3)

    clk.t += 15  # outside the 10 s window, still inside 60 s
    row = _row(st.snapshot())
    assert row["rps_10s"] == 0
    assert row["requests_60s"] == 20


def test_bucket_rollover_drops_old_samples():
    clk = Clock()
    st = LiveStats(clock=clk)
    st.dispatch(KEY)
    st.finish(KEY, was_generating=False, success=True, output_tokens=0, decode_seconds=0.0, ttft=None)
    clk.t += 61
    assert st.snapshot() == []  # nothing active inside the window
    st.dispatch(KEY)
    assert _row(st.snapshot())["requests_60s"] == 1


def test_waiting_to_generating_transition():
    clk = Clock()
    st = LiveStats(clock=clk)
    st.dispatch(KEY)
    st.dispatch(KEY)
    assert _row(st.snapshot())["waiting"] == 2
    st.first_token(KEY)
    row = _row(st.snapshot())
    assert (row["waiting"], row["generating"]) == (1, 1)
    st.finish(KEY, was_generating=True, success=True, output_tokens=10, decode_seconds=2.0, ttft=0.2)
    row = _row(st.snapshot())
    assert (row["waiting"], row["generating"]) == (1, 0)


def test_tok_s_decode_is_mean_of_per_request_speed_and_throughput_is_sum_over_60s():
    clk = Clock()
    st = LiveStats(clock=clk)
    st.dispatch(KEY)
    st.first_token(KEY)
    st.finish(KEY, was_generating=True, success=True, output_tokens=100, decode_seconds=2.0)  # 50 tok/s
    st.dispatch(KEY)
    st.first_token(KEY)
    st.finish(KEY, was_generating=True, success=True, output_tokens=60, decode_seconds=2.0)   # 30 tok/s
    row = _row(st.snapshot())
    assert row["tok_s_decode"] == pytest.approx(40.0)
    assert row["tok_s_throughput"] == pytest.approx(160 / 60, abs=0.01)


def test_ttft_median_and_errors_and_estimate_flag():
    clk = Clock()
    st = LiveStats(clock=clk)
    for t in (0.1, 0.3, 0.2):
        st.dispatch(KEY)
        st.first_token(KEY)
        st.finish(KEY, was_generating=True, success=True, output_tokens=1, decode_seconds=1.0, ttft=t, estimated=True)
    st.dispatch(KEY)
    st.finish(KEY, was_generating=False, success=False)
    row = _row(st.snapshot())
    assert row["ttft_p50_ms"] == pytest.approx(200.0)
    assert row["errors_60s"] == 1
    assert row["tokens_estimated"] is True


def test_cancelled_is_not_an_error():
    clk = Clock()
    st = LiveStats(clock=clk)
    st.dispatch(KEY)
    st.first_token(KEY)
    st.finish(KEY, was_generating=True, success=False, cancelled=True)
    assert _row(st.snapshot())["errors_60s"] == 0


def test_inflight_bytes_add_and_release():
    st = LiveStats(clock=Clock())
    st.dispatch(KEY)
    st.add_inflight_bytes(KEY, 4096)
    assert _row(st.snapshot())["inflight_bytes"] == 4096
    st.add_inflight_bytes(KEY, -4096)
    assert _row(st.snapshot())["inflight_bytes"] == 0


def test_tracker_retarget_moves_waiting_and_finish_is_idempotent():
    clk = Clock()
    st = LiveStats(clock=clk)
    tr = track_dispatch("primary", "ollama", "a", store=st)
    tr.retarget("fallback", "vllm", "b")
    snap = {(r["server"], r["model"]): r for r in st.snapshot()}
    assert ("primary", "a") not in snap
    assert snap[("fallback", "b")]["waiting"] == 1
    assert track_done(tr, success=True) is not None
    assert track_done(tr, success=True) is None  # second finish is a no-op
    assert st.snapshot()[0]["waiting"] == 0


def test_tracker_timing_uses_store_clock():
    clk = Clock(100.0)
    st = LiveStats(clock=clk)
    tr = track_dispatch("s", "vllm", "m", store=st)
    clk.t = 100.5
    tr.first_token()
    clk.t = 102.5
    out = tr.finish(success=True, output_tokens=20)
    assert out["ttft"] == pytest.approx(0.5)
    assert out["decode_seconds"] == pytest.approx(2.0)
