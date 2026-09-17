# LLM Gateway - Phoenix Telemetry & OpenInference Guide

## Overview

This document covers the complete telemetry implementation for the LLM Gateway, including Phoenix integration, OpenInference semantic conventions, and troubleshooting guides.

> For general API usage, routing strategies, and admin dashboard instructions, please see the **[User Guide](USER_GUIDE.md)**.

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

## 2. Multi-Tenant Project Separation

### The Problem

Phoenix groups traces by the ROOT span's project name. If FastAPI auto-instrumentation creates root spans with "llm-gateway" project, all tenant-specific child spans appear under that project.

### Solution

- Set `set_global_tracer_provider=False` in the global tracer
- Disable FastAPI auto-instrumentation
- Let tenant-specific tracers create root spans

### PhoenixTraceManager Usage

```python
# Creates per-tenant tracer with project name: gateway-{owner_id}-{provider}
tenant_tracer = PhoenixTraceManager.get_tracer(owner_id, provider)
```

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
| `backend.url`                | Backend URL                   | `http://10.10.110.25:11434`              |

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
