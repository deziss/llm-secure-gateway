"""Dashboard summary aggregates, run against a real (in-memory SQLite) database."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from llm_gateway.models import APIKey, AuditLog, CachedResponse, LLMBackend, Owner, SpendRecord
from llm_gateway.routers.dashboard import dashboard_summary


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s
    await engine.dispose()


async def _seed(s):
    now = _now()
    s.add(Owner(id="team:a", type="project", name="Team A", monthly_budget_usd=10.0))
    s.add(Owner(id="team:b", type="project", name="Team B", monthly_budget_usd=100.0))
    s.add(APIKey(key_hash="h1", prefix="sk-aaa", owner_id="team:a", scopes=["llm:chat"],
                 monthly_budget_usd=5.0, expires_at=now + timedelta(days=2)))
    s.add(APIKey(key_hash="h2", prefix="sk-bbb", owner_id="team:b", scopes=["llm:chat"],
                 expires_at=now + timedelta(days=30)))
    # Today: 9.0 on team:a via key h1 (90% of owner budget, 180% of key budget)
    s.add(SpendRecord(owner_id="team:a", api_key_hash="h1", model="gpt-4o", provider="openai",
                      input_tokens=100, output_tokens=50, cost_usd=9.0, created_at=now))
    s.add(SpendRecord(owner_id="team:b", api_key_hash="h2", model="llama3", provider="ollama",
                      input_tokens=10, output_tokens=5, cost_usd=0.0, created_at=now))
    s.add(SpendRecord(owner_id="team:b", api_key_hash="h2", model="llama3", provider="ollama",
                      input_tokens=10, output_tokens=5, cost_usd=0.0, created_at=now))
    s.add(CachedResponse(cache_key="c1", model="gpt-4o", response_json="{}", input_tokens=100,
                         output_tokens=50, hit_count=3, expires_at=now + timedelta(hours=1)))
    s.add(CachedResponse(cache_key="c2", model="gpt-4o", response_json="{}", hit_count=99,
                         expires_at=now - timedelta(hours=1)))  # expired: ignored
    s.add(AuditLog(event_type="policy_eval", identity="apikey:x", resource="/v1/chat", decision="deny", timestamp=now))
    s.add(AuditLog(event_type="policy_eval", identity="apikey:y", resource="/v1/chat", decision="allow", timestamp=now))
    s.add(AuditLog(event_type="key_created", identity="admin", resource="k", decision="error",
                   timestamp=now - timedelta(days=3)))  # outside 24h
    s.add(LLMBackend(name="b1", base_url="http://b1", backend_type="ollama", models=["llama3", "qwen"]))
    await s.commit()


async def test_summary_aggregates(session):
    await _seed(session)
    service = AsyncMock()
    service.get_setting = AsyncMock(return_value="true")
    out = await dashboard_summary(session=session, service=service, user=None)

    today = out["spend"]["today"]
    assert today["cost_usd"] == 9.0
    assert today["requests"] == 3
    assert (today["input_tokens"], today["output_tokens"]) == (120, 60)
    assert len(out["spend"]["daily"]) == 14
    assert out["spend"]["daily"][-1]["cost_usd"] == 9.0

    assert out["top_models"][0] == {"model": "llama3", "requests": 2, "tokens": 30, "cost_usd": 0.0}

    budgets = {(b["kind"], b["label"]) for b in out["alerts"]["budgets"]}
    assert budgets == {("owner", "Team A"), ("key", "sk-aaa…")}
    assert [k["label"] for k in out["alerts"]["expiring_keys"]] == ["sk-aaa…"]

    assert out["cache"] == {"entries": 1, "hits": 3, "tokens_saved": 450}

    sec = out["security"]
    assert sec["audit_enabled"] is True
    assert (sec["denied_24h"], sec["errors_24h"]) == (1, 0)
    assert len(sec["recent"]) == 1

    assert out["backends"] == [{"name": "b1", "backend_type": "ollama", "models": 2, "state": "idle", "avg_latency_ms": None}]


async def test_summary_empty_database(session):
    service = AsyncMock()
    service.get_setting = AsyncMock(return_value="false")
    out = await dashboard_summary(session=session, service=service, user=None)
    assert out["spend"]["month"]["cost_usd"] == 0
    assert out["top_models"] == [] and out["backends"] == []
    assert out["alerts"] == {"budgets": [], "expiring_keys": []}
    assert out["security"]["audit_enabled"] is False
