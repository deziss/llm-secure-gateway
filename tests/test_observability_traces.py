import pytest
from llm_gateway.observability.traces import TraceRingBuffer, RequestTrace


def test_trace_ring_buffer_fifo():
    buf = TraceRingBuffer(capacity=3)
    for i in range(5):
        buf.record(RequestTrace(
            request_id=f"req-{i}",
            timestamp="2026-11-19T00:00:00Z",
            model="gpt-4o",
            status_code=200,
            duration_ms=120.5,
        ))
    traces = buf.get_all()
    assert len(traces) == 3
    # Most recent first
    assert traces[0].request_id == "req-4"
    assert traces[1].request_id == "req-3"
    assert traces[2].request_id == "req-2"


def test_trace_ring_buffer_limit():
    buf = TraceRingBuffer(capacity=10)
    for i in range(10):
        buf.record(RequestTrace(
            request_id=f"req-{i}",
            timestamp="2026-11-19T00:00:00Z",
            model="gpt-4o",
            status_code=200,
            duration_ms=100.0,
        ))
    traces = buf.get_all(limit=5)
    assert len(traces) == 5
    assert traces[0].request_id == "req-9"


def test_classify_error():
    from llm_gateway.observability.collect.requests import classify_error
    assert classify_error(429) == "upstream_rate_limit"
    assert classify_error(503) == "upstream_overload"
    assert classify_error(500) == "upstream_failure"
    assert classify_error(502) == "upstream_failure"
    assert classify_error(504) == "upstream_failure"
    assert classify_error(400) == "guardrail_blocked"
    assert classify_error(403) == "guardrail_blocked"
    assert classify_error(401) == "auth_failure"
    assert classify_error(499) == "client_disconnect"
    assert classify_error(501) == "http_501"


def test_tracker_finish_records_trace():
    from llm_gateway.observability.store import LiveStats
    from llm_gateway.observability.collect.requests import track_dispatch
    from llm_gateway.observability.traces import get_trace_buffer

    buf = get_trace_buffer()
    tr = track_dispatch("srv-1", "openai", "gpt-4o", store=LiveStats(), request_id="custom-req-123")
    tr.first_token()
    tr.finish(
        success=True,
        prompt_tokens=10,
        output_tokens=20,
        total_tokens=30,
        status_code=200,
        cost_usd=0.0015,
        cache_hit=True,
        phases={"inference": 150.0},
    )

    traces = buf.get_all(limit=10)
    assert any(t.request_id == "custom-req-123" for t in traces)
    trace = next(t for t in traces if t.request_id == "custom-req-123")
    assert trace.model == "gpt-4o"
    assert trace.provider == "openai"
    assert trace.input_tokens == 10
    assert trace.output_tokens == 20
    assert trace.total_tokens == 30
    assert trace.cost_usd == 0.0015
    assert trace.cache_hit is True
    assert trace.status_code == 200
    assert trace.error_type is None
    assert trace.phases == {"inference": 150.0}


