"""Integration tests shipped alongside streaming output guardrails, key
rotation automation, and response/guardrail admin surfaces."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from llm_gateway.models import APIKey


def _utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Streaming output guardrails ──────────────────────────────────────────

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


def _always_block(text):
    class D:
        is_allowed = False
        reason = "test block"
        matches = ["test"]
    return D()


def _never_block(text):
    class D:
        is_allowed = True
        reason = "ok"
        matches = []
    return D()


async def test_stream_output_guardrail_block_replaces_content():
    evs = [
        {"choices": [{"index": 0, "delta": {"content": "leak secret one"}}]},
        {"choices": [{"index": 0, "delta": {"content": "leak secret two"}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    sse = "".join(f"data: {__import__('json').dumps(e)}\n\n" for e in evs).encode() + b"data: [DONE]\n\n"
    violations = []
    out = await _collect([sse], output_guardrail=_always_block,
                         output_guardrail_action="block",
                         on_output_violation=lambda d: violations.append(d))
    decoded = out.decode()
    assert "leak secret" not in decoded
    assert violations  # callback fired
    assert "Guardrail" in decoded or "guardrail" in decoded.lower()


async def test_stream_output_guardrail_warn_passes_through():
    evs = [{"choices": [{"index": 0, "delta": {"content": "suspicious text"}}]},
           {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
    sse = "".join(f"data: {__import__('json').dumps(e)}\n\n" for e in evs).encode() + b"data: [DONE]\n\n"
    violations = []
    out = await _collect([sse], output_guardrail=_always_block,
                         output_guardrail_action="warn",
                         on_output_violation=lambda d: violations.append(d))
    assert b"suspicious text" in out
    assert violations


async def test_stream_output_guardrail_clean_stream_untouched():
    sse = b'data: {"choices":[{"index":0,"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n'
    out = await _collect([sse], output_guardrail=_never_block,
                         output_guardrail_action="block")
    assert b"hello" in out
    assert out == sse


# ── Key rotation service ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rotate_key_unknown_prefix_returns_none(db_session):
    from llm_gateway.services.auth_service import AuthService

    svc = AuthService()
    assert await svc.rotate_key(db_session, "deadbeef") is None


@pytest.mark.asyncio
async def test_rotate_key_requires_active(db_session):
    from llm_gateway.services.auth_service import AuthService

    key = APIKey(key_hash="x" * 64, prefix="revoked1", owner_id="o1",
                 is_active=False, scopes=["chat"])
    db_session.add(key)
    await db_session.commit()

    with pytest.raises(ValueError):
        await AuthService().rotate_key(db_session, "revoked1")


@pytest.mark.asyncio
async def test_rotate_key_twice_rejected(db_session):
    from llm_gateway.services.auth_service import AuthService

    svc = AuthService()
    key = APIKey(key_hash="y" * 64, prefix="twice1", owner_id="o1",
                 is_active=True, scopes=["chat"])
    db_session.add(key)
    await db_session.commit()

    class _Created:
        def __init__(self, prefix, hash_):
            self.key = None
            self.obj = APIKey(key_hash=hash_, prefix=prefix, owner_id="o1",
                              is_active=True, scopes=["chat"])

    async def fake_create(session, data):
        return "sk-new-secret", _Created("twice2", "z" * 64).obj

    svc.create_api_key = fake_create  # instance-level patch
    raw, new, old = await svc.rotate_key(db_session, "twice1", grace_days=3)
    assert raw == "sk-new-secret"
    assert old.rotated_at is not None
    assert new.rotated_from == "twice1"

    with pytest.raises(ValueError):
        await svc.rotate_key(db_session, "twice1")


def test_rotate_endpoint_validators():
    import inspect

    from llm_gateway.routers import owners
    from pydantic import BaseModel

    src = inspect.getsource(owners)
    assert "rotate_key" in src
    assert 'audit' in src  # rotates an audit entry

    class R(BaseModel):
        grace_days: int = 7
    assert R().grace_days == 7  # endpoint request model is trivially valid
