"""Usage extraction across Ollama NDJSON, OpenAI SSE, Anthropic, the thinking-parser branch,
and client cancel (track_done must still fire)."""
import json
from unittest.mock import MagicMock

import pytest

from llm_gateway.observability.collect.usage import (
    Usage, usage_from_body, usage_from_event, inject_stream_usage, estimate_completion,
)


class _Resp:
    def __init__(self, chunks, content_type):
        self._chunks = chunks
        self.headers = {"content-type": content_type}

    async def aiter_raw(self):
        for c in self._chunks:
            yield c


def _span():
    s = MagicMock()
    s.is_recording.return_value = False
    return s


async def _run(chunks, content_type, endpoint="/api/chat", **kw):
    from llm_gateway.telemetry.streaming import stream_with_telemetry
    out = b""
    async for b in stream_with_telemetry(_Resp(chunks, content_type), 0, "http://u", "m", endpoint, "o", _span(), **kw):
        out += b
    return out


def _split(data: bytes, n: int):
    return [data[i:i + n] for i in range(0, len(data), n)]


# ---------------------------------------------------------------- pure parsing
def test_ollama_final_object_maps_counts_and_duration_ns():
    u = usage_from_event({"done": True, "prompt_eval_count": 26, "eval_count": 298, "eval_duration": 4_000_000_000})
    assert u.prompt_tokens == 26
    assert u.completion_tokens == 298
    assert u.total_tokens == 324
    assert u.eval_duration_s == pytest.approx(4.0)


def test_anthropic_usage_names_are_mapped():
    u = usage_from_event({"type": "message_delta", "usage": {"input_tokens": 12, "output_tokens": 40}})
    assert (u.prompt_tokens, u.completion_tokens) == (12, 40)


def test_anthropic_message_start_nested_usage():
    u = usage_from_event({"type": "message_start", "message": {"usage": {"input_tokens": 9, "output_tokens": 1}}})
    assert (u.prompt_tokens, u.completion_tokens) == (9, 1)


def test_openai_null_usage_is_no_usage():
    assert usage_from_event({"choices": [], "usage": None}) is None


def test_non_stream_body_ollama_single_json():
    raw = json.dumps({"response": "hi", "prompt_eval_count": 3, "eval_count": 5, "eval_duration": 1_000_000_000}).encode()
    u = usage_from_body(raw)
    assert (u.prompt_tokens, u.completion_tokens, u.eval_duration_s) == (3, 5, 1.0)


def test_merge_keeps_later_nonzero_values():
    u = Usage(prompt_tokens=10, completion_tokens=1)
    u.merge(Usage(completion_tokens=40))
    assert (u.prompt_tokens, u.completion_tokens) == (10, 40)


def test_estimate_is_flagged():
    u = estimate_completion(7)
    assert u.completion_tokens == 7 and u.estimated is True


def test_inject_stream_usage_is_opt_in(monkeypatch):
    body = {"model": "m", "stream": True}
    monkeypatch.delenv("OBS_FORCE_STREAM_USAGE", raising=False)
    assert inject_stream_usage(body, "vllm") is body
    monkeypatch.setenv("OBS_FORCE_STREAM_USAGE", "true")
    out = inject_stream_usage(body, "vllm")
    assert out["stream_options"] == {"include_usage": True}
    assert "stream_options" not in body  # caller's dict untouched
    assert inject_stream_usage(body, "ollama") is body
    assert inject_stream_usage({"model": "m"}, "vllm") == {"model": "m"}


# ---------------------------------------------------------------- stream wrapper
async def test_ollama_ndjson_stream_records_usage_and_eval_duration(monkeypatch):
    from llm_gateway.telemetry import streaming
    seen = {}
    monkeypatch.setattr(streaming, "record_request_metrics", lambda **kw: seen.update(kw))
    lines = [
        {"model": "m", "message": {"role": "assistant", "content": "Hel"}, "done": False},
        {"model": "m", "message": {"role": "assistant", "content": "lo"}, "done": False},
        {"model": "m", "message": {"role": "assistant", "content": ""}, "done": True,
         "prompt_eval_count": 11, "eval_count": 2, "eval_duration": 500_000_000},
    ]
    data = ("\n".join(json.dumps(l) for l in lines) + "\n").encode()
    await _run(_split(data, 17), "application/x-ndjson")
    assert seen["prompt_tokens"] == 11
    assert seen["completion_tokens"] == 2
    assert seen["success"] is True
    assert seen["generation_tps"] == pytest.approx(4.0)  # 2 tokens / 0.5 s from eval_duration


async def test_openai_sse_with_usage_chunk():
    chunks = [
        {"choices": [{"delta": {"content": "a"}}]},
        {"choices": [{"delta": {"content": "b"}}]},
        {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}},
    ]
    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    got = {}

    async def on_complete(p, c, _):
        got["p"], got["c"] = p, c

    await _run(_split(sse.encode(), 13), "text/event-stream", "/v1/chat/completions", on_complete=on_complete)
    assert (got["p"], got["c"]) == (4, 2)


async def test_openai_sse_without_usage_is_estimated(monkeypatch):
    from llm_gateway.telemetry import streaming
    seen = {}
    monkeypatch.setattr(streaming, "record_request_metrics", lambda **kw: seen.update(kw))
    chunks = [{"choices": [{"delta": {"content": t}}]} for t in ["x", "y", "z"]]
    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    await _run([sse.encode()], "text/event-stream", "/v1/chat/completions")
    assert seen["completion_tokens"] == 3


async def test_anthropic_stream_takes_input_from_start_and_output_from_delta(monkeypatch):
    from llm_gateway.telemetry import streaming
    seen = {}
    monkeypatch.setattr(streaming, "record_request_metrics", lambda **kw: seen.update(kw))
    events = [
        {"type": "message_start", "message": {"content": [], "usage": {"input_tokens": 20, "output_tokens": 1}}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 33}},
    ]
    sse = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    await _run([sse.encode()], "text/event-stream", "/v1/messages")
    assert seen["prompt_tokens"] == 20
    assert seen["completion_tokens"] == 33


async def test_thinking_parser_branch_keeps_usage(monkeypatch):
    from llm_gateway.telemetry import streaming
    seen = {}
    monkeypatch.setattr(streaming, "record_request_metrics", lambda **kw: seen.update(kw))
    chunks = [
        {"choices": [{"delta": {"content": "<think>hm</think>ok"}}]},
        {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16}},
    ]
    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    await _run(_split(sse.encode(), 11), "text/event-stream", "/v1/chat/completions", normalize_thinking=True)
    assert (seen["prompt_tokens"], seen["completion_tokens"]) == (7, 9)


async def test_non_stream_json_body_usage_reaches_metrics(monkeypatch):
    from llm_gateway.telemetry import streaming
    seen = {}
    monkeypatch.setattr(streaming, "record_request_metrics", lambda **kw: seen.update(kw))
    body = json.dumps({"choices": [{"message": {"content": "x"}}],
                       "usage": {"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11}}).encode()
    await _run([body], "application/json", "/v1/chat/completions")
    assert (seen["prompt_tokens"], seen["completion_tokens"]) == (5, 6)
    assert seen["total_tokens"] == 11


async def test_client_cancel_still_records_and_reraises(monkeypatch):
    from llm_gateway.telemetry import streaming
    seen = {}
    monkeypatch.setattr(streaming, "record_request_metrics", lambda **kw: seen.update(kw))
    tracker = MagicMock()
    completed = []

    class _Cancelling(_Resp):
        async def aiter_raw(self):
            yield b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
            raise GeneratorExit

    from llm_gateway.telemetry.streaming import stream_with_telemetry

    async def on_complete(p, c, _):
        completed.append((p, c))

    gen = stream_with_telemetry(_Cancelling([], "text/event-stream"), 0, "u", "m", "/v1/chat/completions",
                                "o", _span(), on_complete=on_complete, tracker=tracker)
    with pytest.raises(GeneratorExit):
        async for _ in gen:
            pass
    assert seen["success"] is False
    assert completed, "on_complete must run on disconnect"
    tracker.first_token.assert_called_once()
    assert tracker.finish is not None  # track_done path invoked via the tracker
