"""Tests for response-side (streaming + non-stream) PII masking."""
import json
from unittest.mock import patch

import pytest

from llm_gateway.services.config_service import ConfigService


class _ChunkedResponse:
    def __init__(self, chunks, content_type, status_code=200):
        self._chunks = chunks
        self.headers = {"content-type": content_type}
        self.status_code = status_code

    async def aiter_raw(self):
        for c in self._chunks:
            yield c


async def _collect(chunks, content_type="text/event-stream", **kwargs):
    from unittest.mock import MagicMock

    from llm_gateway.telemetry.streaming import stream_with_telemetry

    span = MagicMock()
    span.is_recording.return_value = False
    out = b""
    async for b in stream_with_telemetry(
        _ChunkedResponse(chunks, content_type), 0, "u", "m", "/v1/chat/completions", "o", span,
        **kwargs,
    ):
        out += b
    return out


def _scrubber(categories=None):
    from llm_gateway.services.pii_service import PIIScanner, PIIScrubber

    return PIIScrubber(PIIScanner(), categories=categories)


def _sse_events(raw: bytes):
    return [json.loads(l[6:]) for l in raw.decode().split("\n") if l.startswith("data: {")]


# ── PIIScrubber unit ─────────────────────────────────────────────────────

class TestPIIScrubber:

    def test_scrub_full_masks_email_and_ssn(self):
        scrub = _scrubber()
        out = scrub.scrub_full("mail john.doe@example.com ssn 123-45-6789")
        assert "john.doe@example.com" not in out
        assert "123-45-6789" not in out
        assert "[EMAIL_REDACTED]" in out and "[SSN_REDACTED]" in out
        assert scrub.total_masked >= 2

    def test_feed_flush_splits_secret_across_chunks(self):
        scrub = _scrubber()
        # SSN split across two feeds (carry window must hold the partial)
        emit1 = scrub.feed("his SSN is 123-")
        emit2 = scrub.feed("45-6789 ok")
        tail = scrub.flush()
        joined = emit1 + emit2 + tail
        assert "123-45-6789" not in joined
        assert "[SSN_REDACTED]" in joined

    def test_category_filter(self):
        scrub = _scrubber(categories=["contact"])
        out = scrub.scrub_full("email a@b.com key sk-abcdefghijklmnopqrstuvwxyz012345")
        assert "[EMAIL_REDACTED]" in out
        # credentials category is not in the allow-list -> untouched
        assert "sk-abcdefghijklmnopqrstuvwxyz012345" in out

    def test_replacements_recorded(self):
        scrub = _scrubber()
        scrub.scrub_full("card 4111111111111111")
        assert scrub.replacements.get("4111111111111111") == "[CREDIT_CARD_REDACTED]"


# ── Streaming SSE masking ────────────────────────────────────────────────

async def test_stream_masks_email_split_across_deltas():
    e1 = {"choices": [{"index": 0, "delta": {"content": "reach me at bob.sm"}}]}
    e2 = {"choices": [{"index": 0, "delta": {"content": "ith@example.com now"}}]}
    e3 = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    sse = (f"data: {json.dumps(e1)}\n\n"
           f"data: {json.dumps(e2)}\n\n"
           f"data: {json.dumps(e3)}\n\n"
           "data: [DONE]\n\n").encode()
    # 11-byte chunks also split the JSON lines themselves
    chunks = [sse[i:i + 11] for i in range(0, len(sse), 11)]

    scrub = _scrubber()
    out = await _collect(chunks, pii_scrubber=scrub)
    text = "".join(
        (ev.get("choices") or [{}])[0].get("delta", {}).get("content", "")
        for ev in _sse_events(out) if ev.get("choices")
    )
    assert "bob.smith@example.com" not in text
    assert "[EMAIL_REDACTED]" in text
    assert "data: [DONE]" in out.decode()
    assert scrub.total_masked >= 1


async def test_stream_passthrough_when_disabled():
    payload = b'data: {"choices":[{"index":0,"delta":{"content":"a@b.com"}}]}\n\ndata: [DONE]\n\n'
    out = await _collect([payload])
    assert out == payload


async def test_stream_flush_preserves_full_secret_before_done():
    """SSN split by chunk boundaries: reassembled text is masked (never raw)
    and [DONE] still terminates the stream."""
    e1 = {"choices": [{"index": 0, "delta": {"content": "id 123-45-6789 end"}}]}
    e2 = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    sse = (f"data: {json.dumps(e1)}\n\ndata: {json.dumps(e2)}\n\ndata: [DONE]\n\n").encode()
    scrub = _scrubber()
    out = await _collect([sse], pii_scrubber=scrub)
    decoded = out.decode()
    text = "".join(
        (ev.get("choices") or [{}])[0].get("delta", {}).get("content", "")
        for ev in _sse_events(out) if ev.get("choices")
    )
    assert "123-45-6789" not in text
    assert "[SSN_REDACTED]" in text
    assert decoded.rstrip().endswith("data: [DONE]")


async def test_whole_json_body_masked():
    body = json.dumps({
        "choices": [{"index": 0, "message": {"role": "assistant",
                                             "content": "SSN: 123-45-6789, email x@y.co"}}]
    }).encode()
    scrub = _scrubber()
    out = await _collect([body], content_type="application/json", pii_scrubber=scrub)
    obj = json.loads(out)
    content = obj["choices"][0]["message"]["content"]
    assert "123-45-6789" not in content
    assert "[SSN_REDACTED]" in content
    assert scrub.total_masked >= 1


async def test_whole_json_body_split_chunks_still_masked():
    body = json.dumps({
        "choices": [{"index": 0, "message": {"role": "assistant",
                                             "content": "call +1 555 123 4567"}}]
    }).encode()
    chunks = [body[i:i + 16] for i in range(0, len(body), 16)]
    scrub = _scrubber()
    out = await _collect(chunks, content_type="application/json", pii_scrubber=scrub)
    obj = json.loads(out)
    assert "555 123 4567" not in obj["choices"][0]["message"]["content"]


async def test_on_pii_mask_callback_fires():
    events = []
    e1 = {"choices": [{"index": 0, "delta": {"content": "ssn 123-45-6789"}}]}
    e2 = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    sse = (f"data: {json.dumps(e1)}\n\ndata: {json.dumps(e2)}\n\ndata: [DONE]\n\n").encode()
    await _collect([sse], pii_scrubber=_scrubber(),
                   on_pii_mask=lambda total, cats: events.append((total, cats)))
    assert events and events[0][0] >= 1
    assert "identity" in events[0][1]


# ── Helper: output_pii_kwargs ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_output_pii_kwargs_disabled_returns_empty():
    from unittest.mock import AsyncMock, MagicMock

    from llm_gateway.proxy_helpers import output_pii_kwargs

    session = MagicMock()
    with patch_set(settings={"GUARDRAIL_OUTPUT_PII_MASKING": "", "ENABLE_PII_MASKING": "false",
                             "PII_MASK_CATEGORIES": ""}):
        kwargs = await output_pii_kwargs(session, model="m")
    assert kwargs == {}


def patch_set(settings):
    """Patch ConfigService.get_setting (the `from . import config_service`
    imports resolve to the singleton instance re-exported by services/__init__)."""
    from contextlib import contextmanager

    async def fake_get(self, session, key, default=""):
        return settings.get(key, default)

    @contextmanager
    def cm():
        with patch.object(ConfigService, "get_setting", fake_get):
            yield
    return cm()


@pytest.mark.asyncio
async def test_output_pii_kwargs_enabled_builds_scrubber():
    from unittest.mock import MagicMock

    from llm_gateway.proxy_helpers import output_pii_kwargs

    session = MagicMock()
    with patch_set(settings={"GUARDRAIL_OUTPUT_PII_MASKING": "true",
                             "PII_MASK_CATEGORIES": ""}):
        kwargs = await output_pii_kwargs(session, model="m", owner_id="o")
    assert kwargs.get("pii_scrubber") is not None
    assert callable(kwargs.get("on_pii_mask"))


# ── mask_response_pii (non-stream helper) ────────────────────────────────

@pytest.mark.asyncio
async def test_mask_response_pii_openai_body():
    from unittest.mock import MagicMock

    from llm_gateway.proxy_helpers import mask_response_pii

    body = {"choices": [{"message": {"role": "assistant", "content": "card 4111111111111111"}}]}
    with patch_set(settings={"GUARDRAIL_OUTPUT_PII_MASKING": "true", "PII_MASK_CATEGORIES": ""}):
        out = await mask_response_pii(MagicMock(), body, model="m")
    assert "4111111111111111" not in out["choices"][0]["message"]["content"]


@pytest.mark.asyncio
async def test_mask_response_pii_disabled_untouched():
    from unittest.mock import MagicMock

    from llm_gateway.proxy_helpers import mask_response_pii

    body = {"choices": [{"message": {"content": "card 4111111111111111"}}]}
    with patch_set(settings={"GUARDRAIL_OUTPUT_PII_MASKING": "", "ENABLE_PII_MASKING": "false",
                             "PII_MASK_CATEGORIES": ""}):
        out = await mask_response_pii(MagicMock(), body, model="m")
    assert out["choices"][0]["message"]["content"] == "card 4111111111111111"
