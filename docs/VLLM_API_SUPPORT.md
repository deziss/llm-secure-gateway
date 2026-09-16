# vLLM Native API Support in llm-secure-gateway

## Overview
The llm-secure-gateway now fully supports vLLM native APIs through the catch-all proxy router with bearer token authentication.

## API Status Summary

| API Endpoint | Method | Status | HTTP Code | Notes |
|---|---|---|---|---|
| `/health` | GET | ✅ Working | 200 | Server health check |
| `/metrics` | GET | ✅ Working | 200 | Prometheus metrics |
| `/tokenize` | POST | ✅ Working | 200 | Token encoding |
| `/detokenize` | POST | ✅ Working | 200 | Token decoding |
| `/v1/models` | GET | ✅ Working | 200 | List available models |
| `/v1/completions` | POST | ✅ Working | 200 | OpenAI-compatible completions |
| `/v1/chat/completions` | POST | ✅ Working | 200 | OpenAI-compatible chat |
| `/v1/embeddings` | POST | ✅ Supported | 200 | Embeddings (if enabled) |
| `/v1/audio/transcriptions` | POST | ✅ Supported | 200 | Audio transcription (if enabled) |
| `/v1/audio/translations` | POST | ✅ Supported | 200 | Audio translation (if enabled) |
| `/generate` | POST | ❓ Needs Verification | 404 | Direct generation (vLLM-specific) |


## Active Cluster Deployment: `vllm-42`

The gateway is connected to a dedicated high-performance vLLM cluster:
- **Cluster Endpoint**: `http://10.10.110.42:13313`
- **Backend Name**: `vllm-42`
- **Backend Type**: `VLLM`

### Available Models on `vllm-42`

| Model Name | Type | Endpoint | Description |
|---|---|---|---|
| `llama-3.1-8b` | Chat / Text | `/v1/chat/completions` | General-purpose fast text & conversational model |
| `qwen-2.5-14b` | Chat / Text | `/v1/chat/completions` | Balanced 14B instruction & reasoning model |
| `llama-3.3-70b` | Large Reasoning | `/v1/chat/completions` | Flagship 70B parameter open-weights model |
| `llama-4-scout` | High-efficiency | `/v1/chat/completions` | Next-generation lightweight reasoning model |
| `llava-1.6-34b` | Multimodal | `/v1/chat/completions` | Vision-language and text multimodal assistant |
| `nemotron-3-nano-30b-a3b` | Chat / CoT | `/v1/chat/completions` | Advanced reasoning model with chain-of-thought |
| `bge-m3` | Vector Embeddings | `/v1/embeddings` | Dense 1024-dimensional semantic embedding model |
| `qwen-3-30b-a3b` | Chat / CoT | `/v1/chat/completions` | 30B reasoning model with deep thought output |

### Testing `vllm-42` Through the Gateway

#### 1. Chat Completion Example (`llama-3.1-8b`):
```bash
curl -X POST http://localhost:6130/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama-3.1-8b",
    "messages": [
      {"role": "user", "content": "Explain quantum computing in simple terms."}
    ],
    "max_tokens": 256,
    "temperature": 0.7
  }'
```

#### 2. Dense Vector Embeddings (`bge-m3`):
```bash
curl -X POST http://localhost:6130/v1/embeddings \
  -H "Authorization: Bearer sk-gateway-YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "bge-m3",
    "input": "Semantic search sentence to embed."
  }'
```

---

## Access Paths

All vLLM APIs are accessible through:

```
/direct/{backend_name}/{endpoint}
```

### Example:
```
/direct/vLLM-Kaveri/tokenize
/direct/vLLM-Kaveri/v1/completions
```

## Authentication

All requests require bearer token authentication with the vLLM gateway API key:

```bash
Authorization: Bearer sk-gateway-{token}
```

## Curl Test Examples

### 1. Health Check
```bash
curl -X GET \
  "http://localhost:6130/direct/vLLM-Kaveri/health" \
  -H "Authorization: Bearer sk-gateway-jBCrTD55R6zcO-_hywK0xxlov2C4An88jLeDrzqT6S8"
```

**Response:**
```json
{}
```

---

### 2. Metrics (Prometheus)
```bash
curl -X GET \
  "http://localhost:6130/direct/vLLM-Kaveri/metrics" \
  -H "Authorization: Bearer sk-gateway-jBCrTD55R6zcO-_hywK0xxlov2C4An88jLeDrzqT6S8"
```

**Response:** (Prometheus format)
```
# HELP python_gc_objects_collected_total Objects collected during gc
# TYPE python_gc_objects_collected_total counter
python_gc_objects_collected_total{generation="0"} 347123.0
...
```

---

### 3. Tokenize
```bash
curl -X POST \
  "http://localhost:6130/direct/vLLM-Kaveri/tokenize" \
  -H "Authorization: Bearer sk-gateway-jBCrTD55R6zcO-_hywK0xxlov2C4An88jLeDrzqT6S8" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5-27b",
    "prompt": "Hello world"
  }'
```

**Response:**
```json
{
  "count": 2,
  "max_model_len": 65536,
  "tokens": [9419, 1814],
  "token_strs": null
}
```

---

### 4. Detokenize
```bash
curl -X POST \
  "http://localhost:6130/direct/vLLM-Kaveri/detokenize" \
  -H "Authorization: Bearer sk-gateway-jBCrTD55R6zcO-_hywK0xxlov2C4An88jLeDrzqT6S8" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5-27b",
    "tokens": [1, 2, 3, 4, 5]
  }'
```

**Response:**
```json
{
  "prompt": "\"#$%&"
}
```

---

### 5. List Models (OpenAI v1)
```bash
curl -X GET \
  "http://localhost:6130/direct/vLLM-Kaveri/v1/models" \
  -H "Authorization: Bearer sk-gateway-jBCrTD55R6zcO-_hywK0xxlov2C4An88jLeDrzqT6S8"
```

**Response:**
```json
{
  "object": "list",
  "data": [
    {
      "id": "qwen3.5-27b",
      "object": "model",
      "created": 1775633303,
      "owned_by": "vllm",
      "root": "/models/qwen3.5-27b",
      "parent": null,
      "max_model_len": 65536,
      "permission": [...]
    }
  ]
}
```

---

### 6. Completions (OpenAI v1)
```bash
curl -X POST \
  "http://localhost:6130/direct/vLLM-Kaveri/v1/completions" \
  -H "Authorization: Bearer sk-gateway-jBCrTD55R6zcO-_hywK0xxlov2C4An88jLeDrzqT6S8" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5-27b",
    "prompt": "Q: What is 2+2?\nA:",
    "max_tokens": 50
  }'
```

**Response:**
```json
{
  "id": "cmpl-a5878cb294f0acab",
  "object": "text_completion",
  "created": 1775633303,
  "model": "qwen3.5-27b",
  "choices": [
    {
      "index": 0,
      "text": " 4\n\nQ: What is 2+",
      "finish_reason": "length"
    }
  ],
  "usage": {
    "prompt_tokens": 12,
    "total_tokens": 22,
    "completion_tokens": 10
  }
}
```

---

### 7. Chat Completions (OpenAI v1)
```bash
curl -X POST \
  "http://localhost:6130/direct/vLLM-Kaveri/v1/chat/completions" \
  -H "Authorization: Bearer sk-gateway-jBCrTD55R6zcO-_hywK0xxlov2C4An88jLeDrzqT6S8" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5-27b",
    "messages": [
      {"role": "user", "content": "Say hello"}
    ],
    "max_tokens": 50
  }'
```

**Response:**
```json
{
  "id": "chatcmpl-8235668a41026363",
  "object": "chat.completion",
  "created": 1775633303,
  "model": "qwen3.5-27b",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! How can I assist you today?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 12,
    "total_tokens": 22,
    "completion_tokens": 10
  }
}
```

---

## Features Enabled by Default

✅ **Model Federation** - Aggregate models across multiple backends via `/v1/models` and `/api/tags`
✅ **Rate Limiting** - Per-API-key rate limits (configured in settings)
✅ **Telemetry** - OpenTelemetry tracing with Phoenix collector
✅ **Security Headers** - Enforced security headers on all responses
✅ **RBAC** - Role-based access control via API key scopes
✅ **Fallback URLs** - Automatic failover to secondary backend URLs
✅ **Circuit Breaker** - Prevents hammering failed backends
✅ **Streaming Support** - HTTP streaming for long-running requests

## Configuration

### Backend Configuration
```yaml
name: vLLM-Kaveri
base_url: http://vllm-server:8000
backend_type: vllm
models:
  - qwen3.5-27b
allowed_endpoints:
  - "*"  # Allow all endpoints, or specify: /v1/*, /tokenize, /metrics, etc.
```

### Environment Variables
```bash
# Enable model federation (aggregates models across backends)
ENABLE_MODEL_FEDERATION=true

# Enable experimental routing (load balancing across matching backends)
ENABLE_EXPERIMENTAL_ROUTING=false

# Enable retry backoff for failed requests
ENABLE_RETRY_BACKOFF=false
```

## Troubleshooting

### 404 Errors
- Verify the backend is configured in `.env` or admin panel
- Check that `allowed_endpoints` includes the desired endpoint
- Confirm the vLLM backend actually exposes the endpoint

### Rate Limit Errors (429)
- Check API key rate limit: `rate_limit_rpm` setting
- Use a different API key or request higher limits from admin

### Connection Errors (502)
- Verify backend URL is reachable
- Check circuit breaker status in logs
- Confirm firewall allows connection

## Next Steps

- [ ] Enable `/generate` endpoint if vLLM backend publishes it
- [ ] Add dedicated routes for vLLM native APIs (optional performance optimization)
- [ ] Configure model federation across multiple backends
- [ ] Set up monitoring dashboards in Grafana
