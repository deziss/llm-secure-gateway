"""
Tests for Anthropic /v1/messages Telemetry Instrumentation.

Verifies that native Anthropic /v1/messages requests (both streaming SSE and non-streaming JSON,
both native Anthropic and translated to OpenAI backends):
1. Extract exact prompt and completion token counts.
2. Invoke record_request_metrics with accurate figures and status.
3. Generate Phoenix root spans with context isolation (parent is None) and complete span lifecycle.
4. Update LiveStats request tracking (waiting -> generating -> finished).
"""
import asyncio
import json
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from llm_gateway.telemetry.tracing import PhoenixTraceManager
from llm_gateway.observability.export import manager
from llm_gateway.telemetry import streaming
from llm_gateway.telemetry.streaming import stream_with_telemetry
from llm_gateway.observability.store import get_live_stats
from llm_gateway.observability.collect.requests import track_dispatch
from llm_gateway.translation.anthropic_to_openai import (
    translate_request,
    translate_response,
)


@pytest.fixture(autouse=True)
def _clean_telemetry(monkeypatch):
    """Reset manager and PhoenixTraceManager for test isolation, avoiding real network calls."""
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    manager._reset_for_tests()
    PhoenixTraceManager._reset_for_tests()

    # Configure per-tenant Phoenix mode
    manager.apply({
        "phoenix": {
            "PHOENIX_COLLECTOR_ENDPOINT": "http://phoenix:6006",
            "PHOENIX_PROJECT_PER_TENANT": "true",
        }
    })
    # Clear the underlying processors to avoid real network attempts during test
    manager._span_processor._procs.clear()

    yield

    manager._reset_for_tests()
    PhoenixTraceManager._reset_for_tests()


class _MockChunkedResponse:
    """Mock response implementing aiter_raw and headers for stream_with_telemetry."""

    def __init__(self, chunks: list[bytes], content_type: str = "text/event-stream", status_code: int = 200):
        self._chunks = chunks
        self.headers = {"content-type": content_type}
        self.status_code = status_code

    async def aiter_raw(self):
        for c in self._chunks:
            yield c

    async def aclose(self):
        pass


def _make_anthropic_sse(events: list[dict]) -> bytes:
    """Serialize a list of Anthropic SSE event dictionaries."""
    return "".join(
        f"event: {e.get('type', 'message')}\ndata: {json.dumps(e)}\n\n"
        for e in events
    ).encode("utf-8")


# =============================================================================
# 1. Streaming Anthropic Telemetry (Tokens, Metrics, Phoenix Span, Live Stats)
# =============================================================================

@pytest.mark.asyncio
async def test_anthropic_streaming_telemetry_records_tokens_and_span():
    """
    Test streaming /v1/messages with Anthropic SSE chunks.

    Chunks include:
    - message_start with input_tokens
    - content_block_delta with text
    - message_delta with output_tokens

    Verifies:
    - Exact token extraction (prompt and completion tokens)
    - record_request_metrics invocation with correct parameters
    - Phoenix root span creation, parent isolation, attributes, and span ending
    - Live request stats lifecycle (waiting -> generating -> finished)
    """
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_anthropic_stream_01",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "claude-3-5-sonnet-20241022",
                "usage": {"input_tokens": 32, "output_tokens": 1},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello, this is Claude streaming!"},
        },
        {
            "type": "content_block_stop",
            "index": 0,
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 48},
        },
        {
            "type": "message_stop",
        },
    ]
    sse_body = _make_anthropic_sse(events)

    backend_url = "https://api.anthropic.com"
    endpoint_uri = "/v1/messages"
    server_name = "anthropic-primary"
    server_type = "anthropic"
    model_name = "claude-3-5-sonnet-20241022"
    tenant_id = "tenant-prod-a"

    with respx.mock:
        respx.post(f"{backend_url}{endpoint_uri}").respond(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse_body,
        )

        # 1. Create Phoenix root span
        span = PhoenixTraceManager.start_root_span(tenant_id, server_type, "llm_request")
        assert span.parent is None, "Span must be a root span (parent is None)"
        assert span.is_recording() is True
        assert span.end_time is None

        # 2. Dispatch LiveStats tracker
        store = get_live_stats()
        tracker = track_dispatch(server_name, server_type, model_name)
        live_waiting = [r for r in store.snapshot() if r["server"] == server_name]
        assert len(live_waiting) == 1
        assert live_waiting[0]["waiting"] == 1

        # 3. Intercept metrics recording
        recorded_metrics = {}
        with patch.object(streaming, "record_request_metrics", side_effect=lambda **kw: recorded_metrics.update(kw)):
            client = httpx.AsyncClient()
            req = client.build_request("POST", f"{backend_url}{endpoint_uri}", json={
                "model": model_name,
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            })
            raw_resp = await client.send(req, stream=True)

            accumulated = []
            start_time = time.time()
            async for chunk in stream_with_telemetry(
                raw_resp,
                start_time,
                backend_url,
                model_name,
                endpoint_uri,
                tenant_id,
                span,
                client=client,
                server_name=server_name,
                server_type=server_type,
                tracker=tracker,
            ):
                accumulated.append(chunk)

        # 4. Verify exact token extraction and metric recording
        assert recorded_metrics["prompt_tokens"] == 32
        assert recorded_metrics["completion_tokens"] == 48
        assert recorded_metrics["success"] is True
        assert recorded_metrics["model"] == model_name
        assert recorded_metrics["endpoint_uri"] == endpoint_uri
        assert recorded_metrics["server"] == server_name
        assert recorded_metrics["server_type"] == server_type
        assert recorded_metrics["user_name"] == tenant_id
        assert recorded_metrics["request_duration"] > 0

        # 5. Verify Phoenix root span lifecycle and OpenInference attributes
        assert span.is_recording() is False, "Span must be ended when stream finishes"
        assert span.end_time is not None, "Span end_time must be recorded"
        assert span.attributes["openinference.span.kind"] == "LLM"
        assert span.attributes["llm.model_name"] == model_name
        assert span.attributes["llm.token_count.prompt"] == 32
        assert span.attributes["llm.token_count.completion"] == 48
        assert span.attributes["gen_ai.usage.input_tokens"] == 32
        assert span.attributes["gen_ai.usage.output_tokens"] == 48
        assert span.attributes["output.value"] == "Hello, this is Claude streaming!"
        assert "Hello, this is Claude streaming!" in span.attributes["llm.output_messages"]

        # 6. Verify LiveStats request completion
        assert tracker.finished is True
        live_after = [r for r in store.snapshot() if r["server"] == server_name]
        assert live_after[0]["waiting"] == 0
        assert live_after[0]["generating"] == 0
        assert live_after[0]["requests_60s"] >= 1
        assert live_after[0]["errors_60s"] == 0


# =============================================================================
# 2. Non-Streaming Anthropic Telemetry (Tokens, Metrics, Phoenix Span, Live Stats)
# =============================================================================

@pytest.mark.asyncio
async def test_anthropic_non_streaming_telemetry_records_tokens_and_span():
    """
    Test non-streaming /v1/messages with JSON response.

    Verifies:
    - Accurate prompt and completion token counts extracted from response JSON body
    - record_request_metrics invocation with success and full token counts
    - Phoenix root span ending and attribute assignment
    - Live request stats update
    """
    response_body = {
        "id": "msg_anthropic_nonstream_01",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "text", "text": "This is a non-streaming response from Claude 3.5 Sonnet."}
        ],
        "model": "claude-3-5-sonnet-20241022",
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 19,
            "output_tokens": 37,
        },
    }
    raw_json = json.dumps(response_body).encode("utf-8")

    backend_url = "https://api.anthropic.com"
    endpoint_uri = "/v1/messages"
    server_name = "anthropic-nonstream-srv"
    server_type = "anthropic"
    model_name = "claude-3-5-sonnet-20241022"
    tenant_id = "tenant-prod-b"

    with respx.mock:
        respx.post(f"{backend_url}{endpoint_uri}").respond(
            200,
            headers={"content-type": "application/json"},
            content=raw_json,
        )

        span = PhoenixTraceManager.start_root_span(tenant_id, server_type, "llm_request")
        assert span.parent is None
        assert span.is_recording() is True

        store = get_live_stats()
        tracker = track_dispatch(server_name, server_type, model_name)

        recorded_metrics = {}
        with patch.object(streaming, "record_request_metrics", side_effect=lambda **kw: recorded_metrics.update(kw)):
            client = httpx.AsyncClient()
            req = client.build_request("POST", f"{backend_url}{endpoint_uri}", json={
                "model": model_name,
                "messages": [{"role": "user", "content": "Explain relativity."}],
                "stream": False,
            })
            raw_resp = await client.send(req, stream=True)

            out_chunks = []
            async for chunk in stream_with_telemetry(
                raw_resp,
                time.time(),
                backend_url,
                model_name,
                endpoint_uri,
                tenant_id,
                span,
                client=client,
                server_name=server_name,
                server_type=server_type,
                tracker=tracker,
            ):
                out_chunks.append(chunk)

        # Verify token counts in metrics
        assert recorded_metrics["prompt_tokens"] == 19
        assert recorded_metrics["completion_tokens"] == 37
        assert recorded_metrics["total_tokens"] == 56
        assert recorded_metrics["success"] is True
        assert recorded_metrics["endpoint_uri"] == endpoint_uri
        assert recorded_metrics["server"] == server_name

        # Verify span ended and attributes set
        assert span.is_recording() is False
        assert span.end_time is not None
        assert span.attributes["llm.token_count.prompt"] == 19
        assert span.attributes["llm.token_count.completion"] == 37
        assert span.attributes["llm.token_count.total"] == 56
        assert span.attributes["gen_ai.usage.input_tokens"] == 19
        assert span.attributes["gen_ai.usage.output_tokens"] == 37

        # Verify tracker finished
        assert tracker.finished is True
        rows = [r for r in store.snapshot() if r["server"] == server_name]
        assert rows[0]["waiting"] == 0
        assert rows[0]["generating"] == 0
        assert rows[0]["requests_60s"] >= 1


# =============================================================================
# 3. Anthropic to OpenAI Translation Telemetry
# =============================================================================

@pytest.mark.asyncio
async def test_anthropic_to_openai_translation_telemetry():
    """
    Test Anthropic /v1/messages translated to an OpenAI backend.

    Verifies:
    - translate_request transforms Anthropic system/messages/parameters to OpenAI format
    - Upstream OpenAI streaming chunks translated to Anthropic SSE events
    - Token metrics captured from translated response
    - Phoenix root span attributes and lifecycle
    - Non-streaming response translation and token extraction
    """
    # 1. Request translation test
    anthropic_req = {
        "model": "gpt-4o",
        "system": "You are a professional mathematician.",
        "messages": [
            {"role": "user", "content": "What is Euler's identity?"}
        ],
        "max_tokens": 128,
        "temperature": 0.2,
        "stream": True,
    }
    openai_req = translate_request(anthropic_req)
    assert openai_req["model"] == "gpt-4o"
    assert openai_req["max_tokens"] == 128
    assert openai_req["temperature"] == 0.2
    assert openai_req["stream"] is True
    assert len(openai_req["messages"]) == 2
    assert openai_req["messages"][0] == {"role": "system", "content": "You are a professional mathematician."}
    assert openai_req["messages"][1] == {"role": "user", "content": "What is Euler's identity?"}

    # 2. Streaming response translation from OpenAI to Anthropic
    openai_chunks = [
        {"choices": [{"delta": {"content": "e^(i*pi) + 1 "}}]},
        {"choices": [{"delta": {"content": "= 0"}}]},
        {"choices": [], "usage": {"prompt_tokens": 22, "completion_tokens": 11, "total_tokens": 33}},
    ]
    openai_sse = "".join(f"data: {json.dumps(c)}\n\n" for c in openai_chunks) + "data: [DONE]\n\n"

    backend_url = "http://openai-backend:8000"
    target_path = "/v1/chat/completions"
    client_endpoint = "/v1/messages"
    server_name = "vllm-openai-srv"
    server_type = "vllm"
    tenant_id = "tenant-trans-test"

    with respx.mock:
        respx.post(f"{backend_url}{target_path}").respond(
            200,
            headers={"content-type": "text/event-stream"},
            content=openai_sse.encode("utf-8"),
        )

        span = PhoenixTraceManager.start_root_span(tenant_id, server_type, "llm_request")
        assert span.parent is None
        tracker = track_dispatch(server_name, server_type, "gpt-4o")

        recorded_metrics = {}
        with patch.object(streaming, "record_request_metrics", side_effect=lambda **kw: recorded_metrics.update(kw)):
            client = httpx.AsyncClient()
            req = client.build_request("POST", f"{backend_url}{target_path}", json=openai_req)
            raw_resp = await client.send(req, stream=True)

            translated_chunks = []
            async for chunk in stream_with_telemetry(
                raw_resp,
                time.time(),
                backend_url,
                "gpt-4o",
                client_endpoint,
                tenant_id,
                span,
                client=client,
                translation_mode="anthropic_to_openai",
                server_name=server_name,
                server_type=server_type,
                tracker=tracker,
            ):
                translated_chunks.append(chunk)

        full_output = b"".join(translated_chunks).decode("utf-8")

        # Verify client received translated Anthropic events
        assert "event: message_start" in full_output
        assert "event: content_block_start" in full_output
        assert "event: content_block_delta" in full_output
        assert "e^(i*pi) + 1 " in full_output
        assert "= 0" in full_output
        assert "event: message_delta" in full_output
        assert "event: message_stop" in full_output

        # Verify telemetry extraction from translated stream
        assert recorded_metrics["completion_tokens"] == 11
        assert recorded_metrics["success"] is True
        assert recorded_metrics["endpoint_uri"] == client_endpoint
        assert recorded_metrics["server"] == server_name

        # Verify Phoenix span attributes
        assert span.is_recording() is False
        assert span.end_time is not None
        assert span.attributes["llm.token_count.completion"] == 11
        assert span.attributes["output.value"] == "e^(i*pi) + 1 = 0"

        # Verify tracker finished
        assert tracker.finished is True

    # 3. Non-streaming response translation test
    openai_json_resp = {
        "id": "chatcmpl-trans-999",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "e^(i*pi) + 1 = 0"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 22,
            "completion_tokens": 11,
            "total_tokens": 33,
        },
    }
    anthropic_translated = translate_response(openai_json_resp)
    assert anthropic_translated["type"] == "message"
    assert anthropic_translated["role"] == "assistant"
    assert anthropic_translated["content"][0]["text"] == "e^(i*pi) + 1 = 0"
    assert anthropic_translated["stop_reason"] == "end_turn"
    assert anthropic_translated["usage"]["input_tokens"] == 22
    assert anthropic_translated["usage"]["output_tokens"] == 11


# =============================================================================
# 4. Context Isolation: Phoenix Span is Root Span
# =============================================================================

def test_anthropic_span_is_root_span():
    """
    Verify that the span created by PhoenixTraceManager.start_root_span has
    parent is None even when called inside an ambient parent context.

    Ensures traces are assigned to per-tenant Phoenix projects rather than
    getting attached to an outer FastAPI or ambient framework trace.
    """
    ambient_provider = TracerProvider()
    ambient_tracer = ambient_provider.get_tracer("ambient_fastapi")

    # Simulate an ambient parent span (e.g. FastAPI middleware or upstream request)
    with ambient_tracer.start_as_current_span("parent_fastapi_request"):
        parent_span = trace.get_current_span()
        assert parent_span.is_recording() is True

        # Tenant span created inside ambient parent context
        tenant_span = PhoenixTraceManager.start_root_span(
            owner_id="tenant-anthropic-user",
            backend_name="anthropic",
            span_name="llm_request",
        )
        try:
            # Must be decoupled from ambient parent
            assert tenant_span.parent is None, "Tenant span must have parent is None to serve as root span"
            assert tenant_span.is_recording() is True

            # Resource attribute specifies tenant project
            expected_project = PhoenixTraceManager.get_project_name("tenant-anthropic-user", "anthropic")
            assert tenant_span.resource.attributes.get("openinference.project.name") == expected_project
        finally:
            tenant_span.end()
            assert tenant_span.end_time is not None
            assert tenant_span.is_recording() is False

        # Verify child spans created under tenant_span correctly nest under it
        tenant_tracer = PhoenixTraceManager.get_tracer("tenant-anthropic-user", "anthropic")
        root_span = PhoenixTraceManager.start_root_span("tenant-anthropic-user", "anthropic", "llm_request")
        try:
            with trace.use_span(root_span):
                child_span = tenant_tracer.start_span("anthropic_tool_call")
                try:
                    assert child_span.parent is not None
                    assert child_span.parent.span_id == root_span.get_span_context().span_id
                finally:
                    child_span.end()
        finally:
            root_span.end()


# =============================================================================
# 5. Supplementary: Token Estimation When Stream Lacks Usage Block
# =============================================================================

@pytest.mark.asyncio
async def test_anthropic_streaming_without_usage_estimates_tokens():
    """
    When an Anthropic stream omits usage in message_delta, tokens are estimated
    from content deltas, and tokens_estimated is flagged in LiveStats.
    """
    events = [
        {"type": "message_start", "message": {"id": "msg_02"}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "chunk one "}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "chunk two "}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "chunk three"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    ]
    sse_body = _make_anthropic_sse(events)

    span = PhoenixTraceManager.start_root_span("tenant-est", "anthropic", "llm_request")
    tracker = track_dispatch("anthropic-est-srv", "anthropic", "claude-3-haiku")
    store = get_live_stats()

    recorded_metrics = {}
    with patch.object(streaming, "record_request_metrics", side_effect=lambda **kw: recorded_metrics.update(kw)):
        async for _ in stream_with_telemetry(
            _MockChunkedResponse([sse_body]),
            time.time(),
            "https://api.anthropic.com",
            "claude-3-haiku",
            "/v1/messages",
            "tenant-est",
            span,
            server_name="anthropic-est-srv",
            server_type="anthropic",
            tracker=tracker,
        ):
            pass

    # 3 content deltas -> 3 estimated completion tokens
    assert recorded_metrics["completion_tokens"] == 3
    assert span.attributes["llm.token_count.completion"] == 3
    assert tracker.finished is True

    rows = [r for r in store.snapshot() if r["server"] == "anthropic-est-srv"]
    assert rows[0]["tokens_estimated"] is True


# =============================================================================
# 6. Supplementary: Client Disconnect & Stream Cancellation Handling
# =============================================================================

@pytest.mark.asyncio
async def test_anthropic_streaming_client_cancel_records_telemetry():
    """
    When client cancels / disconnects mid-stream on Anthropic /v1/messages,
    record_request_metrics records success=False, tracker finishes as cancelled,
    and the Phoenix span is ended cleanly.
    """
    class _DisconnectingResponse(_MockChunkedResponse):
        async def aiter_raw(self):
            yield b'event: message_start\ndata: {"type": "message_start", "message": {"usage": {"input_tokens": 10}}}\n\n'
            yield b'event: content_block_delta\ndata: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "partial"}}\n\n'
            raise GeneratorExit

    span = PhoenixTraceManager.start_root_span("tenant-cancel", "anthropic", "llm_request")
    tracker = track_dispatch("anthropic-cancel-srv", "anthropic", "claude-3-5-sonnet")

    recorded_metrics = {}
    with patch.object(streaming, "record_request_metrics", side_effect=lambda **kw: recorded_metrics.update(kw)):
        gen = stream_with_telemetry(
            _DisconnectingResponse([]),
            time.time(),
            "https://api.anthropic.com",
            "claude-3-5-sonnet",
            "/v1/messages",
            "tenant-cancel",
            span,
            server_name="anthropic-cancel-srv",
            server_type="anthropic",
            tracker=tracker,
        )
        with pytest.raises(GeneratorExit):
            async for _ in gen:
                pass

    assert recorded_metrics["success"] is False
    assert tracker.finished is True
    assert span.is_recording() is False
    assert span.end_time is not None


# =============================================================================
# 7. Supplementary: Backend Stream Error Status & Telemetry Handling
# =============================================================================

@pytest.mark.asyncio
async def test_anthropic_streaming_backend_error_records_failure():
    """
    When the backend stream raises an unexpected connection or network error,
    metrics record success=False, span status is set to ERROR and ends,
    and tracker is finished with success=False.
    """
    class _ErrorResponse(_MockChunkedResponse):
        async def aiter_raw(self):
            yield b'event: message_start\ndata: {"type": "message_start", "message": {"usage": {"input_tokens": 10}}}\n\n'
            raise httpx.RemoteProtocolError("Connection broken by upstream Anthropic server")

    span = PhoenixTraceManager.start_root_span("tenant-err", "anthropic", "llm_request")
    tracker = track_dispatch("anthropic-err-srv", "anthropic", "claude-3-opus")

    recorded_metrics = {}
    with patch.object(streaming, "record_request_metrics", side_effect=lambda **kw: recorded_metrics.update(kw)):
        gen = stream_with_telemetry(
            _ErrorResponse([]),
            time.time(),
            "https://api.anthropic.com",
            "claude-3-opus",
            "/v1/messages",
            "tenant-err",
            span,
            server_name="anthropic-err-srv",
            server_type="anthropic",
            tracker=tracker,
        )
        with pytest.raises(httpx.RemoteProtocolError):
            async for _ in gen:
                pass

    assert recorded_metrics["success"] is False
    assert tracker.finished is True
    assert span.is_recording() is False
    assert span.end_time is not None
    assert span.status.status_code == trace.StatusCode.ERROR


# =============================================================================
# 8. Endpoint Level Tests for anthropic_messages_endpoint
# =============================================================================

@pytest.mark.asyncio
async def test_anthropic_messages_endpoint_non_streaming():
    from llm_gateway.routers.proxy import anthropic_messages_endpoint
    from fastapi import Request
    from unittest.mock import AsyncMock, MagicMock, patch

    req = MagicMock(spec=Request)
    req.method = "POST"
    req.url = "http://localhost:8000/v1/messages"
    req.query_params = {}
    req.headers = {
        "user-agent": "claude-code/1.0.0",
        "authorization": "Bearer sk-test",
    }
    req.state = MagicMock()
    req.state.user = MagicMock()
    req.state.user.owner_id = "test-owner"
    req.state.user.key_hash = "khash"
    req.json = AsyncMock(return_value={
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello!"}],
        "system": "You are a helpful assistant.",
        "max_tokens": 100,
        "stream": False,
    })

    config_service = AsyncMock()
    config_service.get_setting = AsyncMock(return_value="false")

    backend = MagicMock()
    backend.name = "anthropic-prod"
    backend.backend_type = MagicMock()
    backend.backend_type.value = "anthropic"
    backend.base_url = "https://api.anthropic.com"
    backend.fallback_urls = []

    session = AsyncMock()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"type": "message", "content": [{"type": "text", "text": "Hi there!"}], "usage": {"input_tokens": 12, "output_tokens": 18}}'
    mock_resp.json = MagicMock(return_value={
        "type": "message",
        "content": [{"type": "text", "text": "Hi there!"}],
        "usage": {"input_tokens": 12, "output_tokens": 18},
    })

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    recorded_metrics = {}
    with patch("llm_gateway.routers.proxy.apply_rate_limit"), \
         patch("llm_gateway.services.spend_service.check_budget", AsyncMock()), \
         patch("llm_gateway.routers.proxy.get_target_backend", AsyncMock(return_value=backend)), \
         patch("llm_gateway.routers.proxy.check_owner_permissions", AsyncMock()), \
         patch("llm_gateway.proxy_helpers.build_backend_auth_headers", AsyncMock(return_value={})), \
         patch("llm_gateway.proxy_helpers._get_client", return_value=mock_client), \
         patch("llm_gateway.routers.proxy.record_request_metrics", side_effect=lambda **kw: recorded_metrics.update(kw)):

        response = await anthropic_messages_endpoint(req, config_service=config_service, session=session)

        assert response.status_code == 200
        content = json.loads(response.body.decode("utf-8"))
        assert content["type"] == "message"
        assert content["content"][0]["text"] == "Hi there!"
        assert recorded_metrics["prompt_tokens"] == 12
        assert recorded_metrics["completion_tokens"] == 18
        assert recorded_metrics["endpoint_uri"] == "/v1/messages"
        assert recorded_metrics["success"] is True


@pytest.mark.asyncio
async def test_anthropic_messages_endpoint_streaming():
    from llm_gateway.routers.proxy import anthropic_messages_endpoint
    from fastapi import Request
    from unittest.mock import AsyncMock, MagicMock, patch

    req = MagicMock(spec=Request)
    req.method = "POST"
    req.url = "http://localhost:8000/v1/messages"
    req.query_params = {}
    req.headers = {
        "user-agent": "cursor/0.40.0",
        "authorization": "Bearer sk-test",
    }
    req.state = MagicMock()
    req.state.user = MagicMock()
    req.state.user.owner_id = "test-owner"
    req.state.user.key_hash = "khash"
    req.json = AsyncMock(return_value={
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Stream to me"}],
        "stream": True,
    })

    config_service = AsyncMock()
    config_service.get_setting = AsyncMock(return_value="false")

    backend = MagicMock()
    backend.name = "anthropic-prod"
    backend.backend_type = MagicMock()
    backend.backend_type.value = "anthropic"
    backend.base_url = "https://api.anthropic.com"
    backend.fallback_urls = []

    session = AsyncMock()

    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 10, "output_tokens": 1}}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "streaming piece"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 20}},
    ]
    sse_bytes = _make_anthropic_sse(events)

    mock_resp = _MockChunkedResponse([sse_bytes])

    mock_client = AsyncMock()
    mock_client.build_request = MagicMock(return_value=MagicMock())
    mock_client.send = AsyncMock(return_value=mock_resp)

    recorded_metrics = {}
    with patch("llm_gateway.routers.proxy.apply_rate_limit"), \
         patch("llm_gateway.services.spend_service.check_budget", AsyncMock()), \
         patch("llm_gateway.routers.proxy.get_target_backend", AsyncMock(return_value=backend)), \
         patch("llm_gateway.routers.proxy.check_owner_permissions", AsyncMock()), \
         patch("llm_gateway.proxy_helpers.build_backend_auth_headers", AsyncMock(return_value={})), \
         patch("llm_gateway.proxy_helpers._get_client", return_value=mock_client), \
         patch("llm_gateway.telemetry.streaming.record_request_metrics", side_effect=lambda **kw: recorded_metrics.update(kw)):

        response = await anthropic_messages_endpoint(req, config_service=config_service, session=session)

        assert response.status_code == 200
        assert response.media_type == "text/event-stream"
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)

        full_res = b"".join(chunks).decode("utf-8")
        assert "streaming piece" in full_res
        assert recorded_metrics["prompt_tokens"] == 10
        assert recorded_metrics["completion_tokens"] == 20
        assert recorded_metrics["success"] is True


@pytest.mark.asyncio
async def test_anthropic_messages_endpoint_error_handling():
    from llm_gateway.routers.proxy import anthropic_messages_endpoint
    from fastapi import Request, HTTPException
    from unittest.mock import AsyncMock, MagicMock, patch

    req = MagicMock(spec=Request)
    req.method = "POST"
    req.url = "http://localhost:8000/v1/messages"
    req.query_params = {}
    req.headers = {"authorization": "Bearer sk-test"}
    req.state = MagicMock()
    req.state.user = MagicMock()
    req.state.user.owner_id = "test-owner"
    req.json = AsyncMock(return_value={
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Fail me"}],
        "stream": False,
    })

    config_service = AsyncMock()
    config_service.get_setting = AsyncMock(return_value="false")

    backend = MagicMock()
    backend.name = "anthropic-prod"
    backend.backend_type = MagicMock()
    backend.backend_type.value = "anthropic"
    backend.base_url = "https://api.anthropic.com"
    backend.fallback_urls = []

    session = AsyncMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    recorded_metrics = {}
    with patch("llm_gateway.routers.proxy.apply_rate_limit"), \
         patch("llm_gateway.services.spend_service.check_budget", AsyncMock()), \
         patch("llm_gateway.routers.proxy.get_target_backend", AsyncMock(return_value=backend)), \
         patch("llm_gateway.routers.proxy.check_owner_permissions", AsyncMock()), \
         patch("llm_gateway.proxy_helpers.build_backend_auth_headers", AsyncMock(return_value={})), \
         patch("llm_gateway.proxy_helpers._get_client", return_value=mock_client), \
         patch("llm_gateway.routers.proxy.record_request_metrics", side_effect=lambda **kw: recorded_metrics.update(kw)):

        with pytest.raises(HTTPException) as exc_info:
            await anthropic_messages_endpoint(req, config_service=config_service, session=session)

        assert exc_info.value.status_code == 502
        assert recorded_metrics["success"] is False
        assert recorded_metrics["endpoint_uri"] == "/v1/messages"


