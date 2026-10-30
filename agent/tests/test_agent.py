"""Agent tests with fake NVML / amdsmi modules and fake process table. No GPU needed."""
import json
import os
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from llm_gpu_agent import app, procs
from llm_gpu_agent.collect import MIB, AmdCollector, NvidiaCollector

MIB_B = MIB


# ----- fakes ---------------------------------------------------------------- #
class FakeNVML:
    def __init__(self, procs_by_gpu=None):
        self._procs = procs_by_gpu or {}
        self.shutdown_called = False

    def nvmlInit(self):
        pass

    def nvmlShutdown(self):
        self.shutdown_called = True

    def nvmlDeviceGetCount(self):
        return 2

    def nvmlDeviceGetHandleByIndex(self, i):
        return i

    def nvmlDeviceGetUUID(self, h):
        return f"GPU-{h}".encode()  # older pynvml returns bytes

    def nvmlDeviceGetName(self, h):
        return "NVIDIA A100"

    def nvmlDeviceGetUtilizationRates(self, h):
        return SimpleNamespace(gpu=40 + h)

    def nvmlDeviceGetMemoryInfo(self, h):
        return SimpleNamespace(used=(1000 + h) * MIB_B, total=80 * 1024 * MIB_B)

    def nvmlDeviceGetTemperature(self, h, sensor):
        return 61

    def nvmlDeviceGetPowerUsage(self, h):
        return 250_000  # mW

    def nvmlDeviceGetEnforcedPowerLimit(self, h):
        return 400_000

    def nvmlDeviceGetComputeRunningProcesses(self, h):
        if h == 1:
            raise RuntimeError("not supported on this driver")
        return self._procs.get(h, [])


class FakeAmdsmi:
    def amdsmi_init(self):
        pass

    def amdsmi_get_processor_handles(self):
        return ["h0"]

    def amdsmi_get_gpu_activity(self, h):
        return {"gfx_activity": 77}

    def amdsmi_get_gpu_vram_usage(self, h):
        return {"vram_total": 65536, "vram_used": 12288}

    def amdsmi_get_power_info(self, h):
        return {"average_socket_power": 180}

    def amdsmi_get_gpu_device_uuid(self, h):
        return "UUID-AMD-0"

    def amdsmi_get_gpu_asic_info(self, h):
        return {"market_name": "MI300X"}


class FakeProc:
    def __init__(self, pid, argv, ppid, name="proc"):
        self._argv, self._ppid, self._name, self.pid = argv, ppid, name, pid

    def cmdline(self):
        return self._argv

    def ppid(self):
        return self._ppid

    def name(self):
        return self._name


def fake_table(table):
    def lookup(pid):
        if pid not in table:
            raise procs.psutil.NoSuchProcess(pid)
        return table[pid]
    return lookup


# ----- model hints ---------------------------------------------------------- #
@pytest.mark.parametrize("argv,expected", [
    (["python", "/usr/bin/vllm", "serve", "Qwen/Qwen2.5-7B", "--port", "8000"], "Qwen/Qwen2.5-7B"),
    (["vllm", "serve", "--port", "8000"], None),
    (["vllm", "serve", "meta/llama", "--served-model-name", "llama-3,alias"], "llama-3"),
    (["llama-server", "-m", "/models/qwen2.5-7b-q4.gguf", "--port", "8080"], "qwen2.5-7b-q4"),
    (["llama-server", "--model=/m/x.gguf", "--alias", "chat"], "chat"),
    (["ollama", "runner", "--model", "/root/.ollama/models/blobs/sha256-abc123"], None),
    (["sshd"], None),
    ([], None),
])
def test_model_hint(argv, expected):
    assert procs.model_hint(argv) == expected


def test_resolve_hint_walks_parents_to_vllm_api_process():
    table = {
        500: FakeProc(500, ["python", "-c", "from multiprocessing import spawn"], ppid=400),
        400: FakeProc(400, ["/usr/bin/python3", "/usr/local/bin/vllm", "serve", "Qwen/Qwen2.5-7B"], ppid=1),
    }
    assert procs.resolve_hint(500, lookup=fake_table(table)) == "Qwen/Qwen2.5-7B"


def test_resolve_hint_stops_at_depth_and_handles_missing_pid():
    table = {
        10: FakeProc(10, ["a"], ppid=9),
        9: FakeProc(9, ["b"], ppid=8),
        8: FakeProc(8, ["c"], ppid=7),
        7: FakeProc(7, ["d"], ppid=6),
        6: FakeProc(6, ["vllm", "serve", "deep"], ppid=1),  # beyond MAX_PARENT_DEPTH
    }
    assert procs.resolve_hint(10, lookup=fake_table(table)) is None
    assert procs.resolve_hint(999, lookup=fake_table(table)) is None


def test_container_id_from_cgroup(tmp_path):
    cid = "a" * 64
    (tmp_path / "42").mkdir()
    (tmp_path / "42" / "cgroup").write_text(f"0::/system.slice/docker-{cid}.scope\n")
    assert procs.container_id(42, proc_root=str(tmp_path)) == "a" * 12
    assert procs.container_id(43, proc_root=str(tmp_path)) is None


# ----- collectors ----------------------------------------------------------- #
def test_nvidia_collector_maps_fields_and_survives_one_bad_gpu():
    fake = FakeNVML({0: [SimpleNamespace(pid=321, usedGpuMemory=2048 * MIB_B)]})
    c = NvidiaCollector(nvml=fake)
    gpus = c.sample()
    assert [g["index"] for g in gpus] == [0, 1]  # gpu 1 fails in processes, still reported
    g0 = gpus[0]
    assert g0["uuid"] == "GPU-0" and g0["name"] == "NVIDIA A100" and g0["vendor"] == "nvidia"
    assert g0["util_pct"] == 40.0
    assert g0["mem_used_mib"] == pytest.approx(1000.0)
    assert g0["mem_total_mib"] == pytest.approx(80 * 1024)
    assert g0["power_w"] == pytest.approx(250.0) and g0["power_limit_w"] == pytest.approx(400.0)
    assert g0["processes"] == [{"pid": 321, "used_mib": pytest.approx(2048.0)}]
    c.close()
    assert fake.shutdown_called


def test_nvidia_unknown_process_memory_is_none():
    fake = FakeNVML({0: [SimpleNamespace(pid=5, usedGpuMemory=2**64 - 1)]})
    assert NvidiaCollector(nvml=fake).sample()[0]["processes"] == [{"pid": 5, "used_mib": None}]


def test_amdsmi_collector_maps_fields():
    c = AmdCollector(amdsmi=FakeAmdsmi())
    gpus = c.sample()
    assert gpus[0]["vendor"] == "amd" and gpus[0]["util_pct"] == 77
    assert gpus[0]["mem_used_mib"] == 12288.0 and gpus[0]["mem_total_mib"] == 65536.0
    assert gpus[0]["name"] == "MI300X"


def test_rocm_smi_fallback_parses_json(tmp_path, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "amdsmi", None)  # make `import amdsmi` fail
    script = tmp_path / "rocm-smi"
    payload = {"card0": {"GPU use (%)": "12", "VRAM Total Memory (B)": str(64 * MIB_B),
                         "VRAM Total Used Memory (B)": str(8 * MIB_B)}, "system": {"x": "y"}}
    script.write_text("#!/bin/sh\ncat <<'EOF'\n" + json.dumps(payload) + "\nEOF\n")
    script.chmod(0o755)
    c = AmdCollector(rocm_smi_path=str(script))
    assert c._amdsmi is None and c.available
    gpus = c.sample()
    assert gpus[0]["index"] == 0 and gpus[0]["util_pct"] == 12.0
    assert gpus[0]["mem_total_mib"] == 64.0 and gpus[0]["mem_used_mib"] == 8.0


# ----- snapshot + metrics ---------------------------------------------------- #
def test_collect_once_enriches_processes(monkeypatch):
    fake = FakeNVML({0: [SimpleNamespace(pid=321, usedGpuMemory=MIB_B * 512)]})
    table = {321: FakeProc(321, ["vllm", "serve", "Qwen/Qwen2.5-7B"], ppid=1, name="VLLM::Worker")}
    doc = app.collect_once(NvidiaCollector(nvml=fake), None, "gpu-a", lookup=fake_table(table), proc_root="/nonexistent")
    p = doc["gpus"][0]["processes"][0]
    assert p["name"] == "VLLM::Worker" and p["model_hint"] == "Qwen/Qwen2.5-7B"
    assert p["used_mib"] == pytest.approx(512.0) and p["container_id"] is None
    assert doc["host"] == "gpu-a"


def test_metrics_text_names_labels_and_escaping():
    doc = {"host": 'h"1', "gpus": [{"index": 0, "uuid": "GPU-x", "name": "A100", "vendor": "nvidia",
                                    "util_pct": 81.0, "mem_used_mib": 1.0, "mem_total_mib": 2.0,
                                    "temp_c": 60, "power_w": 200.0,
                                    "processes": [{"pid": 7, "name": "w", "used_mib": 1.0, "model_hint": "m"}]}]}
    text = app.metrics_text(doc)
    assert 'llm_gpu_utilization_ratio{host="h\\"1",gpu="0",uuid="GPU-x",name="A100",vendor="nvidia"} 0.81' in text
    assert "llm_gpu_memory_used_bytes{" in text and "llm_gpu_process_memory_bytes{" in text
    assert "llm_gpu_temperature_celsius{" in text and "llm_gpu_power_watts{" in text


# ----- HTTP: token required ------------------------------------------------- #
@pytest.fixture
def running_server():
    state = app.AgentState("s3cret-token", "gpu-test")
    state.set({"host": "gpu-test", "gpus": [{"index": 0, "vendor": "nvidia", "util_pct": 5.0,
                                             "processes": []}]})
    srv = app.make_server("127.0.0.1", 0, state)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _get(url, token=None):
    req = urllib.request.Request(url)
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_gpu_endpoint_requires_token(running_server):
    assert _get(running_server + "/v1/gpu")[0] == 401
    assert _get(running_server + "/v1/gpu", token="wrong")[0] == 401
    code, body = _get(running_server + "/v1/gpu", token="s3cret-token")
    assert code == 200 and json.loads(body)["host"] == "gpu-test"


def test_metrics_requires_token_healthz_does_not(running_server):
    assert _get(running_server + "/metrics")[0] == 401
    assert _get(running_server + "/metrics", token="s3cret-token")[0] == 200
    assert _get(running_server + "/healthz") == (200, b'{"ok": true}')
    assert _get(running_server + "/nope", token="s3cret-token")[0] == 404


def test_main_refuses_to_start_without_token(monkeypatch, capsys):
    monkeypatch.delenv("GPU_AGENT_TOKEN", raising=False)
    monkeypatch.setattr(app, "build_collectors", lambda *a, **k: (None, None))
    assert app.main(["--no-nvidia", "--no-amd"]) == 2
    assert "GPU_AGENT_TOKEN" in capsys.readouterr().err


def test_python_module_launch_is_not_a_model_hint():
    from llm_gpu_agent.procs import model_hint
    assert model_hint(["python3", "-m", "uvicorn", "app:app", "--port", "8000"]) is None
    assert model_hint(["/usr/bin/python3.12", "-m", "vllm.entrypoints.openai.api_server",
                       "--model", "/models/Qwen2.5-7B"]) == "Qwen2.5-7B"
    assert model_hint(["llama-server", "-m", "/models/phi-3.gguf"]) == "phi-3"
