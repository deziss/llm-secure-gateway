"""Tests for Queue-Aware & Least-Busy Dynamic Load Balancing in get_target_backend."""
import logging
import pytest
from unittest.mock import MagicMock, AsyncMock

from llm_gateway.models import LLMBackend, BackendType
from llm_gateway.services import ConfigService
from llm_gateway.proxy_helpers import (
    get_target_backend,
    get_backend_in_flight,
    record_backend_latency,
    _backend_latency,
)
from llm_gateway.observability.collect.requests import get_live_stats


@pytest.fixture(autouse=True)
def clean_state():
    """Reset latency history and live stats before and after each test."""
    _backend_latency.clear()
    store = get_live_stats()
    if hasattr(store, "_keys") and isinstance(store._keys, dict):
        store._keys.clear()
    yield
    _backend_latency.clear()
    if hasattr(store, "_keys") and isinstance(store._keys, dict):
        store._keys.clear()


def make_backend(name: str, base_url: str, models: list[str], weight: int = 100, backend_type: BackendType = BackendType.OPENAI) -> LLMBackend:
    return LLMBackend(
        name=name,
        base_url=base_url,
        backend_type=backend_type,
        models=models,
        weight=weight,
    )


def make_service_and_session(backends: list[LLMBackend], enable_experimental: str = "true", routing_strategy: str = ""):
    service = MagicMock(spec=ConfigService)
    session = AsyncMock()

    service.list_backends = AsyncMock(return_value=backends)

    async def mock_get_setting(_session, key, default=""):
        if key == "ENABLE_EXPERIMENTAL_ROUTING":
            return enable_experimental
        if key == "ROUTING_STRATEGY":
            return routing_strategy
        return default

    service.get_setting = AsyncMock(side_effect=mock_get_setting)
    return service, session


@pytest.mark.asyncio
async def test_least_busy_selects_idle_backend_over_loaded():
    """2 candidate backends for the same model, one with high in-flight load (5) and one with 0: router selects idle backend."""
    b1 = make_backend("backend-busy", "http://busy:8000", ["gpt-4o"], weight=100)
    b2 = make_backend("backend-idle", "http://idle:8000", ["gpt-4o"], weight=100)
    service, session = make_service_and_session([b1, b2], enable_experimental="true")

    store = get_live_stats()
    for _ in range(5):
        store.dispatch((b1.name, "openai", "gpt-4o"))

    assert get_backend_in_flight(store, b1.name, b1.base_url) == 5
    assert get_backend_in_flight(store, b2.name, b2.base_url) == 0

    selected = await get_target_backend("gpt-4o", service, session)
    assert selected.name == "backend-idle"


@pytest.mark.asyncio
async def test_least_busy_weight_adjustment_concurrency_threshold():
    """Weight adjustment: higher weight backend takes higher concurrency before shifting traffic to lower weight backend."""
    # b_heavy has 2x capacity of b_light
    b_heavy = make_backend("node-heavy", "http://heavy:8000", ["llama-3.1-8b"], weight=200, backend_type=BackendType.VLLM)
    b_light = make_backend("node-light", "http://light:8000", ["llama-3.1-8b"], weight=100, backend_type=BackendType.VLLM)
    service, session = make_service_and_session([b_heavy, b_light], enable_experimental="true")
    store = get_live_stats()

    # Step 1: Both idle (in_flight=0). Score: heavy=1/200=0.005, light=1/100=0.010 -> heavy selected
    selected = await get_target_backend("llama-3.1-8b", service, session)
    assert selected.name == "node-heavy"

    # Step 2: Heavy takes 1 in-flight request.
    # Scores: heavy=(1+1)/200=0.010, light=(0+1)/100=0.010 -> tied!
    # Tie-breaker favors higher capacity (weight 200 > 100), so heavy is still chosen.
    store.dispatch((b_heavy.name, "vllm", "llama-3.1-8b"))
    selected = await get_target_backend("llama-3.1-8b", service, session)
    assert selected.name == "node-heavy"

    # Step 3: Heavy takes second in-flight request (2 in flight).
    # Scores: heavy=(2+1)/200=0.015, light=(0+1)/100=0.010.
    # Now light has lower score (0.010 < 0.015) -> traffic shifts to light!
    store.dispatch((b_heavy.name, "vllm", "llama-3.1-8b"))
    selected = await get_target_backend("llama-3.1-8b", service, session)
    assert selected.name == "node-light"


@pytest.mark.asyncio
async def test_least_busy_latency_penalty_favors_faster_backend():
    """Latency penalty: slow backend has higher score, favoring faster backend."""
    b_fast = make_backend("cluster-fast", "http://fast:8000", ["claude-3-5-sonnet"], weight=100)
    b_slow = make_backend("cluster-slow", "http://slow:8000", ["claude-3-5-sonnet"], weight=100)
    service, session = make_service_and_session([b_fast, b_slow], enable_experimental="true")

    # Fast: avg latency 50ms, Slow: avg latency 2500ms
    record_backend_latency(b_fast.base_url, 0.05)
    record_backend_latency(b_slow.base_url, 2.50)

    # Both idle: fast score = 1 / (100 * 0.952) = 0.0105; slow score = 1 / (100 * 0.2857) = 0.0350
    selected = await get_target_backend("claude-3-5-sonnet", service, session)
    assert selected.name == "cluster-fast"

    # Even with 1 in-flight request on fast, fast score = 2 / 95.2 = 0.0210 < 0.0350 (slow)
    store = get_live_stats()
    store.dispatch((b_fast.name, "openai", "claude-3-5-sonnet"))
    selected = await get_target_backend("claude-3-5-sonnet", service, session)
    assert selected.name == "cluster-fast"


@pytest.mark.asyncio
async def test_least_busy_single_backend():
    """Single backend: selects it without error."""
    b_solo = make_backend("solo-node", "http://solo:8000", ["mistral-large"], weight=100)
    service, session = make_service_and_session([b_solo], enable_experimental="true")

    selected = await get_target_backend("mistral-large", service, session)
    assert selected.name == "solo-node"


@pytest.mark.asyncio
async def test_least_busy_no_candidate_falls_back_gracefully():
    """No candidate strictly hosts model: falls back gracefully to default backend without error."""
    b1 = make_backend("backend-openai", "http://b1:8000", ["gpt-4o"], weight=100)
    b2 = make_backend("backend-anthropic", "http://b2:8000", ["claude-3"], weight=100)
    service, session = make_service_and_session([b1, b2], enable_experimental="true")

    # Request a model none of the backends explicitly host
    selected = await get_target_backend("non-existent-model", service, session)
    assert selected is not None
    assert selected.name == "backend-openai"  # First backend fallback


@pytest.mark.asyncio
async def test_least_busy_logging_output(caplog):
    """Routing decision is logged at INFO level with in_flight, weight, avg_lat, and model."""
    b1 = make_backend("logged-node", "http://logged:8000", ["qwen-2.5"], weight=150)
    service, session = make_service_and_session([b1], enable_experimental="true")
    record_backend_latency(b1.base_url, 0.123)

    with caplog.at_level(logging.INFO):
        selected = await get_target_backend("qwen-2.5", service, session)

    assert selected.name == "logged-node"
    # Check log message
    expected_log = "Least-busy routing selected backend 'logged-node' (in_flight=0, weight=150, avg_lat=0.123s) for model 'qwen-2.5'"
    assert expected_log in caplog.text


@pytest.mark.asyncio
async def test_least_busy_in_flight_finish_rebalances():
    """Completing requests decrements in-flight count and rebalances traffic."""
    b1 = make_backend("worker-1", "http://w1:8000", ["llama-3"], weight=100)
    b2 = make_backend("worker-2", "http://w2:8000", ["llama-3"], weight=100)
    service, session = make_service_and_session([b1, b2], enable_experimental="true")

    store = get_live_stats()
    key1 = (b1.name, "openai", "llama-3")
    store.dispatch(key1)
    store.dispatch(key1)

    # Worker 1 has 2 in flight, worker 2 has 0 -> router picks worker 2
    selected = await get_target_backend("llama-3", service, session)
    assert selected.name == "worker-2"

    # Worker 1 finishes its requests
    store.finish(key1, was_generating=False, success=True)
    store.finish(key1, was_generating=False, success=True)
    assert get_backend_in_flight(store, b1.name, b1.base_url) == 0

    # Worker 2 now takes 2 requests
    key2 = (b2.name, "openai", "llama-3")
    store.dispatch(key2)
    store.dispatch(key2)

    # Router now picks worker 1
    selected = await get_target_backend("llama-3", service, session)
    assert selected.name == "worker-1"


@pytest.mark.asyncio
async def test_least_busy_tracks_by_base_url():
    """In-flight requests tracked by base URL are recognized."""
    b1 = make_backend("named-backend", "http://url-tracker:8000", ["deepseek-r1"], weight=100)
    b2 = make_backend("other-backend", "http://other:8000", ["deepseek-r1"], weight=100)
    service, session = make_service_and_session([b1, b2], enable_experimental="true")

    store = get_live_stats()
    # Dispatch under base_url instead of backend.name
    store.dispatch((b1.base_url, "openai", "deepseek-r1"))
    store.dispatch((b1.base_url, "openai", "deepseek-r1"))

    assert get_backend_in_flight(store, b1.name, b1.base_url) == 2
    selected = await get_target_backend("deepseek-r1", service, session)
    assert selected.name == "other-backend"


@pytest.mark.asyncio
async def test_backward_compatibility_static_routing_when_disabled():
    """When ENABLE_EXPERIMENTAL_ROUTING is false, static routing returns first backend despite load."""
    b1 = make_backend("backend-static-1", "http://s1:8000", ["gpt-4"], weight=100)
    b2 = make_backend("backend-static-2", "http://s2:8000", ["gpt-4"], weight=100)
    service, session = make_service_and_session([b1, b2], enable_experimental="false")

    store = get_live_stats()
    for _ in range(10):
        store.dispatch((b1.name, "openai", "gpt-4"))

    # Despite b1 having 10 in-flight requests, static routing always picks the first candidate
    selected = await get_target_backend("gpt-4", service, session)
    assert selected.name == "backend-static-1"


@pytest.mark.asyncio
async def test_least_busy_routing_strategy_setting():
    """Routing can be explicitly activated via ROUTING_STRATEGY=least_busy even if ENABLE_EXPERIMENTAL_ROUTING is false."""
    b1 = make_backend("worker-a", "http://wa:8000", ["gemini-pro"], weight=100)
    b2 = make_backend("worker-b", "http://wb:8000", ["gemini-pro"], weight=100)
    service, session = make_service_and_session([b1, b2], enable_experimental="false", routing_strategy="least_busy")

    store = get_live_stats()
    for _ in range(3):
        store.dispatch((b1.name, "openai", "gemini-pro"))

    selected = await get_target_backend("gemini-pro", service, session)
    assert selected.name == "worker-b"


@pytest.mark.asyncio
async def test_least_busy_explicit_strategy_parameter():
    """Passing strategy='least_busy' directly to get_target_backend overrides settings."""
    b1 = make_backend("node-x", "http://nx:8000", ["llama-3"], weight=100)
    b2 = make_backend("node-y", "http://ny:8000", ["llama-3"], weight=100)
    service, session = make_service_and_session([b1, b2], enable_experimental="false", routing_strategy="")

    store = get_live_stats()
    for _ in range(4):
        store.dispatch((b1.name, "openai", "llama-3"))

    selected = await get_target_backend("llama-3", service, session, strategy="least_busy")
    assert selected.name == "node-y"
