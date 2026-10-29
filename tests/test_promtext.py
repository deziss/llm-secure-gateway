"""Prometheus text parser and per-source GPU parsers (fixture-based, no network)."""
import math

import pytest

from llm_gateway.observability.collect import gpu, promtext

VLLM_OLD = """# HELP vllm:num_requests_running Number of requests currently running.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{model_name="qwen"} 3.0
vllm:num_requests_waiting{model_name="qwen"} 2.0
vllm:gpu_cache_usage_perc{model_name="qwen"} 0.25
vllm:generation_tokens_total{model_name="qwen"} 1000.0
vllm:prompt_tokens_total{model_name="qwen"} 400.0
"""

VLLM_NEW = """vllm:num_requests_running{model_name="qwen"} 1
vllm:num_requests_waiting{model_name="qwen"} 0
vllm:kv_cache_usage_perc{model_name="qwen"} 0.5
"""

LLAMACPP = """# TYPE llamacpp:requests_processing gauge
llamacpp:requests_processing 2
llamacpp:requests_deferred 4
llamacpp:predicted_tokens_seconds 31.5
llamacpp:prompt_tokens_seconds 110.0
"""

DCGM = """# HELP DCGM_FI_DEV_GPU_UTIL GPU utilization (in %).
DCGM_FI_DEV_GPU_UTIL{gpu="0",UUID="GPU-aaa",modelName="NVIDIA A100 80GB",Hostname="gpu-a"} 81
DCGM_FI_DEV_FB_USED{gpu="0",UUID="GPU-aaa",modelName="NVIDIA A100 80GB",Hostname="gpu-a"} 20480
DCGM_FI_DEV_FB_FREE{gpu="0",UUID="GPU-aaa",modelName="NVIDIA A100 80GB",Hostname="gpu-a"} 61440
DCGM_FI_DEV_GPU_TEMP{gpu="0",UUID="GPU-aaa",modelName="NVIDIA A100 80GB",Hostname="gpu-a"} 63
DCGM_FI_DEV_POWER_USAGE{gpu="0",UUID="GPU-aaa",modelName="NVIDIA A100 80GB",Hostname="gpu-a"} 210.5
DCGM_FI_DEV_GPU_UTIL{gpu="1",UUID="GPU-bbb",modelName="NVIDIA A100 80GB",Hostname="gpu-a"} 0
DCGM_FI_DEV_FB_USED{gpu="1",UUID="GPU-bbb",modelName="NVIDIA A100 80GB",Hostname="gpu-a"} 512
DCGM_FI_DEV_FB_FREE{gpu="1",UUID="GPU-bbb",modelName="NVIDIA A100 80GB",Hostname="gpu-a"} 81408
"""

NVIDIA_EXPORTER = """# HELP nvidia_smi_utilization_gpu_ratio GPU utilization
nvidia_smi_utilization_gpu_ratio{index="0",uuid="GPU-ccc",name="RTX 4090"} 0.37
nvidia_smi_memory_used_bytes{index="0",uuid="GPU-ccc",name="RTX 4090"} 4294967296
nvidia_smi_memory_total_bytes{index="0",uuid="GPU-ccc",name="RTX 4090"} 25769803776
nvidia_smi_temperature_gpu{index="0",uuid="GPU-ccc",name="RTX 4090"} 55
nvidia_smi_power_draw_watts{index="0",uuid="GPU-ccc",name="RTX 4090"} 150.25
nvidia_smi_power_limit_watts{index="0",uuid="GPU-ccc",name="RTX 4090"} 450
"""

AMD = """gpu_gfx_activity{gpu_id="0",card_model="MI300X"} 64
gpu_used_vram{gpu_id="0",card_model="MI300X"} 16384
gpu_total_vram{gpu_id="0",card_model="MI300X"} 196608
gpu_average_socket_power{gpu_id="0",card_model="MI300X"} 512
"""


def test_parse_skips_comments_and_handles_escapes_and_specials():
    text = 'm{a="x\\"y",b="line\\nbreak"} 1\nbad line here\nn NaN\no +Inf\n'
    samples = promtext.parse(text)
    assert samples[0].labels == {"a": 'x"y', "b": "line\nbreak"} and samples[0].value == 1.0
    assert math.isnan(promtext.select(samples, "n")[0].value)
    assert promtext.select(samples, "o")[0].value == math.inf
    assert len(samples) == 3  # the malformed line is dropped, not fatal


def test_vllm_old_kv_name_is_read_when_new_name_missing():
    s = promtext.parse(VLLM_OLD)
    assert promtext.total(s, "vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc") == pytest.approx(0.25)
    assert promtext.total(s, "vllm:num_requests_running") == 3.0
    assert promtext.total(s, "vllm:num_requests_waiting") == 2.0


def test_vllm_new_kv_name_wins_and_sums_across_label_sets():
    s = promtext.parse(VLLM_NEW + 'vllm:num_requests_running{model_name="other"} 2\n')
    assert promtext.total(s, "vllm:num_requests_running") == 3.0
    assert promtext.total(s, "vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc") == 0.5


def test_llamacpp_metrics():
    s = promtext.parse(LLAMACPP)
    assert promtext.total(s, "llamacpp:requests_processing") == 2
    assert promtext.total(s, "llamacpp:requests_deferred") == 4
    assert promtext.total(s, "llamacpp:predicted_tokens_seconds") == pytest.approx(31.5)


def test_dcgm_parser_computes_total_and_keeps_uuid():
    gpus = sorted(gpu.parse_dcgm(DCGM, "gpu-a"), key=lambda g: g["index"])
    g0 = gpus[0]
    assert g0["uuid"] == "GPU-aaa" and g0["name"] == "NVIDIA A100 80GB" and g0["vendor"] == "nvidia"
    assert g0["util_pct"] == 81.0
    assert g0["mem_used_mib"] == 20480 and g0["mem_total_mib"] == 20480 + 61440
    assert g0["mem_util_pct"] == pytest.approx(20480 / 81920 * 100)
    assert g0["temp_c"] == 63 and g0["power_w"] == pytest.approx(210.5)
    assert g0["processes"] is None  # exporters have no per-process list
    assert gpus[1]["util_pct"] == 0.0


def test_nvidia_gpu_exporter_converts_ratio_and_bytes():
    g = gpu.parse_nvidia_exporter(NVIDIA_EXPORTER, "rig-1")[0]
    assert g["util_pct"] == pytest.approx(37.0)
    assert g["mem_used_mib"] == pytest.approx(4096.0)
    assert g["mem_total_mib"] == pytest.approx(24576.0)
    assert g["power_limit_w"] == 450.0 and g["name"] == "RTX 4090" and g["uuid"] == "GPU-ccc"


def test_amd_parser_uses_name_map_and_vendor():
    g = gpu.parse_amd(AMD, "mi-1")[0]
    assert g["vendor"] == "amd" and g["name"] == "MI300X"
    assert g["util_pct"] == 64 and g["mem_used_mib"] == 16384 and g["mem_total_mib"] == 196608
    assert g["power_w"] == 512 and g["processes"] is None


def test_agent_json_parses_with_nested_processes():
    doc = {"gpus": [{"index": 0, "uuid": "GPU-1", "name": "A", "vendor": "nvidia", "util_pct": 10,
                     "mem_used_mib": 100, "mem_total_mib": 1000,
                     "processes": [{"pid": 7, "name": "w", "used_mib": 90, "model_hint": "m", "container_id": "abc"}]}]}
    g = gpu.parse_agent(doc, "h")[0]
    assert g["host"] == "h" and g["mem_util_pct"] == pytest.approx(10.0)
    assert g["processes"] == [{"pid": 7, "name": "w", "used_mib": 90.0, "model_hint": "m",
                               "container_id": "abc", "server": None}]


def _host(name, gpus):
    return {"name": name, "gpus": gpus}


def test_attribution_pinned_process_host_and_none():
    procs0 = [{"pid": 1, "name": "w", "used_mib": 1, "model_hint": "qwen2.5-7b", "server": "vllm-a"}]
    gpus = [
        gpu._gpu("gpu-a", 0, vendor="nvidia", processes=procs0),
        gpu._gpu("gpu-a", 1, vendor="nvidia", processes=[]),
    ]
    hosts = [_host("gpu-a", gpus)]
    targets = [{"name": "vllm-a", "models": ["Qwen2.5:7B"], "gpu_host": "gpu-a"}]
    gpu.attribute_processes(targets, hosts)
    assert procs0[0]["server"] == "vllm-a"

    by_proc, how = gpu.server_gpus({"name": "vllm-a", "gpu_host": "gpu-a", "gpu_indices": None}, hosts)
    assert how == "process" and [g["index"] for g in by_proc] == [0]

    pinned, how = gpu.server_gpus({"name": "vllm-a", "gpu_host": "gpu-a", "gpu_indices": [1]}, hosts)
    assert how == "pinned" and [g["index"] for g in pinned] == [1]

    everything, how = gpu.server_gpus({"name": "other", "gpu_host": "gpu-a", "gpu_indices": None}, hosts)
    assert how == "host" and len(everything) == 2

    assert gpu.server_gpus({"name": "x", "gpu_host": None}, hosts) == ([], "none")
    assert gpu.server_gpus({"name": "x", "gpu_host": "missing"}, hosts) == ([], "none")
