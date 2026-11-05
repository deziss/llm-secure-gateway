"""Tests for Model Pricing Catalog, Auto-Detection on Provider Key Addition,
and Dynamic Spend Tracking.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from sqlmodel import select

from llm_gateway.main import app
from llm_gateway.models import ModelPricing, Owner, OwnerType, ProviderKey
from llm_gateway.services.pricing_catalog import (
    lookup_catalog_rate,
    auto_seed_pricing_for_provider,
    PROVIDER_PRICING_CATALOG,
)
from llm_gateway.services.owner_service import get_owner_service
from llm_gateway.services.spend_service import (
    calculate_cost,
    record_spend,
    list_model_pricing,
    upsert_model_pricing,
    delete_model_pricing,
    sync_all_catalog_pricing,
    _load_pricing,
    _get_model_cost,
)


def test_pricing_catalog_lookup():
    """Verify lookup_catalog_rate resolves standard provider rates."""
    # OpenAI gpt-4o: $2.50 / 1M in, $10.00 / 1M out
    rate_openai = lookup_catalog_rate("gpt-4o", "openai")
    assert rate_openai is not None
    assert rate_openai["input_cost_per_1m"] == 2.50
    assert rate_openai["output_cost_per_1m"] == 10.00
    assert rate_openai["cached_input_cost_per_1m"] == 1.25

    # Anthropic Claude 3.5 Sonnet: $3.00 / 1M in, $15.00 / 1M out
    rate_anthropic = lookup_catalog_rate("claude-3-5-sonnet-20241022", "anthropic")
    assert rate_anthropic is not None
    assert rate_anthropic["input_cost_per_1m"] == 3.00
    assert rate_anthropic["output_cost_per_1m"] == 15.00

    # Local Ollama / vLLM: free ($0.0)
    rate_ollama = lookup_catalog_rate("any-local-model", "ollama")
    assert rate_ollama is not None
    assert rate_ollama["input_cost_per_1m"] == 0.0
    assert rate_ollama["output_cost_per_1m"] == 0.0

    # Unknown cloud model falls back to None from catalog
    rate_unknown = lookup_catalog_rate("completely-fictional-model-xyz", "some-provider")
    assert rate_unknown is None


@pytest.mark.asyncio
async def test_auto_seed_when_owner_adds_provider_key(db_session):
    """When an owner registers a provider key, model pricing is automatically seeded."""
    owner = Owner(id="corp-owner", name="Corp Project", type=OwnerType.PROJECT)
    db_session.add(owner)
    await db_session.commit()

    service = get_owner_service()

    # Owner adds an Anthropic key
    pk = await service.set_provider_key(db_session, "corp-owner", "anthropic", "sk-ant-test-12345")
    assert pk.id is not None
    assert pk.provider_id == "anthropic"

    # Verify Anthropic models were automatically seeded into ModelPricing table
    res = await db_session.execute(
        select(ModelPricing).where(ModelPricing.provider == "anthropic")
    )
    anthropic_rules = res.scalars().all()
    assert len(anthropic_rules) >= 3

    model_names = {r.model for r in anthropic_rules}
    assert "claude-3-5-sonnet-20241022" in model_names
    assert "claude-3-5-haiku-20241022" in model_names

    # Check that they are tagged as auto-detected provider catalog
    sonnet_rule = next(r for r in anthropic_rules if r.model == "claude-3-5-sonnet-20241022")
    assert sonnet_rule.source == "provider_catalog"
    assert sonnet_rule.input_cost_per_1m == 3.00
    assert sonnet_rule.output_cost_per_1m == 15.00


@pytest.mark.asyncio
async def test_manual_override_preserved_on_auto_seed(db_session):
    """Manual user overrides are preserved and not clobbered when provider key is re-added."""
    # Pre-create a manual override for gpt-4o with custom negotiated discount ($1.00 / 1M in)
    custom_rule = await upsert_model_pricing(
        db_session,
        model="gpt-4o",
        provider="openai",
        input_cost_per_1m=1.00,
        output_cost_per_1m=5.00,
        description="Negotiated Enterprise Tier",
    )
    assert custom_rule.source == "manual"
    assert custom_rule.input_cost_per_1m == 1.00

    # Owner adds OpenAI key
    owner = Owner(id="openai-owner", name="OpenAI Project", type=OwnerType.PROJECT)
    db_session.add(owner)
    await db_session.commit()

    service = get_owner_service()
    await service.set_provider_key(db_session, "openai-owner", "openai", "sk-proj-custom-key")

    # Verify gpt-4o is STILL the manual rate ($1.00, not standard $2.50)
    res = await db_session.execute(
        select(ModelPricing).where(
            ModelPricing.model == "gpt-4o",
            ModelPricing.provider == "openai",
        )
    )
    gpt4o = res.scalars().first()
    assert gpt4o.source == "manual"
    assert gpt4o.input_cost_per_1m == 1.00
    assert gpt4o.output_cost_per_1m == 5.00

    # Other OpenAI models (like gpt-4o-mini) were seeded with catalog defaults
    res_mini = await db_session.execute(
        select(ModelPricing).where(
            ModelPricing.model == "gpt-4o-mini",
            ModelPricing.provider == "openai",
        )
    )
    mini = res_mini.scalars().first()
    assert mini is not None
    assert mini.source == "provider_catalog"
    assert mini.input_cost_per_1m == 0.15


@pytest.mark.asyncio
async def test_calculate_cost_with_1m_standard_and_cache(db_session):
    """Test calculate_cost with 1M-token pricing, prompt caching, and hierarchy."""
    pricing = {
        ("test-model", "openai"): {
            "input_per_1m": 2.00,
            "output_per_1m": 8.00,
            "cached_input_per_1m": 0.50,
        }
    }

    # 100K prompt tokens + 20K output tokens (no cache)
    # in: 100,000 / 1M * $2.00 = $0.20
    # out: 20,000 / 1M * $8.00 = $0.16
    # total = $0.36
    cost = calculate_cost(
        pricing,
        model="test-model",
        provider="openai",
        input_tokens=100_000,
        output_tokens=20_000,
        cached_tokens=0,
    )
    assert round(cost, 6) == 0.36

    # 100K prompt with 50K cached tokens
    # regular in: 50,000 / 1M * $2.00 = $0.10
    # cached in: 50,000 / 1M * $0.50 = $0.025
    # out: 20,000 / 1M * $8.00 = $0.16
    # total = $0.285
    cached_cost = calculate_cost(
        pricing,
        model="test-model",
        provider="openai",
        input_tokens=100_000,
        output_tokens=20_000,
        cached_tokens=50_000,
    )
    assert round(cached_cost, 6) == 0.285


@pytest.mark.asyncio
async def test_record_spend_integrates_with_model_pricing(db_session):
    """record_spend loads pricing from DB and records exact cost."""
    # Insert custom rate into DB
    await upsert_model_pricing(
        db_session,
        model="special-rag-model",
        provider="groq",
        input_cost_per_1m=0.50,
        output_cost_per_1m=1.00,
    )

    record = await record_spend(
        session=db_session,
        owner_id="rag-team",
        api_key_hash="hash-abc",
        model="special-rag-model",
        provider="groq",
        input_tokens=2_000_000,   # 2M tokens * $0.50 = $1.00
        output_tokens=1_000_000,  # 1M tokens * $1.00 = $1.00
    )
    await db_session.commit()

    assert record.id is not None
    assert round(record.cost_usd, 4) == 2.0000


@pytest.mark.asyncio
async def test_admin_pricing_rest_api(client: AsyncClient, admin_token_headers: dict, db_session):
    """Verify REST API for admin pricing: GET, POST, PUT, DELETE, sync."""
    headers = admin_token_headers

    # 1. POST /admin/pricing - Create a custom pricing rule
    post_resp = await client.post(
        "/admin/pricing",
        headers=headers,
        json={
            "model": "deepseek-coder-v2",
            "provider": "deepseek",
            "input_cost_per_1m": 0.25,
            "output_cost_per_1m": 0.50,
            "cached_input_cost_per_1m": 0.05,
            "description": "Code specialist model",
        },
    )
    assert post_resp.status_code == 201
    created = post_resp.json()
    assert created["model"] == "deepseek-coder-v2"
    assert created["input_cost_per_1m"] == 0.25
    assert created["source"] == "manual"
    pricing_id = created["id"]

    # 2. GET /admin/pricing - Verify listing contains created rule
    get_resp = await client.get("/admin/pricing", headers=headers)
    assert get_resp.status_code == 200
    items = get_resp.json()
    assert any(item["model"] == "deepseek-coder-v2" for item in items)

    # 3. PUT /admin/pricing/{id} - Update the rule
    put_resp = await client.put(
        f"/admin/pricing/{pricing_id}",
        headers=headers,
        json={
            "input_cost_per_1m": 0.20,
            "output_cost_per_1m": 0.40,
            "description": "Updated pricing",
        },
    )
    assert put_resp.status_code == 200
    updated = put_resp.json()
    assert updated["input_cost_per_1m"] == 0.20
    assert updated["output_cost_per_1m"] == 0.40

    # 4. POST /admin/pricing/sync-catalog - Trigger catalog sync
    sync_resp = await client.post(
        "/admin/pricing/sync-catalog",
        headers=headers,
        json={"provider": "groq"},
    )
    assert sync_resp.status_code == 200
    sync_data = sync_resp.json()
    assert sync_data["status"] == "synced"

    # 5. DELETE /admin/pricing/{id} - Delete the rule
    del_resp = await client.delete(f"/admin/pricing/{pricing_id}", headers=headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    # Verify deletion
    del_again = await client.delete(f"/admin/pricing/{pricing_id}", headers=headers)
    assert del_again.status_code == 404
