import pytest
from llm_gateway.services.cache_service import (
    compute_cache_key,
    is_cacheable,
    get_cached,
    store_cached,
)


def test_cacheable_and_key_determinism():
    messages = [{"role": "user", "content": "Hello world"}]

    # Streaming is not cacheable
    assert not is_cacheable({"messages": messages, "stream": True})

    # High temperature (> 0.1) is not cacheable
    assert not is_cacheable({"messages": messages, "temperature": 0.7})

    # Low temperature is cacheable
    assert is_cacheable({"messages": messages, "temperature": 0.0})

    # Keys are deterministic
    k1 = compute_cache_key("gpt-4o", messages, temperature=0.0)
    k2 = compute_cache_key("gpt-4o", messages, temperature=0.0)
    k3 = compute_cache_key("gpt-4o-mini", messages, temperature=0.0)
    assert k1 == k2
    assert k1 != k3


@pytest.mark.asyncio
async def test_cache_store_and_retrieve(db_session):
    messages = [{"role": "user", "content": "Ping"}]
    key = compute_cache_key("mock-model", messages, 0.0)

    # Empty lookup
    resp = await get_cached(key, db_session)
    assert resp is None

    # Store response
    cached_payload = {"choices": [{"message": {"role": "assistant", "content": "Pong"}}]}
    await store_cached(
        key, cached_payload, "mock-model", input_tokens=5, output_tokens=5, ttl_seconds=60, session=db_session
    )
    await db_session.commit()

    # Retrieve cached response
    resp = await get_cached(key, db_session)
    assert resp is not None
    assert resp["choices"][0]["message"]["content"] == "Pong"


from llm_gateway.services.cache_service import (
    extract_prompt,
    compute_sparse_embedding,
    cosine_similarity,
    store_semantic_cache,
    get_semantic_cached,
)


def test_extract_prompt():
    # OpenAI style messages
    msgs = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Explain quantum computing in simple terms."},
    ]
    extracted = extract_prompt({"messages": msgs})
    assert "Explain quantum computing" in extracted
    assert "helpful assistant" in extracted

    # Anthropic style content blocks
    blocks = [
        {"role": "user", "content": [{"type": "text", "text": "Hello world"}]}
    ]
    assert extract_prompt(blocks) == "Hello world"

    # Raw prompt field
    assert extract_prompt({"prompt": "Single query prompt"}) == "Single query prompt"


def test_sparse_embedding_and_cosine():
    text1 = "How to install Docker on Ubuntu Linux 22.04"
    text2 = "How to install Docker on Ubuntu Linux 22.04?"
    text3 = "Recipe for chocolate chip cookies with nuts"

    vec1 = compute_sparse_embedding(text1)
    vec2 = compute_sparse_embedding(text2)
    vec3 = compute_sparse_embedding(text3)

    assert len(vec1) > 0
    # Almost identical strings should have similarity >= 0.95
    sim_high = cosine_similarity(vec1, vec2)
    assert sim_high >= 0.95

    # Completely different strings should have very low similarity
    sim_low = cosine_similarity(vec1, vec3)
    assert sim_low < 0.2


@pytest.mark.asyncio
async def test_semantic_cache_store_and_lookup(db_session):
    model = "meta-llama/Llama-3-8b"
    prompt1 = "What is the speed of light in vacuum?"
    prompt_variant = "What is the speed of light in vacuum?"
    prompt_unrelated = "Who won the World Cup in 1998?"

    resp_payload = {
        "id": "chatcmpl-sem-1",
        "choices": [{"message": {"role": "assistant", "content": "299,792,458 m/s"}}],
    }

    # Store in semantic cache
    store_semantic_cache(model, prompt1, resp_payload, ttl_seconds=300)

    # Lookup with identical / slightly varied prompt
    hit = await get_semantic_cached(model, prompt_variant, threshold=0.85, session=db_session)
    assert hit is not None
    cached_data, sim_score = hit
    assert cached_data["choices"][0]["message"]["content"] == "299,792,458 m/s"
    assert sim_score >= 0.85

    # Lookup with unrelated prompt should return None
    miss = await get_semantic_cached(model, prompt_unrelated, threshold=0.85, session=db_session)
    assert miss is None

    # Lookup with different model should return None
    wrong_model = await get_semantic_cached("gpt-4o", prompt1, threshold=0.85, session=db_session)
    assert wrong_model is None


@pytest.mark.asyncio
async def test_store_cached_with_prompt_indexes_semantic(db_session):
    model = "claude-3-5-sonnet"
    prompt = "Write a python function to compute fibonacci numbers"
    key = compute_cache_key(model, [{"role": "user", "content": prompt}], 0.0)

    resp_payload = {
        "choices": [{"message": {"role": "assistant", "content": "def fib(n): return n if n < 2 else fib(n-1) + fib(n-2)"}}]
    }

    await store_cached(
        cache_key=key,
        response=resp_payload,
        model=model,
        prompt=prompt,
        ttl_seconds=300,
        session=db_session,
    )

    # Verify retrieval via semantic query
    hit = await get_semantic_cached(model, "write a python function to compute fibonacci numbers", threshold=0.85, session=db_session)
    assert hit is not None
    assert "def fib" in hit[0]["choices"][0]["message"]["content"]
