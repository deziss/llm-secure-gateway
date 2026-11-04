# LLM Gateway - Phoenix Telemetry & OpenInference Guide

## Overview

This document covers the complete telemetry implementation for the LLM Gateway, including Phoenix integration, OpenInference semantic conventions, and troubleshooting guides.

> For general API usage, routing strategies, and admin dashboard instructions, please see the **[User Guide](USER_GUIDE.md)**.

---

## 0. Turning telemetry on and off

Telemetry export is **opt-in**. `ENABLE_TELEMETRY` is the master switch:

| Value | Behaviour |
| :--- | :--- |
| `false` *(default)* | No OTLP exporter is created. In-process metric instruments still work; nothing leaves the process. |
| `true` | Traces go to Phoenix, metrics to the OTLP collector. |

```bash
ENABLE_TELEMETRY=true
PHOENIX_COLLECTOR_ENDPOINT=http://phoenix:6006
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
```

Two things to know:

1. **`docker restart` will not pick up a changed value** — it reuses the container's existing environment. Use `docker compose up -d`.
2. **Inside Docker the endpoint must not be `localhost`.** `localhost` resolves to the container itself, so the exporter connects to nothing and retries forever:
   ```
   Transient error HTTPConnectionPool(host='localhost', port=4318): ...
   [Errno 111] Connection refused ... retrying in 3.71s.
   ```
   Use `host.docker.internal` (host) or the collector's compose service name.

Verify at startup:

```bash
docker compose logs gateway | grep -i telemetry
```

---

## 0a. Observability and export presets (v0.14.0)

Since v0.14.0 the gateway collects live model, GPU and process statistics and can export them to
several tools. The full guide is **[docs/OBSERVABILITY.md](docs/OBSERVABILITY.md)**. Short version:

| Variable | Purpose |
| :--- | :--- |
| `OBS_EXPORTERS` | Comma-separated presets, e.g. `phoenix,langfuse,grafana`. Each preset reads its own vendor variables (see the doc). |
| `OBS_PROMETHEUS` | `true` (default) serves `/metrics`. |
| `OBS_METRICS_TOKEN` | Bearer token required on `/metrics`. Unset: 403 unless `OBS_METRICS_ALLOW_ANONYMOUS=true`. |
| `OBS_METRICS_ALLOW_ANONYMOUS` | `true` serves `/metrics` without a token (startup warning). Default `false`. |
| `OBS_METRICS_INTERVAL_S` | OTLP metric export interval (default 60). |
| `OBS_POLL_INTERVAL` | Model server and GPU poll interval (default 5 s). |
| `OBS_INSTANCE_ID` | Replica name shown in the UI and as `service.instance.id`. |
| `OBS_FORCE_STREAM_USAGE` | Opt-in: request usage in vLLM and llama.cpp streams. |
| `REDIS_URL` | Merge live stats across replicas (scope `cluster`). |
| `GPU_AGENT_TOKEN` | Bearer token for GPU agents. |

`ENABLE_TELEMETRY=true` keeps working as before (Phoenix traces plus the OTLP metrics collector).
`OBS_EXPORTERS` is the way to select other tools.

---

## 1. Phoenix Authentication & Configuration

### Environment Configuration

**docker-compose.yml:**

```yaml
services:
  gateway:
    environment:
      - PHOENIX_COLLECTOR_ENDPOINT=http://host.docker.internal:6006
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
      - PHOENIX_API_KEY=${PHOENIX_API_KEY:-}
    extra_hosts:
      - "host.docker.internal:host-gateway" # Required for container→host connectivity
```

**.env:**

```
PHOENIX_API_KEY=your-phoenix-api-key
```

### Authentication Fix (401 Error Resolution)

**Root Cause:** Duplicate OTLP exporter without authorization headers.

**Solution:** Use `phoenix.otel.register()` which correctly handles authentication with the `headers` parameter. Do not add additional exporters that lack auth headers.

---

## 2. Multi-Tenant Project Separation & Context Isolation

### The Problem

Arize Phoenix attributes traces to projects based solely on the **root span's** resource attribute:
```json
"openinference.project.name": "gateway-<owner_id>-<provider>"
```
In an async ASGI framework like FastAPI, incoming requests may inherit ambient context from:
1. Outer FastAPI middleware or routing spans created by default/global tracers (`service.name=llm-gateway`).
2. Upstream W3C `traceparent` headers injected by IDE extensions, client agents, or reverse proxies.

When a tenant-specific span is created without explicit context isolation (`tracer.start_span("llm_request")`), OpenTelemetry Python automatically binds the active ambient span as its parent (`span.parent`). Because the trace root belongs to the global project (`llm-gateway`), Phoenix indexes the **entire trace** under `llm-gateway`, ignoring the tenant tracer provider's project name.

### The Solution: Root Span Context Isolation

To guarantee that tenant spans act as independent trace roots in Phoenix and multi-tenant telemetry backends:
1. **Explicit Context Decoupling**: Spans are instantiated with `context=trace.Context()` to strip any ambient parent context.
2. **Centralized Helper**: Use `PhoenixTraceManager.start_root_span(owner_id, provider, span_name)`.
3. **Disabled Global Auto-Instrumentation**: `FastAPIInstrumentor` is intentionally omitted so root spans are controlled explicitly per tenant.

```python
# In llm_gateway.telemetry.tracing:
span = PhoenixTraceManager.start_root_span(
    owner_id=owner_id,
    backend_name=provider,
    span_name="llm_request"
)
```

Child spans generated during streaming or downstream tool calls correctly nest under this tenant root span:

```python
with trace.use_span(span):
    # Child spans inherit tenant trace_id and span_id
    child_span = tenant_tracer.start_span("tool_call")
```

### Enabling Project-Wise Tracing

Project separation is enabled by default when Phoenix telemetry is active:

1. **Via Environment (`.env` or Docker Compose)**:
   ```bash
   ENABLE_TELEMETRY=true
   PHOENIX_COLLECTOR_ENDPOINT=http://phoenix:6006
   PHOENIX_PROJECT_PER_TENANT=true   # true: gateway-<project>-<provider>; false: llm-gateway
   ```

2. **Via Admin UI (`/admin/observability` -> Integrations -> Arize Phoenix)**:
   - Check **"Separate project per gateway project and provider"**.
   - Traces are dynamically dispatched to `gateway-<owner_id>-<provider>`.
   - Changes take effect immediately without restarting the gateway.

---

## 2a. Multi-Tenant Tracing Across Observability Platforms

Does project-wise tracing work for other tools like Langfuse, OpenObserve, and Grafana Tempo? **Yes, each platform supports multi-tenancy differently.** Here is the complete architectural comparison:

| Platform | Multi-Tenant Grouping Model | How `llm-secure-gateway` Integrates | Native Project Dashboard? |
| :--- | :--- | :--- | :--- |
| **Arize Phoenix** | Resource attribute `openinference.project.name` on the root span. | Dynamic per-tenant `TracerProvider` caching or managed span processor. | **Yes**: Creates separate project workspaces (`gateway-<owner>-<provider>`) in the Phoenix UI. |
| **Langfuse** | Scoped per API Key pair (`pk-lf-...` / `sk-lf-...`). Within a project, multi-tenancy uses metadata & attributes (`user.id`, `session.id`, `tags`). | Emits standard `owner.id`, `model`, `provider`, and session tags on every span. For total isolation, configure distinct Langfuse projects per department. | **Yes** (via separate API keys) or **Filter-based** (filter by `user.id` / `owner.id` in a single project). |
| **OpenObserve** | Organizations (`ZO_ORG`), Streams (`stream-name`), or OpenTelemetry resource/span attributes. | Exports OTLP traces with Basic Auth. Spans carry `owner.id`, `provider`, `model`. Can be partitioned into dedicated streams via `stream-name: gateway_<tenant>` or queried via SQL / TraceQL. | **Yes**: Filter/dashboard per project using SQL queries (`SELECT * FROM traces WHERE "owner.id" = 'anshu'`). |
| **Grafana Tempo / Cloud** | Multi-tenant tenant ID header (`X-Scope-OrgID`) or TraceQL span attribute filtering. | Exports OTLP traces. In shared Tempo, filter traces in Explore using TraceQL: `{ .owner.id = "anshu" && .provider = "vllm" }`. In enterprise Tempo, pass tenant headers. | **Yes**: Dedicated dashboards with template variables parameterized on `$owner_id`. |

### Detailed Platform Behaviors

#### 1. Arize Phoenix & Arize AX
- **Mechanism**: The OpenInference spec maps projects directly to `openinference.project.name` in the tracer's `Resource`.
- **Project Structure**: Each unique `(owner_id, provider)` pair automatically provisions a project, for example:
  - `gateway-anshu-vllm`
  - `gateway-default-owner-vllm`
  - `gateway-team-nlp-openai`
- **Result**: Complete separation in the Phoenix project switcher dropdown, independent evaluation datasets, and separate drift tracking.

#### 2. Langfuse
- **Mechanism**: Langfuse projects are security boundaries defined by the public/secret key pair. Within a project, Langfuse natively tracks multi-tenancy through:
  - `user.id`: Mapped to the gateway's `owner.id`.
  - `metadata`: Contains `provider`, `backend.url`, `backend.type`, `model`.
  - `tags`: Allows filtering by environment or project tag.
- **Project Isolation**: Teams wanting isolated Langfuse workspaces can configure separate integrations or filter the main dashboard by `User ID = anshu`.

#### 3. OpenObserve
- **Mechanism**: OpenObserve ingests traces into high-efficiency columnar streams.
- **Tenancy Options**:
  - **Single Organization / Shared Stream**: Traces are indexed with all span attributes. In the OpenObserve UI, query with SQL:
    ```sql
    SELECT trace_id, duration, span_kind, "llm.model_name"
    FROM "default"
    WHERE "owner.id" = 'anshu'
    ORDER BY start_time DESC
    ```
  - **Multi-Org Isolation**: Point `ZO_ORG` to separate tenant organizations for strict RBAC separation.

#### 4. Grafana Tempo
- **Mechanism**: Tempo stores traces indexed by search tags (span attributes).
- **TraceQL Queries**:
  - Find all traces for project `anshu`:
    ```traceql
    { .owner.id = "anshu" && .openinference.span.kind = "LLM" }
    ```
  - Find failed inference requests for a specific backend:
    ```traceql
    { .owner.id = "anshu" && status = error }
    ```
- **Grafana Dashboard**: Create a dropdown variable `$owner` mapped to label `owner.id` to create instant tenant-isolated trace views.

---

## 3. OpenInference Semantic Conventions

### Span Attributes Captured

| Attribute                    | Description                   | Example                                  |
| ---------------------------- | ----------------------------- | ---------------------------------------- |
| `openinference.span.kind`    | Span type                     | `LLM`                                    |
| `llm.model_name`             | Model being used              | `llama3.2:latest`                        |
| `llm.input_messages`         | JSON array of input messages  | `[{"role":"user","content":"Hello"}]`    |
| `llm.output_messages`        | JSON array of output messages | `[{"role":"assistant","content":"Hi!"}]` |
| `llm.invocation_parameters`  | Model parameters              | `{"temperature": 0.7}`                   |
| `llm.token_count.prompt`     | Input tokens                  | `150`                                    |
| `llm.token_count.completion` | Output tokens                 | `50`                                     |
| `llm.token_count.total`      | Total tokens                  | `200`                                    |
| `owner.id`                   | Tenant/user identifier        | `test-user-v3`                           |
| `backend.name`               | Backend name                  | `ollama`                                 |
| `backend.url`                | Backend URL                   | `http://192.0.2.25:11434`              |

### Example Phoenix Trace

```json
{
  "name": "llm_request_direct",
  "attributes": {
    "openinference.span.kind": "LLM",
    "llm.model_name": "mistral",
    "llm.input_messages": "[{\"role\":\"user\",\"content\":\"Hello!\"}]",
    "llm.output_messages": "[{\"role\":\"assistant\",\"content\":\"Hi there!\"}]",
    "llm.invocation_parameters": "{\"stream\": false}",
    "owner.id": "test-user-v3",
    "backend.name": "ollama"
  }
}
```

---

## 4. Telemetry Code Structure

### Key Components

| File           | Function                   | Purpose                                                   |
| -------------- | -------------------------- | --------------------------------------------------------- |
| `telemetry.py` | `setup_telemetry()`        | Initialize OpenTelemetry, Phoenix tracing, and metrics    |
| `telemetry.py` | `PhoenixTraceManager`      | Per-tenant tracer management with project isolation       |
| `telemetry.py` | `stream_with_telemetry()`  | Parse streaming responses, capture output and token usage |
| `telemetry.py` | `record_request_metrics()` | Record metrics with token counts                          |
| `v2_proxy.py`  | `direct_proxy()`           | LLM request handling with tracing for direct routing      |
| `v2_proxy.py`  | `dynamic_proxy()`          | LLM request handling with tracing for dynamic routing     |

### Span Structure

```
llm_request_direct (root, kind=LLM)
├── openinference.span.kind = "LLM"
├── llm.model_name
├── llm.input_messages
├── llm.output_messages
├── backend.name, backend.url
└── owner.id
```

---

## 5. Testing & Verification

### Send Test Request

```bash
curl -X POST http://localhost:6130/direct/ollama/api/chat \
  -H "X-API-Key: YOUR_GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mistral",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false
  }'
```

### Verify in Phoenix UI

1. Open: http://localhost:6006
2. Click "Traces" tab
3. Find your trace (project: `gateway-{owner}-{backend}`)
4. Click span to see all attributes

### Check for Errors

```bash
# Check for auth errors
docker logs llm-gateway-v3-gateway-1 | grep "401"

# Check for telemetry logs
docker logs llm-gateway-v3-gateway-1 | grep -i "phoenix\|tracer"
```

---

## 6. Graceful Fallbacks

### OTLP Metrics Endpoint

If the OTLP metrics endpoint is unreachable, the gateway automatically falls back to console metrics:

```
WARNING: OTLP metrics endpoint http://localhost:4318 unreachable, using console exporter
```

### Response Parsing

- **SSE Format**: Parses `data: {"choices":[{"delta":{"content":"..."}}]}`
- **JSON Format**: Parses `{"message":{"content":"..."}}`
- **Fallback**: Raw response stored as `output.value`

---

## 7. Troubleshooting

| Issue                | Cause                             | Solution                                      |
| -------------------- | --------------------------------- | --------------------------------------------- |
| 401 Invalid Token    | Exporter missing auth headers     | Use `phoenix.otel.register()` with headers    |
| Kind shows "unknown" | Missing `openinference.span.kind` | Set as first attribute on root span           |
| Separate traces      | No parent-child relationship      | Create root span with `start_as_current_span` |
| Wrong project name   | FastAPI auto-instrumentation      | Disable `FastAPIInstrumentor`                 |
| Empty output.value   | Backend not sending SSE format    | Check backend response format                 |
| No token counts      | Backend doesn't include usage     | Some backends need `stream=false`             |

---

## 8. Performance Notes

- **Memory**: Output truncated to 10,000 characters
- **Latency**: Stream parsing adds ~1-2ms per chunk
- **Non-blocking**: Parsing happens while streaming to client

---

## Summary

**Telemetry Stack:**

- OpenTelemetry SDK for traces and metrics
- Phoenix for LLM observability
- OpenInference semantic conventions for LLM attributes

**Key Features:**

- ✅ Multi-tenant project isolation
- ✅ Full input/output capture with JSON array format
- ✅ Token usage tracking
- ✅ Graceful fallbacks for unreachable endpoints
- ✅ SSE and JSON response parsing
