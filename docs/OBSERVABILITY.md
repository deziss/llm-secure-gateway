# Observability

Added in v0.14.0. The gateway measures what it proxies (per-model traffic, token throughput,
time to first token, queue depth), reads the state of the model servers and GPUs behind it, and
sends the same numbers to three places: the admin page, Prometheus, and any OTLP-compatible
tool you choose.

Telemetry is configured by environment variables only. No credentials are stored in the
database.

## Architecture

```
            requests / streams (proxy routers)
                         │
                         ▼
   ┌───────────────────────────────────────────────┐
   │ COLLECT (in process, cheap, no network I/O)   │
   │  usage.py      tokens, TTFT, tok/s            │
   │  requests.py   waiting / generating, rps      │
   │  process.py    RSS, USS, threads, fds, tasks  │
   │  poller.py     model server /metrics, GPU     │ ── every OBS_POLL_INTERVAL (5 s),
   │                hosts (one overall deadline)   │    one deadline per tick
   └───────────────┬───────────────────────────────┘
                   │
                   ▼
   ┌───────────────────────────────────────────────┐
   │ STORE                                          │
   │  LiveStats: 10 s / 60 s rings, in-process      │
   │  Redis (only if REDIS_URL): obs:live:{replica} │
   │  TTL 10 s, merged by /live for multi-replica   │
   └───────┬───────────────────────┬───────────────┘
           │                       │
           ▼                       ▼
   EXPORT                    ADMIN PAGE
   /metrics (Prometheus)     /admin/observability
   OTLP sinks (OBS_EXPORTERS)  SSE /admin/observability/stream (3 s)
   server + GPU gauges         fallback polling /admin/observability/live (5 s)
```

Notes:

- The browser never talks to model servers or GPU agents. CSP allows `'self'` only, so every
  read goes through `/admin/observability/*`.
- Model servers and GPU hosts are polled in the gateway process. A black-holed backend cannot
  hold up the rest of the poll, because each tick has one overall deadline.
- Sinks are independent. A failing sink shows its last error on the Integrations tab and never
  blocks a request.

## Turning it on

| Variable | Default | What it does |
|---|---|---|
| `OBS_EXPORTERS` | empty | Comma-separated presets to send to, e.g. `langfuse,phoenix`. Empty means no OTLP export. |
| `ENABLE_TELEMETRY` | `false` | Backward-compatible switch for the Phoenix path. Still works without `OBS_EXPORTERS`. |
| `OBS_METRICS_TOKEN` | empty | If set, `/metrics` requires `Authorization: Bearer <token>` (exact `Bearer ` prefix). If unset, `/metrics` returns 403 unless `OBS_METRICS_ALLOW_ANONYMOUS=true`. |
| `OBS_METRICS_ALLOW_ANONYMOUS` | `false` | Serves `/metrics` without a token when `OBS_METRICS_TOKEN` is unset. Logs a startup warning; keep the port on a private network. |
| `OBS_POLL_INTERVAL` | `5` | Seconds between model-server and GPU polls (minimum 1). |
| `OBS_INSTANCE_ID` | hostname | Stable name for this replica in the UI and as `service.instance.id`. |
| `OBS_PROMETHEUS` | `true` | Serves `/metrics`. `false` makes it return 404. Independent of `ENABLE_TELEMETRY`. |
| `OBS_METRICS_INTERVAL_S` | `60` | How often OTLP sinks export metrics (minimum 1 s). |
| `OBS_FORCE_STREAM_USAGE` | `false` | For vLLM and llama.cpp streams, adds `stream_options.include_usage` so exact token counts arrive. It adds one extra `choices: []` chunk the client did not ask for, which is why it is opt-in. |
| `REDIS_URL` | empty | With several gateway replicas, the admin page merges live stats from all of them (scope `cluster`). Without it the scope is `replica`. |
| `GPU_AGENT_TOKEN` | empty | Bearer token the gateway sends to a GPU agent (set the same value on the agent host). |

Sink variables are listed per tool below. Each preset reads only its own variables, and a
missing required variable is logged and skipped. It does not stop the gateway.

## Export presets

Set `OBS_EXPORTERS` to one or more names. Each preset sends traces and/or metrics over OTLP/HTTP.

| Preset | Target | Required variables | Optional variables (default) |
|---|---|---|---|
| `phoenix` | Self-hosted Arize Phoenix | `PHOENIX_COLLECTOR_ENDPOINT` (`http://localhost:6006`), `PHOENIX_API_KEY` if auth is on | `PHOENIX_PROJECT_NAME` (`llm-gateway`) |
| `phoenix` (Phoenix Cloud) | app.phoenix.arize.com | `PHOENIX_COLLECTOR_ENDPOINT`, `PHOENIX_API_KEY` | `PHOENIX_PROJECT_NAME` |
| `phoenix` (Arize AX) | Arize AX | `ARIZE_SPACE_ID`, `ARIZE_API_KEY` | `ARIZE_OTLP_ENDPOINT` (`https://otlp.arize.com/v1/traces`) |
| `langfuse` | Langfuse (cloud or self-hosted) | `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | none. Sent as Basic auth. |
| `langsmith` | LangSmith | `LANGSMITH_API_KEY` | `LANGSMITH_ENDPOINT` (`https://api.smith.langchain.com`), `LANGSMITH_PROJECT` (`llm-gateway`) |
| `openobserve` | OpenObserve | `ZO_URL`, `ZO_USER`, `ZO_TOKEN` | `ZO_ORG` (`default`), `ZO_STREAM` (`default`). Sent as Basic auth. |
| `signoz` | SigNoz Cloud | `SIGNOZ_INGESTION_KEY` | `SIGNOZ_REGION` (`us`) |
| `signoz` (self-hosted) | SigNoz OTel collector | `SIGNOZ_OTLP_ENDPOINT` (`http://localhost:4318`) | none |
| `grafana` | Grafana Cloud OTLP | `GRAFANA_OTLP_ENDPOINT`, `GRAFANA_OTLP_INSTANCE_ID`, `GRAFANA_OTLP_TOKEN` | none. Sent as Basic auth. |
| `grafana` (local) | Local Alloy or OTel collector | `GRAFANA_OTLP_ENDPOINT` (`http://localhost:4318`) | none |
| `datadog` | Datadog Agent (OTLP ingest) | `DD_OTLP_ENDPOINT` (`http://localhost:4318`) | none. The agent holds the API key. |
| `honeycomb` | Honeycomb | `HONEYCOMB_API_KEY` | `HONEYCOMB_ENDPOINT` (`https://api.honeycomb.io`) |
| `newrelic` | New Relic | `NEW_RELIC_LICENSE_KEY` | `NEW_RELIC_OTLP_ENDPOINT` (`https://otlp.nr-data.net`) |
| `elastic` | Elastic APM / Elastic Cloud OTLP | `ELASTIC_OTLP_ENDPOINT`, `ELASTIC_API_KEY` | none |
| `otlp` | Any OTLP/HTTP collector | `OTEL_EXPORTER_OTLP_ENDPOINT`, or the per-signal `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` / `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | `OTEL_EXPORTER_OTLP_HEADERS` (standard `k=v,k=v` form) |

Example:

```bash
OBS_EXPORTERS=langfuse,grafana
LANGFUSE_HOST=https://cloud.langfuse.com
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
GRAFANA_OTLP_ENDPOINT=https://otlp-gateway-prod-eu-west-2.grafana.net/otlp
GRAFANA_OTLP_INSTANCE_ID=123456
GRAFANA_OTLP_TOKEN=glc_...
```

Presets marked "verify" in the `presets.py` docstring are the least certain: their endpoint paths or
headers follow the vendor docs but were not checked against a live service. Confirm them against a
live collector before you rely on them.

To check a preset, set `OBS_EXPORTERS`, restart the gateway, send one streaming and one
non-streaming request, then look at the Integrations tab. `last_ok` should update within a
minute, and `failed` should stay at zero.

## The admin page

`/admin/observability` is visible to admins and managers. Managers see read-only views.

| Tab | Shows | Writes (admin) |
|---|---|---|
| Models | Per server × model: RPS (10 s, 60 s), waiting, generating, tok/s decode and throughput, TTFT p50, server running/waiting, KV cache %, RPS sparkline | none |
| Groups | One card per model group: VRAM `used / total GiB (%)`, GPU utilisation average and count, Σ RPS, Σ tok/s, Σ queue. Each instance shows its GPUs and an attribution badge. | none here |
| GPUs | Per GPU host: source, fresh/stale badge, per GPU util %, VRAM MiB and GiB, temperature, power and limit, expandable process list | GPU host CRUD, server-to-GPU link |
| Gateway | RSS, USS, threads, asyncio tasks, open fds, CPU %, GC counts, per-thread CPU time, in-flight bytes per model, tracemalloc top 20, memory history | tracemalloc toggle, group overrides |
| Integrations | Each sink: signals, endpoint, last OK, last error, exported and failed counts | none |

Every percentage appears with its absolute value. A missing value is shown as `—`.

The scope badge says `replica` or `cluster`. With `REDIS_URL` set, `cluster` means the numbers
are summed across replicas. The instance and replica count are shown next to it.

Live updates arrive over SSE every 3 s. If the stream fails, the page falls back to polling
every 5 s. Both pause when the tab is hidden.

### tracemalloc

tracemalloc records every Python allocation with its traceback. Turning it on adds roughly
10-30% CPU overhead. Use it briefly to find a leak, then turn it off. It is off by default,
and only admins can toggle it.

## Prometheus `/metrics`

`GET /metrics` on the gateway port (8000 in the container; 6130 on the default host mapping)
exposes the OpenTelemetry instruments in Prometheus text format. Set `OBS_METRICS_TOKEN` and
send `Authorization: Bearer <token>`; with no token the endpoint answers 403 unless
`OBS_METRICS_ALLOW_ANONYMOUS=true`. The route is public to the auth middleware, and the token
is its only guard.

```bash
curl -s -H "Authorization: Bearer $OBS_METRICS_TOKEN" http://localhost:6130/metrics | grep llm_gateway_model
```

`prometheus/prometheus.yml` scrapes the gateway and the GPU agents. The Grafana dashboard
(`grafana/llm-gateway-dashboard.json`) and alert rules (`grafana/alerts.yml`) use only the names
below.

## GPU agent

The gateway cannot see GPU memory per process from the outside. The GPU agent runs on each GPU
host and exposes `llm_gpu_*` metrics on port 9835. It also reports the process list with the
model each process serves.

### Install with Docker

```bash
docker run -d --name gpu-agent --restart unless-stopped \
  --gpus all \
  --pid=host \
  -e GPU_AGENT_TOKEN=change-me \
  -p 9835:9835 \
  <your-registry>/llm-gateway-gpu-agent:0.14.0
```

- `--pid=host` lets the agent map a GPU process ID to the container or server that owns it. Without
  it you still get the totals, but no process-to-model attribution.
- `--gpus all` requires the NVIDIA Container Toolkit. For AMD use `--device=/dev/kfd --device=/dev/dri`
  instead.
- Set the same `GPU_AGENT_TOKEN` in the gateway's environment.

### Install with systemd

Run the agent as a service with the same flags, using `Restart=always`, and point the gateway at
`http://<gpu-host>:9835/metrics`. Keep port 9835 on a private network.

### Other exporters

If you cannot run the agent, use an existing exporter and register its host with the matching
source on the GPUs tab:

| Exporter | Source value | Per-process data |
|---|---|---|
| This project's GPU agent | `agent` | Yes |
| NVIDIA DCGM exporter | `dcgm` | Usually no |
| nvidia_gpu_exporter | `nvidia_smi_exporter` | Usually no |
| AMD exporter | `amd_exporter` | Depends on the exporter |

When the exporter has no per-process data, the GPUs tab shows
"n/a — exporter has no per-process data". Attribution then falls back to the host total.

### Do I need the agent?

- Only for per-process VRAM and model attribution, which is what the Groups tab uses for
  "per-process" and what the per-process panels in Grafana need.
- For totals alone (utilisation, VRAM used/total, temperature, power), DCGM or nvidia_gpu_exporter is
  enough. Register the host with the matching source.
- To attribute a whole GPU to one server, pin it on the GPUs tab (`gpu_attribution: pinned`). That
  works with any source and needs no agent.

## Metric reference

Names are final Prometheus names as emitted in v0.14.0.

### Gateway (`llm_gateway_*`)

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `llm_gateway_requests_total` | counter | `server`, `server_type`, `model`, plus existing `backend_url`, `endpoint_uri`, `user_name` | Requests started |
| `llm_gateway_requests_success_total` | counter | same | Requests that completed |
| `llm_gateway_requests_failed_total` | counter | same | Requests that failed or were cancelled upstream |
| `llm_gateway_prompt_tokens_total` | counter | `server`, `server_type`, `model` | Input tokens |
| `llm_gateway_completion_tokens_total` | counter | same | Output tokens |
| `llm_gateway_total_tokens_total` | counter | same | Input plus output |
| `llm_gateway_request_duration_seconds` | histogram | same | End-to-end duration |
| `llm_gateway_time_to_first_token_seconds` | histogram | same | Time to first token (streams) |
| `llm_gateway_generation_tokens_per_second` | histogram | same | Per-request decode speed |
| `llm_gateway_model_requests` | gauge | `server`, `model`, `state` (`waiting` / `generating`) | Live queue and active generation |
| `llm_gateway_model_output_tokens_per_second` | gauge | `server`, `model` | Aggregate output tok/s over 60 s |
| `llm_gateway_inflight_buffer_bytes` | gauge | `server`, `model` | Bytes buffered for in-flight responses |
| `llm_gateway_process_memory_bytes` | gauge | `type` (`rss` / `uss`) | Gateway process memory |
| `llm_gateway_process_threads` | gauge | | Thread count |
| `llm_gateway_asyncio_tasks` | gauge | | Live asyncio tasks |

The OpenTelemetry resource attribute `service.instance.id` becomes the Prometheus `instance` label
(or a `target_info` label). Use `instance` to tell replicas apart.

### Model servers (`llm_server_*`)

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `llm_server_requests` | gauge | `server`, `state` (`running` / `waiting`) | Server-side queue, from its own metrics |
| `llm_server_kv_cache_usage_ratio` | gauge | `server` | KV cache fill, 0 to 1 |
| `llm_server_generation_tokens_per_second` | gauge | `server` | Server-reported decode speed |
| `llm_server_up` | gauge | `server` | 1 when the last poll succeeded |

### GPUs (`llm_gpu_*`)

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `llm_gpu_utilization_ratio` | gauge | `host`, `gpu`, `uuid`, `name`, `vendor` | 0 to 1 |
| `llm_gpu_memory_used_bytes` | gauge | same | VRAM used |
| `llm_gpu_memory_total_bytes` | gauge | same | VRAM total |
| `llm_gpu_temperature_celsius` | gauge | same | Temperature |
| `llm_gpu_power_watts` | gauge | same | Power draw |
| `llm_gpu_process_memory_bytes` | gauge | `host`, `gpu`, `pid`, `process`, `model`, `server` | VRAM per process, mapped to the server that owns it |
| `llm_model_group_info` | gauge (always 1) | `server`, `model`, `group` | Join helper: `… * on (server, model) group_left(group)` |

The GPU agent exports the same `llm_gpu_*` names with `host` as a label.

### Grafana query patterns

```promql
# Requests per group (per-model rate, joined to its group)
sum by (group) (
  sum by (server, model) (rate(llm_gateway_requests_total[1m]))
  * on (server, model) group_left(group)
  max by (server, model, group) (llm_model_group_info)
)

# VRAM used by group (bytes)
sum by (group) (
  llm_gpu_process_memory_bytes
  * on (server, model) group_left(group)
  max by (server, model, group) (llm_model_group_info)
)

# VRAM percent per GPU
llm_gpu_memory_used_bytes / llm_gpu_memory_total_bytes * 100
```

## What is not instrumented

- **Native Anthropic `/v1/messages` passthrough** is not instrumented in v0.14.0. Requests on that
  route do not appear in the metrics or the Models tab. The OpenAI-compatible and Ollama routes are.
- **Text-to-speech** requests are not instrumented.
- **Bots** (Telegram, Slack, Discord, WhatsApp) are not instrumented.
- **Per-thread memory** cannot be measured from Python. The gateway has no per-thread allocator
  counters. The Gateway tab shows per-thread CPU time instead, plus process RSS and USS.
- **Token counts for some streams** are estimated when the upstream sends no usage block. Those
  rows show "≈ est. tokens". Set `OBS_FORCE_STREAM_USAGE=true` to ask for usage.
- **Circuit breaker state** is not in the metric list above. It is visible on the Model servers page.

## Troubleshooting

- **Models tab empty:** no traffic has passed this replica since restart. Send a request.
- **Groups show "Not attributed":** the server has no GPU link. Pin it on the GPUs tab, or run an agent
  with `--pid=host`.
- **GPU host shows "stale":** the poll is older than 30 s. Check the agent and the token.
- **`/metrics` returns 403:** neither `OBS_METRICS_TOKEN` nor `OBS_METRICS_ALLOW_ANONYMOUS=true` is set.
- **`/metrics` returns 401:** `OBS_METRICS_TOKEN` is set and the request has no matching bearer token.
- **A sink's `last_error` says it cannot connect:** check the endpoint from inside the gateway
  container, not from your laptop.

## Managing integrations from the admin UI

Admin → Observability → **Integrations** lists every supported tool. Open a card to see how to connect it, fill in the endpoint and credentials, press **Test** (one real span is sent and the backend's HTTP answer is shown), then save. Turn a tool off without losing its settings, or remove it (deletes the stored credentials).

- Changes apply immediately; other gateway replicas pick them up within about 15 seconds.
- Credentials are stored Fernet-encrypted (`ENCRYPTION_KEY`) in the `obs_integration` table and are never returned by the API; leave a secret field blank to keep the stored value.
- Settings made in the environment (`OBS_EXPORTERS`) still work and show as source `env` in **Live export status**. If a tool is configured in both places it exports twice, so use one.
- Integrations configured here export even when `ENABLE_TELEMETRY` is off. Per-tenant Phoenix projects remain an environment feature.
- Prometheus is pull-based and environment-controlled (`OBS_PROMETHEUS`, `OBS_METRICS_TOKEN`); its card shows a scrape config to copy.

The Models and Groups tabs have filters and column sorting; the Groups tab has Cards and List views.
