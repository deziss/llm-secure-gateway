import pytest
from fastapi import HTTPException
from sqlmodel import select

from llm_gateway.models import SpendRecord, Owner, APIKey
from llm_gateway.services.spend_service import (
    calculate_cost,
    record_spend,
    get_monthly_spend,
    get_monthly_spend_by_key,
    check_budget,
    DEFAULT_PRICING,
)


def test_calculate_cost():
    # OpenAI gpt-4o ($0.0025/1k in, $0.01/1k out)
    cost = calculate_cost(DEFAULT_PRICING, "gpt-4o", "openai", 1000, 1000)
    assert round(cost, 4) == 0.0125

    # Local Ollama / vLLM should be zero cost
    local_cost = calculate_cost(DEFAULT_PRICING, "mistral:7b", "ollama", 5000, 2000)
    assert local_cost == 0.0


@pytest.mark.asyncio
async def test_record_and_get_monthly_spend(db_session):
    # Record spend for owner-1
    record = await record_spend(
        db_session,
        owner_id="team-alpha",
        api_key_hash="hash-123",
        model="gpt-4o-mini",
        provider="openai",
        input_tokens=10000,
        output_tokens=5000,
    )
    await db_session.commit()
    assert record.id is not None
    assert record.cost_usd > 0

    # Monthly spend summation
    spend = await get_monthly_spend(db_session, "team-alpha")
    assert spend == record.cost_usd

    # Monthly spend by key
    key_spend = await get_monthly_spend_by_key(db_session, "hash-123")
    assert key_spend == record.cost_usd


@pytest.mark.asyncio
async def test_budget_enforcement(db_session):
    # Create owner with $1.00 budget
    owner = Owner(id="budget-team", name="Budget Team", type="project", monthly_budget_usd=1.00)
    db_session.add(owner)
    await db_session.commit()

    # Under budget: check_budget should pass without exception
    await check_budget(db_session, "budget-team", None)

    # Incur spend of $1.50
    record = SpendRecord(
        owner_id="budget-team",
        model="gpt-4o",
        provider="openai",
        input_tokens=100000,
        output_tokens=100000,
        cost_usd=1.50,
    )
    db_session.add(record)
    await db_session.commit()

    # Over budget: check_budget must raise HTTPException 429
    with pytest.raises(HTTPException) as exc_info:
        await check_budget(db_session, "budget-team", None)
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["error"]["code"] == "budget_exceeded"
    assert "budget exceeded" in exc_info.value.detail["error"]["message"].lower()


@pytest.mark.asyncio
async def test_budget_warning_event_above_threshold(db_session, monkeypatch):
    """Spend >= 80% of budget (but under the cap) emits a budget_warning audit event."""
    owner = Owner(id="warn-team", name="Warn Team", type="project", monthly_budget_usd=1.00)
    db_session.add(owner)
    await db_session.commit()
    db_session.add(SpendRecord(owner_id="warn-team", model="gpt-4o", provider="openai",
                               input_tokens=1000, output_tokens=1000, cost_usd=0.85))
    await db_session.commit()

    events = []

    class _FakeAudit:
        def log(self, event_type, user, resource, decision, metadata=None, ip_address=None):
            events.append((event_type, decision, metadata))

    import llm_gateway.audit as audit_mod
    monkeypatch.setattr(audit_mod, "get_audit_logger", lambda: _FakeAudit())

    await check_budget(db_session, "warn-team", None)  # must NOT raise

    assert any(e[0] == "budget_warning" and e[1] == "warn" for e in events)
    meta = next(e[2] for e in events if e[0] == "budget_warning")
    assert meta["budget_usd"] == 1.0 and meta["used_pct"] >= 80.0


@pytest.mark.asyncio
async def test_budget_no_warning_below_threshold(db_session, monkeypatch):
    owner = Owner(id="ok-team", name="OK Team", type="project", monthly_budget_usd=10.00)
    db_session.add(owner)
    await db_session.commit()
    db_session.add(SpendRecord(owner_id="ok-team", model="gpt-4o", provider="openai",
                               input_tokens=1000, output_tokens=1000, cost_usd=0.50))
    await db_session.commit()

    events = []

    class _FakeAudit:
        def log(self, event_type, user, resource, decision, metadata=None, ip_address=None):
            events.append(event_type)

    import llm_gateway.audit as audit_mod
    monkeypatch.setattr(audit_mod, "get_audit_logger", lambda: _FakeAudit())

    await check_budget(db_session, "ok-team", None)
    assert "budget_warning" not in events
