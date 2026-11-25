"""Tests for GPU/infrastructure-aware backend scoring (routing/gpu_aware.py)."""
from types import SimpleNamespace

from llm_gateway.routing.gpu_aware import score_backend, select_gpu_aware


def _backend(name, url=None, gpu_host=None, gpu_indices=None, weight=100):
    return SimpleNamespace(
        name=name,
        base_url=url or f"http://{name}:8000",
        gpu_host=gpu_host,
        gpu_indices=gpu_indices,
        weight=weight,
        fallback_urls=None,
    )


def _snap(servers=None, hosts=None):
    return {"servers": servers or [], "gpu_hosts": hosts or []}


def test_prefers_server_with_free_capacity_over_saturated_queue():
    ok = _backend("gpu-b", gpu_host="host-b")
    busy = _backend("gpu-a", gpu_host="host-a")
    snap = _snap(
        servers=[
            {"name": "gpu-a", "url": "http://gpu-a:8000", "status": "ok",
             "kv_cache_pct": 96.0, "waiting": 20, "model_stats": {}},
            {"name": "gpu-b", "url": "http://gpu-b:8000", "status": "ok",
             "kv_cache_pct": 15.0, "waiting": 2, "model_stats": {}},
        ],
        hosts=[
            {"name": "host-a", "gpus": [{"index": 0, "mem_used_mib": 76000, "mem_total_mib": 80000, "util_pct": 90, "temp_c": 70}]},
            {"name": "host-b", "gpus": [{"index": 0, "mem_used_mib": 30000, "mem_total_mib": 80000, "util_pct": 40, "temp_c": 60}]},
        ],
    )
    chosen, ranked = select_gpu_aware([busy, ok], "llama3", snap, lambda b: 1.0, lambda b: 0)
    assert chosen is ok
    assert ranked[0].backend is ok
    # KV saturation penalty is recorded in the rationale
    assert ranked[1].reasons["kv_cache_pct"] == 96.0


def test_high_util_but_free_mem_beats_low_util_no_free_mem():
    # Exactly the doc's warning: 90% util with headroom should beat 40% util with none.
    hot = _backend("hot", gpu_host="h-hot")
    full = _backend("full", gpu_host="h-full")
    snap = _snap(
        servers=[
            {"name": "hot", "url": "http://hot:8000", "status": "ok", "kv_cache_pct": 30.0, "waiting": 1, "model_stats": {}},
            {"name": "full", "url": "http://full:8000", "status": "ok", "kv_cache_pct": 30.0, "waiting": 1, "model_stats": {}},
        ],
        hosts=[
            {"name": "h-hot", "gpus": [{"index": 0, "mem_used_mib": 45000, "mem_total_mib": 80000, "util_pct": 90, "temp_c": 75}]},
            {"name": "h-full", "gpus": [{"index": 0, "mem_used_mib": 79000, "mem_total_mib": 80000, "util_pct": 40, "temp_c": 55}]},
        ],
    )
    chosen, _ = select_gpu_aware([full, hot], "llama3", snap, lambda b: 1.0, lambda b: 0)
    assert chosen is hot


def test_unhealthy_server_penalized():
    good = _backend("good")
    bad = _backend("bad")
    snap = _snap(servers=[
        {"name": "good", "url": "http://good:8000", "status": "ok", "model_stats": {}},
        {"name": "bad", "url": "http://bad:8000", "status": "unreachable", "model_stats": {}},
    ])
    sc_good = score_backend(good, "m", snap, 1.0, 0)
    sc_bad = score_backend(bad, "m", snap, 1.0, 0)
    assert sc_good.score < sc_bad.score
    assert "unhealthy" in sc_bad.reasons["reason"]


def test_falls_back_to_neutral_without_any_telemetry():
    a = _backend("a")
    b = _backend("b", weight=200)
    # Empty snapshot: score comes only from latency/neutral terms; weight decides ties.
    chosen, ranked = select_gpu_aware([a, b], "m", _snap(), lambda b_: 0.2, lambda b_: 0)
    assert chosen is b  # identical score, higher weight wins the tie
    assert all(r.reasons["status"] == "unknown" for r in ranked)


def test_per_model_kv_overrides_server_kv():
    b = _backend("s")
    snap = _snap(servers=[{
        "name": "s", "url": "http://s:8000", "status": "ok", "kv_cache_pct": 10.0,
        "model_stats": {"llama3": {"kv_cache_pct": 99.0, "waiting": 50}},
    }])
    sc = score_backend(b, "llama3", snap, 1.0, 0)
    assert sc.reasons["kv_cache_pct"] == 99.0
    assert sc.reasons["waiting"] == 50


def test_gpu_indices_pin_selected_gpus_only():
    b = _backend("s", gpu_host="h", gpu_indices=[1])
    snap = _snap(
        servers=[{"name": "s", "url": "http://s:8000", "status": "ok", "model_stats": {}}],
        hosts=[{"name": "h", "gpus": [
            {"index": 0, "mem_used_mib": 5000, "mem_total_mib": 80000, "util_pct": 10, "temp_c": 40},
            {"index": 1, "mem_used_mib": 76000, "mem_total_mib": 80000, "util_pct": 99, "temp_c": 90},
        ]}],
    )
    sc = score_backend(b, "m", snap, 1.0, 0)
    assert sc.reasons["free_vram"] == "5%"   # pinned GPU 1 only, ignores idle GPU 0
    assert sc.reasons["gpu_temp_c"] == 90
