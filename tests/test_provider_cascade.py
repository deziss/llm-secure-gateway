import pytest
from llm_gateway.services.fallback_service import rank_fallback_targets


def test_rank_fallback_targets():
    raw_targets = [
        {"backend_name": "groq", "model": "llama-3.3-70b", "priority": 3},
        {"backend_name": "anthropic", "model": "claude-3-5-sonnet", "priority": 1},
        {"backend_name": "openai", "model": "gpt-4o", "priority": 2},
    ]
    ranked = rank_fallback_targets(raw_targets)
    assert ranked[0]["backend_name"] == "anthropic"
    assert ranked[1]["backend_name"] == "openai"
    assert ranked[2]["backend_name"] == "groq"


def test_rank_fallback_targets_default_priority():
    raw_targets = [
        {"backend_name": "backup", "model": "m1"},
        {"backend_name": "primary", "model": "m2", "priority": 1},
    ]
    ranked = rank_fallback_targets(raw_targets)
    assert ranked[0]["backend_name"] == "primary"
    assert ranked[1]["backend_name"] == "backup"
