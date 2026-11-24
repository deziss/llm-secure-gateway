"""Tests for streaming proxy, circuit breaker, and SSE endpoint."""
import pytest
import time
from unittest.mock import MagicMock, AsyncMock, patch
import httpx


# ─── Circuit Breaker ─────────────────────────────────────────────────────

class TestCircuitBreaker:

    def test_starts_closed(self):
        from llm_gateway.proxy_helpers import CircuitBreaker
        cb = CircuitBreaker()
        assert cb.is_open("http://backend:11434") is False

    def test_opens_after_threshold(self):
        from llm_gateway.proxy_helpers import CircuitBreaker
        cb = CircuitBreaker()
        cb.FAILURE_THRESHOLD = 3
        url = "http://backend:11434"
        for _ in range(3):
            cb.record_failure(url)
        assert cb.is_open(url) is True

    def test_success_resets_failures(self):
        from llm_gateway.proxy_helpers import CircuitBreaker
        cb = CircuitBreaker()
        cb.FAILURE_THRESHOLD = 5
        url = "http://backend:11434"
        for _ in range(4):
            cb.record_failure(url)
        cb.record_success(url)
        assert cb.is_open(url) is False
        assert cb._get(url)["failures"] == 0

    def test_half_open_after_timeout(self):
        from llm_gateway.proxy_helpers import CircuitBreaker
        cb = CircuitBreaker()
        cb.FAILURE_THRESHOLD = 1
        cb.RECOVERY_TIMEOUT = 0.1
        url = "http://backend:11434"
        cb.record_failure(url)
        assert cb.is_open(url) is True
        time.sleep(0.15)
        # After recovery timeout, should be half-open (allows one probe)
        assert cb.is_open(url) is False
        assert cb._get(url)["state"] == "half_open"

    def test_different_urls_independent(self):
        from llm_gateway.proxy_helpers import CircuitBreaker
        cb = CircuitBreaker()
        cb.FAILURE_THRESHOLD = 2
        cb.record_failure("http://a:11434")
        cb.record_failure("http://a:11434")
        assert cb.is_open("http://a:11434") is True
        assert cb.is_open("http://b:11434") is False

    def test_get_status(self):
        from llm_gateway.proxy_helpers import CircuitBreaker
        cb = CircuitBreaker()
        cb.FAILURE_THRESHOLD = 1
        cb.record_failure("http://a:11434")
        cb.record_success("http://b:11434")
        status = cb.get_status()
        assert status["http://a:11434"] == "open"
        assert status["http://b:11434"] == "closed"


# ─── try_backend_with_fallback circuit integration ────────────────────────

class TestTryBackendWithCircuit:

    @pytest.mark.asyncio
    async def test_skips_open_circuit_to_fallback(self):
        """When primary URL circuit is open, falls through to fallback."""
        from llm_gateway.proxy_helpers import try_backend_with_fallback, _circuit_breaker

        backend = MagicMock()
        backend.base_url = "http://dead:11434"
        backend.fallback_urls = ["http://alive:11434"]
        backend.name = "test"

        # Trip the primary circuit
        _circuit_breaker.FAILURE_THRESHOLD = 1
        _circuit_breaker.record_failure("http://dead:11434")

        mock_response = AsyncMock()
        mock_response.status_code = 200

        with patch("llm_gateway.proxy_helpers._get_client") as mock_client_fn:
            mock_client = AsyncMock()
            mock_client.build_request = MagicMock(return_value=MagicMock())
            mock_client.send = AsyncMock(return_value=mock_response)
            mock_client_fn.return_value = mock_client

            r, used_url, _ = await try_backend_with_fallback(
                backend, "api/chat", "POST", {}, {"model": "x"}, None
            )
            assert used_url == "http://alive:11434"

        # Reset for other tests
        _circuit_breaker.record_success("http://dead:11434")


# ─── SSE endpoint format ─────────────────────────────────────────────────

class TestSSEEndpoint:

    def test_traffic_history_format(self):
        """get_traffic_history returns correct shape for SSE."""
        from llm_gateway.tracking import get_traffic_history
        history = get_traffic_history(5)
        assert len(history) == 5
        for entry in history:
            assert "label" in entry
            assert "count" in entry
            assert isinstance(entry["count"], int)
            assert ":" in entry["label"]  # HH:MM format


# ── Regression: normalize_thinking corrupted bodies split across chunks ──
# The thinking parser split each network chunk on "\n" and re-appended "\n"
# to every piece, so a JSON embeddings body cut mid-number gained a newline
# ("Expected ',' or ']' after array element ... line 2 column 1") and grew
# past the upstream Content-Length.

class _ChunkedResponse:
    def __init__(self, chunks, content_type, status_code=200):
        self._chunks = chunks
        self.headers = {"content-type": content_type}
        self.status_code = status_code

    async def aiter_raw(self):
        for c in self._chunks:
            yield c


async def _collect(chunks, content_type):
    import json as _json  # noqa: F401
    from unittest.mock import MagicMock
    from llm_gateway.telemetry.streaming import stream_with_telemetry

    span = MagicMock()
    span.is_recording.return_value = False
    out = b""
    async for b in stream_with_telemetry(
        _ChunkedResponse(chunks, content_type), 0, "u", "m", "/v1/chat/completions", "o", span,
        normalize_thinking=True,
    ):
        out += b
    return out


async def test_normalize_thinking_passes_json_body_through_unchanged():
    import json
    body = json.dumps({"data": [{"embedding": [0.0123456789] * 3000}]}).encode()
    chunks = [body[i:i + 4096] for i in range(0, len(body), 4096)]
    assert await _collect(chunks, "application/json") == body


async def test_normalize_thinking_handles_lines_and_utf8_split_across_chunks():
    import json
    sse = (
        "data: " + json.dumps({"choices": [{"delta": {"content": "<think>hmm</think>héllo 世界"}}]}) + "\n\n"
        "data: [DONE]\n\n"
    ).encode()
    chunks = [sse[i:i + 7] for i in range(0, len(sse), 7)]
    out = (await _collect(chunks, "text/event-stream")).decode()
    events = [json.loads(l[6:]) for l in out.split("\n") if l.startswith("data: {")]
    assert events[0]["choices"][0]["delta"] == {"reasoning_content": "hmm", "content": "héllo 世界"}
    assert "data: [DONE]" in out


def test_passthrough_response_headers_drops_length_and_encoding():
    from llm_gateway.proxy_helpers import passthrough_response_headers
    h = passthrough_response_headers({"Content-Length": "10", "content-encoding": "gzip",
                                      "content-type": "application/json", "x-request-id": "a"})
    assert h == {"content-type": "application/json", "x-request-id": "a"}


@pytest.mark.asyncio
async def test_upstream_400_status_code_propagated_to_tracker():
    from unittest.mock import MagicMock, patch
    from llm_gateway.telemetry.streaming import stream_with_telemetry

    span = MagicMock()
    span.is_recording.return_value = True
    tracker = MagicMock()

    resp = _ChunkedResponse([b'{"error": "bad request"}'], "application/json", status_code=400)

    with patch("llm_gateway.telemetry.streaming.track_done") as mock_track_done:
        chunks = []
        async for c in stream_with_telemetry(
            resp, 0, "http://vllm", "qwen3.8-flash-next", "/v1/chat/completions",
            "anshu", span, tracker=tracker
        ):
            chunks.append(c)

        assert b"".join(chunks) == b'{"error": "bad request"}'
        assert mock_track_done.called
        kwargs = mock_track_done.call_args.kwargs
        assert kwargs["success"] is False
        assert kwargs["status_code"] == 400
        assert kwargs["error_type"] == "bad_request"
        assert span.set_status.called


def test_extract_deltas_counts_vllm_reasoning_deltas():
    from llm_gateway.telemetry.streaming import _extract_deltas

    text, thinking, has = _extract_deltas({"choices": [{"delta": {"reasoning": "We"}}]})
    assert has and thinking == "We" and text == ""

    text, thinking, has = _extract_deltas({"choices": [{"delta": {"reasoning_content": "hmm"}}]})
    assert has and thinking == "hmm"

    text, thinking, has = _extract_deltas({"choices": [{"delta": {"content": "hi"}}]})
    assert has and text == "hi"

    text, thinking, has = _extract_deltas({"choices": [{"delta": {"role": "assistant", "content": ""}}]})
    assert not has


@pytest.mark.asyncio
async def test_pure_reasoning_stream_reports_tokens_without_usage():
    """vLLM reasoning deltas (``delta.reasoning``) must feed live tok/s and the final
    output count even when the stream carries no usage chunk."""
    import json
    from unittest.mock import MagicMock, patch
    from llm_gateway.telemetry.streaming import stream_with_telemetry

    def ev(d):
        return ("data: " + json.dumps(
            {"id": "x", "object": "chat.completion.chunk",
             "choices": [{"index": 0, "delta": d}]}
        ) + "\n\n").encode()

    chunk1 = ev({"role": "assistant", "content": ""}) + ev({"reasoning": "The"}) + ev({"reasoning": " user"})
    chunk2 = ev({"reasoning": " asks"}) + b"data: [DONE]\n\n"

    span = MagicMock()
    span.is_recording.return_value = False
    tracker = MagicMock()
    tracker.finished = False

    resp = _ChunkedResponse([chunk1, chunk2], "text/event-stream")
    with patch("llm_gateway.telemetry.streaming.track_done") as mock_done:
        async for _ in stream_with_telemetry(
            resp, 0, "http://vllm", "qwen3.8-flash-next", "/v1/chat/completions",
            "anshu", span, tracker=tracker,
        ):
            pass

        assert mock_done.called
        assert mock_done.call_args.kwargs["output_tokens"] == 3
        assert mock_done.call_args.kwargs["estimated"] is True

    # progress() reports are throttled to one per 250 ms; in a fast test the whole
    # stream fits in one window so only the first window's report fires. Live
    # crediting itself is covered in tests/test_live_stats.py.
    progressed = [c.args[0] for c in tracker.progress.call_args_list]
    assert progressed, "expected at least one live progress report"
    for p in progressed:
        assert 0 < p <= 3
