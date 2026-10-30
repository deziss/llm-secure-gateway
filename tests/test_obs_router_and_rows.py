"""vLLM production-stack router scraping, configured-model rows and per-model GPU attribution."""
from llm_gateway.observability.collect import promtext, servers
from llm_gateway.observability.collect.gpu import attribute_processes, model_gpus
from llm_gateway.observability.api import build_groups, with_server_rows

ROUTER_METRICS = """\
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{server="http://llama31.internal:8000"} 1.0
vllm:num_requests_running{server="http://qwen25.internal:8000"} 7.0
vllm:num_requests_running{server="http://other.internal:8000"} 2.0
vllm:avg_itl{server="http://llama31.internal:8000"} 0.025
vllm:avg_itl{server="http://qwen25.internal:8000"} -1
vllm:output_tokens_total{server="http://llama31.internal:8000"} OUT
vllm:healthy_pods_total{server="http://llama31.internal:8000"} 1.0
vllm:num_incoming_requests_total{model="llama-3.1-8b"} INC
"""


def _router(out, inc):
    return promtext.parse(ROUTER_METRICS.replace("OUT", str(out)).replace("INC", str(inc)))


def test_engine_model_matching():
    models = ["llama-3.1-8b", "qwen-2.5-14b", "qwen-3-30b-a3b", "qwen3.8-flash-next"]
    assert servers.match_engine_model("http://llama31.internal:8000", models) == "llama-3.1-8b"
    assert servers.match_engine_model("http://qwen25.internal:8000", models) == "qwen-2.5-14b"
    assert servers.match_engine_model("http://qwen3.internal:8000", models) is None  # ambiguous
    assert servers.match_engine_model("http://nothing.internal:8000", models) is None


def test_router_scrape_rates_itl_and_leftover_pairing():
    servers._prev_series.clear()
    target = {"name": "r1", "models": ["llama-3.1-8b", "qwen-2.5-14b", "mystery-model"]}
    assert servers._is_router(_router(100, 10))
    first = servers._scrape_router(target, _router(100, 10), now=1000.0)
    assert first["model_stats"]["llama-3.1-8b"]["out_tok_s"] is None  # no previous sample yet
    second = servers._scrape_router(target, _router(300, 30), now=1010.0)
    llama = second["model_stats"]["llama-3.1-8b"]
    assert llama["out_tok_s"] == 20.0 and llama["rps"] == 2.0
    assert llama["itl_ms"] == 25.0
    qwen = second["model_stats"]["qwen-2.5-14b"]
    assert qwen["running"] == 7 and qwen["itl_ms"] is None  # -1 means "no data"
    # one engine and one model left unmatched are paired
    assert second["model_stats"]["mystery-model"]["running"] == 2
    assert second["running"] == 10


def test_every_configured_model_gets_a_row():
    srv = [{"name": "r1", "server_type": "vllm", "status": "ok", "models": ["a", "b"],
            "model_stats": {"a": {"rps": 1.5, "running": 3, "waiting": None, "out_tok_s": 42.0,
                                  "itl_ms": 20.0, "kv_cache_pct": None, "engines": 1}}}]
    gateway = [{"server": "r1", "model": "b", "rps_10s": 0.5, "rps_60s": 0.4, "waiting": 1}]
    rows = {r["model"]: r for r in with_server_rows(gateway, srv)}
    assert set(rows) == {"a", "b"}
    assert rows["a"]["gateway_traffic"] is False and rows["a"]["server_tok_s"] == 42.0
    assert rows["b"]["gateway_traffic"] is True and rows["b"]["rps_10s"] == 0.5


def test_groups_merge_spellings_and_prefer_server_rates():
    srv = [{"name": "x", "models": ["Qwen3-30B-A3B"], "model_stats": {}},
           {"name": "y", "models": ["qwen-3-30b-a3b"], "model_stats": {}}]
    rows = with_server_rows([], srv)
    rows[0]["server_tok_s"], rows[0]["server_scope"] = 10.0, "model"
    _, groups = build_groups(rows, srv, [], {})
    assert len(groups) == 1 and groups[0]["totals"]["instance_count"] == 2
    assert groups[0]["totals"]["tok_s"] == 10.0


def test_model_gpus_per_model_and_hidden_model():
    host = {"name": "h", "gpus": [
        {"host": "h", "index": 0, "processes": [{"pid": 1, "model_hint": "qwen-3-30b-a3b"}]},
        {"host": "h", "index": 1, "processes": [{"pid": 2, "model_hint": "bge-m3"}]},
    ]}
    target = {"name": "s", "gpu_host": "h", "models": ["Qwen3-30B-A3B", "bge-m3", "elsewhere"]}
    attribute_processes([target], [host])
    srv = {"name": "s", "gpu_host": "h", "gpu_indices": None}
    gpus, how = model_gpus(srv, "Qwen3-30B-A3B", [host])
    assert how == "process" and [g["index"] for g in gpus] == [0]
    assert model_gpus(srv, "elsewhere", [host]) == ([], "none")
