import pytest
from llm_gateway.routing.complexity import (
    classify_complexity,
    ComplexityTier,
    resolve_complexity_model,
)

def test_classify_simple_prompt():
    messages = [{"role": "user", "content": "Hello, how are you today?"}]
    tier = classify_complexity(messages)
    assert tier == ComplexityTier.TIER_1_FAST

def test_classify_standard_prompt():
    messages = [
        {"role": "user", "content": "Write a 3-paragraph summary of photosynthesis."},
        {"role": "assistant", "content": "Photosynthesis is the process..."},
        {"role": "user", "content": "Now explain the light-independent Calvin cycle."},
    ]
    tier = classify_complexity(messages)
    assert tier == ComplexityTier.TIER_2_STANDARD

def test_classify_reasoning_prompt():
    messages = [
        {"role": "user", "content": "Prove that the square root of 2 is irrational step by step with mathematical rigor."}
    ]
    tier = classify_complexity(messages)
    assert tier == ComplexityTier.TIER_3_REASONING

    code_msg = [
        {"role": "user", "content": "```python\ndef solve_tsp(graph):\n    pass\n``` Optimize this dynamic programming solution."}
    ]
    assert classify_complexity(code_msg) == ComplexityTier.TIER_3_REASONING

def test_resolve_model_mapping():
    assert resolve_complexity_model(ComplexityTier.TIER_1_FAST) in ("claude-3-haiku", "gpt-4o-mini", "llama-3.2-3b")
    assert resolve_complexity_model(ComplexityTier.TIER_3_REASONING) in ("claude-3-5-sonnet", "o1", "deepseek-r1", "gpt-4o")

def test_resolve_model_mapping_custom():
    custom = {
        ComplexityTier.TIER_1_FAST: "groq/llama-3.2-3b",
        ComplexityTier.TIER_3_REASONING: "openai/o1-mini",
    }
    assert resolve_complexity_model(ComplexityTier.TIER_1_FAST, custom) == "groq/llama-3.2-3b"
    assert resolve_complexity_model(ComplexityTier.TIER_3_REASONING, custom) == "openai/o1-mini"

def test_classify_empty_or_none():
    assert classify_complexity(None) == ComplexityTier.TIER_1_FAST
    assert classify_complexity([]) == ComplexityTier.TIER_1_FAST

def test_classify_list_content():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Can you prove Fermat's Last Theorem?"}
            ],
        }
    ]
    assert classify_complexity(messages) == ComplexityTier.TIER_3_REASONING

def test_classify_large_context():
    # Long text exceeding 2500 tokens (est 10,000 chars)
    long_text = "word " * 2600
    messages = [{"role": "user", "content": long_text}]
    assert classify_complexity(messages) == ComplexityTier.TIER_3_REASONING


@pytest.mark.asyncio
async def test_get_target_backend_auto_routing():
    from unittest.mock import AsyncMock, MagicMock
    from llm_gateway.proxy_helpers import get_target_backend
    from llm_gateway.models import LLMBackend, BackendType
    from llm_gateway.services import ConfigService

    b_fast = LLMBackend(name="b-fast", base_url="http://fast", backend_type=BackendType.OPENAI, models=["gpt-4o-mini"])
    b_std = LLMBackend(name="b-std", base_url="http://std", backend_type=BackendType.OPENAI, models=["gpt-4o"])
    b_reason = LLMBackend(name="b-reason", base_url="http://reason", backend_type=BackendType.ANTHROPIC, models=["claude-3-5-sonnet"])

    service = MagicMock(spec=ConfigService)
    service.list_backends = AsyncMock(return_value=[b_fast, b_std, b_reason])
    service.get_setting = AsyncMock(return_value="false")
    session = AsyncMock()

    # Fast prompt via "auto"
    fast_msgs = [{"role": "user", "content": "Hello!"}]
    backend = await get_target_backend("auto", service, session, messages=fast_msgs)
    assert backend.name == "b-fast"

    # Reasoning prompt via "smart-route"
    reason_msgs = [{"role": "user", "content": "Prove that the square root of 2 is irrational step by step."}]
    backend = await get_target_backend("smart-route", service, session, messages=reason_msgs)
    assert backend.name == "b-reason"

    # Standard prompt via "auto-router"
    std_msgs = [
        {"role": "user", "content": "Summarize the history of Rome in three paragraphs."},
        {"role": "assistant", "content": "Rome was founded..."},
        {"role": "user", "content": "Tell me more about the Punic Wars."},
    ]
    backend = await get_target_backend("auto-router", service, session, messages=std_msgs)
    assert backend.name == "b-std"

