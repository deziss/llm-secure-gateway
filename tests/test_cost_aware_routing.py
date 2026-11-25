"""Tests for cost-aware backend scoring (routing/cost_aware.py)."""
from types import SimpleNamespace

from llm_gateway.routing.cost_aware import (
    LOCAL_ENGINE_TYPES,
    rank,
    score_backend,
    select_cost_aware,
)


def _backend(name, provider="openai", url=None, weight=100):
    return SimpleNamespace(
        name=name,
        base_url=url or f"http://{name}:8000",
        backend_type=SimpleNamespace(value=provider),
        weight=weight,
        fallback_urls=None,
    )


NO_COSTS = {}  # pricing map without the model -> unknown price


def _lat(v):
    return lambda b: v


def test_cheapest_priced_backend_wins():
    cheap = _backend("cheap", "openai")
    pricey = _backend("pricey", "anthropic")
    pricing = {("llama3", "openai"): (1.0, 4.0), ("llama3", "anthropic"): (3.0, 15.0)}
    chosen, ranked = select_cost_aware(
        [pricey, cheap], "llama3", pricing, _lat(1.0), lambda b: 0, 1000.0, 500.0)
    assert chosen is cheap
    assert ranked[0].backend is cheap and ranked[1].backend is pricey
    assert ranked[0].estimated_cost_usd < ranked[1].estimated_cost_usd


def test_same_pricing_breaks_on_latency():
    a = _backend("fast")
    b = _backend("slow")
    pricing = {("m", "*"): (2.0, 2.0)}
    chosen, _ = select_cost_aware([b, a], "m", pricing, lambda b_: 0.2 if b_.name == "fast" else 4.0,
                                  lambda b_: 0, 1000.0, 500.0)
    assert chosen is a


def test_unpriced_backend_is_neutral_not_free():
    priced = _backend("priced", "openai")
    mystery = _backend("mystery", "acme-cloud")
    pricing = {("m", "openai"): (5.0, 10.0)}
    _, ranked = select_cost_aware([mystery, priced], "m", pricing, _lat(0.0), lambda b: 0,
                                  1000.0, 500.0)
    by_name = {c.backend.name: c for c in ranked}
    assert by_name["mystery"].estimated_cost_usd is None
    assert by_name["mystery"].reasons["pricing"] == "unknown"
    assert by_name["priced"].reasons["pricing"] == "exact"


def test_local_engine_counts_as_free():
    assert "vllm" in LOCAL_ENGINE_TYPES and "ollama" in LOCAL_ENGINE_TYPES
    local = _backend("local", "vllm")
    cloud = _backend("cloud", "openai")
    pricing = {("m", "openai"): (5.0, 20.0)}
    chosen, ranked = select_cost_aware([cloud, local], "m", pricing, _lat(0.5), lambda b: 0,
                                       4000.0, 2000.0)
    assert chosen is local
    assert ranked[0].estimated_cost_usd == 0.0


def test_unhealthy_backend_penalised_even_when_free():
    down = _backend("down", "vllm")
    up = _backend("up", "openai")
    pricing = {("m", "*"): (1.0, 1.0)}
    chosen, _ = select_cost_aware([down, up], "m", pricing, _lat(0.5), lambda b: 0,
                                  1000.0, 500.0, statuses={"down": "down"})
    assert chosen is up


def test_all_unpriced_yields_unknown_costs():
    a, b = _backend("a", "acme"), _backend("b", "other")
    ranked = rank([a, b], "m", {}, _lat(1.0), lambda b_: 0, 100.0, 100.0)
    assert all(c.estimated_cost_usd is None for c in ranked)


def test_cost_estimate_uses_rate_per_million():
    b = _backend("x")
    cs = score_backend(b, "m", {("m", "*"): (2.5, 8.0)}, 0.0, 0, 1_000_000.0, 500_000.0)
    assert cs.reasons["pricing"] == "exact"
    # 1M in * 2.5 + 0.5M out * 8.0 = 2.5 + 4.0
    assert abs(cs.estimated_cost_usd - 6.5) < 1e-9
