# llm-gpu-agent

A small per-host agent for llm-secure-gateway. It reports GPU utilisation, VRAM, temperature
and power, plus which model process uses which GPU. NVIDIA is read through NVML. AMD is read
through `amdsmi`, or through `rocm-smi` when `amdsmi` is not installed.

Hosts that already run **dcgm-exporter**, **nvidia_gpu_exporter** or the **AMD
device-metrics-exporter** do not need this agent. Add those hosts with their source type
instead. Those sources do not report per-process data.

## Endpoints

| Path | Auth | Purpose |
|---|---|---|
| `GET /v1/gpu` | `Authorization: Bearer $GPU_AGENT_TOKEN` | JSON snapshot the gateway reads |
| `GET /metrics` | same | Prometheus text, `llm_gpu_*` with a `host` label |
| `GET /healthz` | none | liveness only |

The token is required. Process command lines are sensitive, so the agent refuses to start
without `GPU_AGENT_TOKEN`. The gateway reads the same variable.

## Run

Docker (NVIDIA hosts):

```bash
cd agent
GPU_AGENT_TOKEN=... docker compose up -d --build
```

Bare metal:

```bash
pip install "./agent[nvidia]"          # AMD: install amdsmi from ROCm, or keep rocm-smi
GPU_AGENT_TOKEN=... llm-gpu-agent --port 9400 --interval 2
```

See `llm-gpu-agent.service` for a systemd unit.

## Register the host in the gateway

On the Observability page (GPUs tab), or with the API:

```bash
curl -X POST https://gateway/admin/observability/gpu-hosts \
  -H "Authorization: Bearer $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"name":"gpu-a","url":"http://gpu-a.internal:9400","source":"agent","enabled":true}'
```

Then bind a model server to the host: `PUT /admin/observability/servers/{name}/gpu` with
`{"gpu_host": "gpu-a", "gpu_indices": null}`. `gpu_indices` pins specific GPUs. When it is
null, the gateway attributes GPUs from the processes it sees.

## Known limits

- Process-to-model matching uses the command line (`vllm serve X`, `--served-model-name`,
  llama.cpp `-m` / `--alias`). Ollama runners use a blob path, so they cannot be matched by
  name. Use `gpu_indices` for those servers.
- The vLLM v1 EngineCore child holds the VRAM. The agent walks up to 4 parent processes to
  find the model name.
- AMD per-process lists are not collected yet. Only `rocm-smi` and `amdsmi` device totals are reported.
- AMD metric names in the gateway's exporter reader are unverified. See `AMD_METRICS` in
  `src/llm_gateway/observability/collect/gpu.py`. The agent path does not depend on them.
