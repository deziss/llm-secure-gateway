import pytest
from sqlmodel import select

from llm_gateway.models import ModelAlias, FallbackChain, LLMBackend, BackendType
from llm_gateway.services.fallback_service import (
    resolve_alias,
    get_fallback_chain,
    get_chain_targets,
    list_aliases,
    list_chains,
)


@pytest.mark.asyncio
async def test_fallback_chains_and_aliases(db_session):
    # 1. Create a fallback chain
    targets = [
        {"backend_name": "primary-openai", "model": "gpt-4o"},
        {"backend_name": "secondary-anthropic", "model": "claude-3-5-sonnet-20241022"},
    ]
    chain = FallbackChain(name="prod-smart", targets=targets)
    db_session.add(chain)
    await db_session.commit()
    await db_session.refresh(chain)

    assert chain.id is not None
    loaded_targets = await get_chain_targets(db_session, chain.id)
    assert len(loaded_targets) == 2

    # 2. Create backend and model alias
    backend = LLMBackend(
        name="primary-openai",
        base_url="http://localhost:8000",
        backend_type=BackendType.OPENAI,
        models=["gpt-4o"],
    )
    db_session.add(backend)

    alias = ModelAlias(
        alias="smart-model",
        backend_name="primary-openai",
        model_name="gpt-4o",
        fallback_chain_id=chain.id,
    )
    db_session.add(alias)
    await db_session.commit()

    # 3. Resolve alias
    b_name, real_m, c_id = await resolve_alias(db_session, "smart-model")
    assert b_name == "primary-openai"
    assert real_m == "gpt-4o"
    assert c_id == chain.id

    # 4. Unregistered alias should pass-through
    b_name, real_m, c_id = await resolve_alias(db_session, "unknown-custom-model")
    assert b_name is None
    assert real_m == "unknown-custom-model"
    assert c_id is None
