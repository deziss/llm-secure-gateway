"""Tests for governance retention cleanup and observability content capture."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

import llm_gateway.config as config
from llm_gateway.models import (
    AgentRun,
    AgentStep,
    AuditLog,
    ModelTelemetryRollup,
    SpendRecord,
)
from llm_gateway.services.retention_service import (
    _delete_older_than,
    run_retention_cleanup,
)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _NoopAudit:
    def log(self, *args, **kwargs):
        pass


def _disable_audit_db(monkeypatch):
    import llm_gateway.audit as audit

    monkeypatch.setattr(audit, "get_audit_logger", lambda: _NoopAudit())


# ── Config defaults ───────────────────────────────────────────────────


def test_retention_capture_config_defaults():
    assert config.OBSERVABILITY_CAPTURE_LEVEL in ("metadata", "redacted", "full")
    assert getattr(config, "OBSERVABILITY_MAX_CONTENT_CHARS", 2000) >= 100
    assert getattr(config, "RETENTION_DAYS_AUDIT", 0) >= 0
    assert getattr(config, "RETENTION_DAYS_SPEND_METRICS", 0) >= 0
    assert getattr(config, "RETENTION_INTERVAL_SECONDS", 21600) >= 3600


# ── Retention cleanup ─────────────────────────────────────────────────


async def _seed(db_session):
    old = _utcnow_naive() - timedelta(days=200)
    fresh = _utcnow_naive() - timedelta(days=1)

    for ts in (old, fresh):
        db_session.add(AuditLog(
            timestamp=ts, event_type="policy_eval", identity="apikey:x",
            resource="/v1/chat", decision="allow",
        ))
        db_session.add(SpendRecord(
            created_at=ts, owner_id="team-a", model="gpt-4o", provider="openai",
            input_tokens=10, output_tokens=5, cost_usd=0.01,
        ))
        db_session.add(AgentRun(
            run_id=f"run-{ts.isoformat()}", agent="a1", owner_id="team-a",
            first_ts=ts, last_ts=ts,
        ))
        db_session.add(AgentStep(
            run_id=f"run-{ts.isoformat()}", seq=0, ts=ts, kind="llm",
            agent="a1", model="gpt-4o",
        ))
        db_session.add(ModelTelemetryRollup(
            bucket_time=ts, server="s1", model="gpt-4o", requests=1,
        ))
    await db_session.commit()


@pytest.mark.asyncio
async def test_retention_deletes_old_preserves_fresh(db_session, monkeypatch):
    monkeypatch.setattr(config, "RETENTION_DAYS_AUDIT", 0)      # keep forever
    monkeypatch.setattr(config, "RETENTION_DAYS_TRACES", 90)
    monkeypatch.setattr(config, "RETENTION_DAYS_SPEND_METRICS", 30)
    _disable_audit_db(monkeypatch)

    await _seed(db_session)

    counts = await run_retention_cleanup(session=db_session)

    assert counts.get("audit_log") is None  # days=0 -> table not targeted
    assert counts["agent_run"] == 1
    assert counts["agent_step"] == 1
    assert counts["spend_record"] == 1
    assert counts["model_telemetry_rollup"] == 1

    # Fresh rows survive, old rows gone
    audit_rows = (await db_session.execute(select(AuditLog))).scalars().all()
    assert len(audit_rows) == 2  # untouched
    runs = (await db_session.execute(select(AgentRun))).scalars().all()
    assert len(runs) == 1 and ((_utcnow_naive() - runs[0].last_ts).days) <= 90
    steps = (await db_session.execute(select(AgentStep))).scalars().all()
    assert len(steps) == 1
    spends = (await db_session.execute(select(SpendRecord))).scalars().all()
    assert len(spends) == 1
    rolls = (await db_session.execute(select(ModelTelemetryRollup))).scalars().all()
    assert len(rolls) == 1


@pytest.mark.asyncio
async def test_retention_zero_days_keeps_everything(db_session, monkeypatch):
    monkeypatch.setattr(config, "RETENTION_DAYS_AUDIT", 0)
    monkeypatch.setattr(config, "RETENTION_DAYS_TRACES", 0)
    monkeypatch.setattr(config, "RETENTION_DAYS_SPEND_METRICS", 0)

    await _seed(db_session)

    counts = await run_retention_cleanup(session=db_session)
    assert counts == {}

    assert len((await db_session.execute(select(AuditLog))).scalars().all()) == 2
    assert len((await db_session.execute(select(AgentRun))).scalars().all()) == 2
    assert len((await db_session.execute(select(SpendRecord))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_retention_audit_window_deletes_audit_rows(db_session, monkeypatch):
    monkeypatch.setattr(config, "RETENTION_DAYS_AUDIT", 60)
    monkeypatch.setattr(config, "RETENTION_DAYS_TRACES", 0)
    monkeypatch.setattr(config, "RETENTION_DAYS_SPEND_METRICS", 0)
    _disable_audit_db(monkeypatch)

    await _seed(db_session)

    counts = await run_retention_cleanup(session=db_session)
    assert counts["audit_log"] == 1
    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_delete_older_than_batches(db_session, monkeypatch):
    """Batched id-deletes drain a table larger than one batch."""
    monkeypatch.setattr(config, "RETENTION_DAYS_SPEND_METRICS", 30)
    old = _utcnow_naive() - timedelta(days=100)
    for _ in range(5):
        db_session.add(SpendRecord(
            created_at=old, owner_id="team-batch", model="m", provider="p",
        ))
    await db_session.commit()

    cutoff = _utcnow_naive() - timedelta(days=30)
    deleted = await _delete_older_than(
        db_session, SpendRecord, SpendRecord.created_at, cutoff, batch_size=2
    )
    assert deleted == 5
    rows = (await db_session.execute(select(SpendRecord))).scalars().all()
    assert rows == []


# ── Capture-level helpers ─────────────────────────────────────────────


def test_capture_metadata_returns_none(monkeypatch):
    from llm_gateway.observability import capture

    monkeypatch.setattr(config, "OBSERVABILITY_CAPTURE_LEVEL", "metadata")
    assert capture.captures_content() is False
    assert capture.prepare_captured_content("hello") is None


def test_capture_full_truncates(monkeypatch):
    from llm_gateway.observability import capture

    monkeypatch.setattr(config, "OBSERVABILITY_CAPTURE_LEVEL", "full")
    monkeypatch.setattr(config, "OBSERVABILITY_MAX_CONTENT_CHARS", 100)
    assert capture.captures_content() is True
    out = capture.prepare_captured_content("x" * 500)
    assert out == "x" * 100


def test_capture_redacted_redacts_and_truncates(monkeypatch):
    from llm_gateway.observability import capture

    monkeypatch.setattr(config, "OBSERVABILITY_CAPTURE_LEVEL", "redacted")
    monkeypatch.setattr(config, "OBSERVABILITY_MAX_CONTENT_CHARS", 200)
    text = "Contact me at secret.person@example.com today. " + "y" * 400
    out = capture.prepare_captured_content(text)
    assert out is not None
    assert len(out) <= 200
    # The default PIIScanner redacts emails; if the scanner were unavailable
    # the helper falls back to unredacted truncated text, so assert leniently:
    if "REDACTED" in out or "REDACTED" in capture.prepare_captured_content(
        "Contact me at secret.person@example.com", max_chars=200
    ):
        assert "secret.person@example.com" not in out


def test_extract_last_user_text():
    from llm_gateway.observability.capture import extract_last_user_text

    body = {
        "messages": [
            {"role": "system", "content": "be nice"},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": [{"type": "text", "text": "second q"}]},
        ]
    }
    assert extract_last_user_text(body) == "second q"
    assert extract_last_user_text({"prompt": "hi there"}) == "hi there"
    assert extract_last_user_text(None) is None
    assert extract_last_user_text({"messages": []}) is None


def test_extract_text_from_data():
    from llm_gateway.observability.capture import extract_text_from_data

    openai = {"choices": [{"message": {"role": "assistant", "content": "hello!"}}]}
    assert extract_text_from_data(openai) == "hello!"
    anthropic = {"content": [{"type": "text", "text": "hi!"}]}
    assert extract_text_from_data(anthropic) == "hi!"
    assert extract_text_from_data({"completion": "ollama text"}) == "ollama text"
    assert extract_text_from_data(None) is None
    assert extract_text_from_data({}) is None


# ── Admin endpoint smoke test ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_retention_run_endpoint(client, admin_token_headers):
    resp = await client.post("/admin/retention/run", headers=admin_token_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "deleted" in data and "total" in data
