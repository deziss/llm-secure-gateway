# vLLM Integration & Playground Setup Analysis

**Date**: April 8, 2026  
**Status**: Exploration Complete  
**Projects Analyzed**:
- `/home/anshukushwaha/Desktop/learn/llm-secure-gateway/`
- `/home/anshukushwaha/Desktop/learn/vllm-docker/`

---

## Executive Summary

The LLM Secure Gateway provides a **multi-tenant API gateway** that acts as a central hub to manage, authenticate, and route requests to multiple LLM backends (Ollama, vLLM, OpenAI, Anthropic, etc.). The **playground** is a web-based UI that allows authenticated users to test API endpoints directly against registered backends.

**Key Integration Points**:
1. **vLLM Configuration**: OpenAI-compatible API on port 7887 (customizable)
2. **Gateway Authentication**: API key validation via database
3. **Model Federation**: Unifies models across multiple backends
4. **Rate Limiting**: Token-bucket per API key
5. **Telemetry**: OpenTelemetry + Phoenix observability

---

## 1. vLLM Docker Setup

### Configuration Files

**Location**: `/home/anshukushwaha/Desktop/learn/vllm-docker/`

#### `.env` File (vLLM Configuration)
```
PORT=7887                                    # vLLM API server port
MODEL=EleutherAI/pythia-70m                 # Model to serve (HuggingFace format)
GPU_COUNT=all                                # GPU allocation
HF_TOKEN=                                    # Hugging Face API token for gated models
DTYPE=float16                                # Data type for inference
GPU_MEM_UTIL=0.90                           # GPU memory utilization ratio
HSA_OVERRIDE_GFX_VERSION=                   # AMD GPU support override
ROCR_VISIBLE_DEVICES=                       # AMD GPU device selection
HIP_VISIBLE_DEVICES=
```

**Current Setup**:
- **Default Model**: `EleutherAI/pythia-70m` (small, CPU-friendly)
- **Port**: 7887 (exposed via docker-compose)
- **Common Alternatives**: 
  - `meta-llama/Llama-3.1-8B-Instruct`
  - `mistralai/Mistral-7B-Instruct-v0.3`

#### Docker Compose Files

**CPU Deployment** (`docker-compose.yml`):
```yaml
services:
  vllm:
    image: substratusai/vllm:v0.6.3-cpu
    container_name: vllm-server
    ports:
      - "${PORT:-8000}:8000"              # Maps to 7887 via .env PORT
    volumes:
      - ~/.cache/huggingface:/root/.cache/huggingface  # Model cache
    environment:
      - HUGGING_FACE_HUB_TOKEN=${HF_TOKEN:-}
    command: --model ${MODEL:-EleutherAI/pythia-14m} --enforce-eager
    ipc: host
```

**GPU Deployment** (`docker-compose.gpu.yml`):
```yaml
services:
  vllm:
    image: vllm/vllm-openai:latest         # NVIDIA GPU-enabled image
    container_name: vllm-server-gpu
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: ${GPU_COUNT:-all}
              capabilities: [gpu]
    ipc: host
```

### vLLM API Endpoints

```
OpenAI-Compatible Endpoints:
├── GET  /v1/models                        → List available models
├── POST /v1/chat/completions              → Chat endpoint (streaming)
├── POST /v1/completions                   → Text completion
├── POST /v1/embeddings                    → Embedding generation
└── GET  /health                           → Health check
```

**Test Command**:
```bash
curl http://localhost:7887/v1/models

curl http://localhost:7887/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "EleutherAI/pythia-70m",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
  }'
```

---

## 2. LLM Secure Gateway Architecture

### File Structure

```
llm-secure-gateway/
├── .env                                    # Environment variables (configured)
├── .env.example                            # Template
├── docker-compose.yml                      # Gateway + PostgreSQL
├── src/llm_gateway/
│   ├── main.py                             # FastAPI app initialization
│   ├── config.py                           # Configuration loader
│   ├── database.py                         # SQLAlchemy async session
│   ├── models.py                           # Pydantic + SQLAlchemy models
│   ├── middleware.py                       # Auth, CSRF, rate limit
│   ├── proxy_helpers.py                    # Circuit breaker, client pool
│   ├── routers/
│   │   ├── admin.py                        # Admin endpoints (/admin/metrics, /admin/models)
│   │   ├── backends.py                     # Backend registration (/admin/backends/*)
│   │   ├── proxy.py                        # Request forwarding (federation)
│   │   ├── v2_proxy.py                     # V2 API proxy
│   │   ├── ui.py                           # Template routes (/auth/login, /admin/dashboard, etc.)
│   │   ├── users.py                        # User management
│   │   ├── owners.py                       # Multi-tenant owners
│   │   └── settings.py                     # Admin settings UI
│   ├── services/
│   │   ├── auth_service.py                 # JWT/API key validation
│   │   ├── config_service.py               # Backend CRUD, model federation
│   │   └── federation_service.py           # Model aggregation
│   ├── auth/
│   │   ├── users.py                        # FastAPI-Users integration
│   │   ├── models.py                       # User, Role, APIKey models
│   │   └── manager.py                      # Custom auth manager
│   ├── static/
│   │   ├── js/
│   │   │   ├── playground.js               # Request builder UI
│   │   │   ├── chat-playground.js          # Chat streaming interface
│   │   │   └── embedding-playground.js     # Embedding visualization
│   │   └── css/
│   └── templates/
│       ├── base.html                       # Layout template
│       ├── playground.html                 # API playground UI
│       ├── chat_playground.html            # Chat interface
│       └── embedding_playground.html       # Embedding visualizer
├── alembic/                                # SQL migration scripts
├── pyproject.toml                          # Python dependencies
└── scripts/
    └── migrate.py                          # Database migration runner
```

### Database Schema (PostgreSQL)

**Key Tables**:
```
user (id, email, hashed_password, role, is_superuser)
apikey (id, owner_id, key_hash, rotated_at, expires_at)
llm_backend (id, name, backend_type, base_url, api_key_encrypted, models[])
owner_permission (owner_id, backend_name, allowed_endpoints[])
audit_log (id, owner_id, endpoint, timestamp, request_body)
```

**Constraints**:
- API keys → rate limited per owner (tokens/minute)
- Models → stored as `ARRAY` of strings in PostgreSQL
- Encryption → Fernet (symmetric) for backend API keys

---

## 3. Playground Integration Flow

### 3.1 Architecture Diagram

```
┌─ User (browser) ──────────────────────────────────────────────┐
│                                                                │
│  [Login/Register] → [Dashboard] → [Playground]               │
│                        ↓                                       │
│                  Session Cookie                               │
│                                                                │
└─────────────────────────────┬──────────────────────────────────┘
                              │
                    GET /admin/view/playground
                              ↓
┌─ LLM Gateway (FastAPI) ────────────────────────────────────────┐
│                                                                │
│  ┌─ ui.py (router) ──────────────────────────────────────────┐│
│  │                                                            ││
│  │  @router.get("/admin/view/playground")                   ││
│  │  async def playground_page(request, user):               ││
│  │    return templates.TemplateResponse(                    ││
│  │      request, "playground.html",                        ││
│  │      context=get_template_context(user)                 ││
│  │    )                                                      ││
│  └────────────────────────────────────────────────────────────┘│
│                              ↓                                 │
│  ┌─ playground.html ─────────────────────────────────────────┐│
│  │                                                            ││
│  │  Jinja2 Template ({user, user_role, base_url})           ││
│  │  Static form + JavaScript                                ││
│  │                                                            ││
│  │  <form id="apiForm">                                      ││
│  │    - Mode Toggle: [Standard] [Direct]                    ││
│  │    - Provider: [Ollama] [OpenAI] [vLLM] [...]            ││
│  │    - Backend: [Dropdown - populated from API]            ││
│  │    - Endpoint: [Dropdown - dynamic]                      ││
│  │    - HTTP Method: [GET, POST]                            ││
│  │    - Headers: [API Key, Content-Type]                    ││
│  │    - Body: [JSON Editor]                                 ││
│  │  </form>                                                  ││
│  │                                                            ││
│  └────────────────────────────────────────────────────────────┘│
│                              ↓                                 │
│  ┌─ playground.js ───────────────────────────────────────────┐│
│  │                                                            ││
│  │  DOM Ready:                                               ││
│  │  1. fetch(BASE_URL + "/admin/backends")                  ││
│  │     → Populates provider dropdowns                       ││
│  │     → Populates direct backend selector                  ││
│  │     → Extracts backend_type per backend                  ││
│  │                                                            ││
│  │  2. updateEndpointDropdown()                             ││
│  │     → Uses ENDPOINTS_BY_TYPE map                         ││
│  │     → Routes based on backend_type                       ││
│  │                                                            ││
│  │  3. updateModelDropdown()                                ││
│  │     → Filters models by provider/backend                 ││
│  │     → Deduplicates across multiple backends              ││
│  │                                                            ││
│  │  4. On Form Submit:                                      ││
│  │     a) Build request (method, endpoint, headers, body)  ││
│  │     b) POST to /admin/[mode]/completions, etc.          ││
│  │     c) Streaming response handling                       ││
│  └────────────────────────────────────────────────────────────┘│
│                              ↓                                 │
│  ┌─ backends.py (router) ────────────────────────────────────┐│
│  │                                                            ││
│  │  GET /admin/backends                                      ││
│  │    ├─ AuthMiddleware (API key validation)               ││
│  │    ├─ Check user auth status                             ││
│  │    └─ Return: [{name, backend_type, models, ...}, ...]  ││
│  │                                                            ││
│  │  Example Response:                                        ││
│  │  [                                                        ││
│  │    {                                                      ││
│  │      "name": "ollama-local",                             ││
│  │      "backend_type": "ollama",                           ││
│  │      "base_url": "http://ollama:11434",                 ││
│  │      "models": ["llama2", "mistral"],                   ││
│  │      "created_at": "2026-04-08T...",                    ││
│  │    },                                                     ││
│  │    {                                                      ││
│  │      "name": "vllm-primary",                             ││
│  │      "backend_type": "vllm",                             ││
│  │      "base_url": "http://vllm:8000",                    ││
│  │      "models": ["EleutherAI/pythia-70m"],               ││
│  │      "created_at": "2026-04-08T...",                    ││
│  │    }                                                      ││
│  │  ]                                                        ││
│  │                                                            ││
│  └────────────────────────────────────────────────────────────┘│
│                              ↓                                 │
│  ┌─ Proxy Routes (proxy.py, v2_proxy.py) ───────────────────┐│
│  │                                                            ││
│  │  POST /api/chat                    (Ollama)              ││
│  │  POST /v1/chat/completions         (OpenAI-compatible)  ││
│  │  GET  /v1/models                   (Model listing)       ││
│  │  POST /v1/completions              (Text completion)     ││
│  │  POST /direct/{backend}/v1/chat... (Specific backend)    ││
│  │                                                            ││
│  │  Request Flow:                                            ││
│  │  1. get_owner_id_from_request()     → From API key       ││
│  │  2. apply_rate_limit()              → RPM check          ││
│  │  3. check_owner_permissions()       → Endpoint whitelist ││
│  │  4. build_backend_auth_headers()    → Add backend API key││
│  │  5. get_target_backend()            → Select backend     ││
│  │  6. try_backend_with_fallback()     → Send + fallback    ││
│  │  7. stream_with_telemetry()         → OpenTelemetry log  ││
│  │  8. Return StreamingResponse        → SSE or JSON        ││
│  │                                                            ││
│  └────────────────────────────────────────────────────────────┘│
│                              ↓                                 │
└──────────────────────────────┬───────────────────────────────────┘
                              │
                ┌─────────────┼─────────────┐
                │             │             │
         [vLLM Backend]   [Ollama Backend] [OpenAI API]
        (localhost:7887)  (localhost:11434) (api.openai.com)
```

### 3.2 Playground Page Routes

| Route | Purpose | Auth |
|-------|---------|------|
| `GET /admin/view/playground` | API request builder | User |
| `GET /admin/view/playground/chat` | Chat streaming UI | User |
| `GET /admin/view/playground/embed` | Embedding visualizer | User |

### 3.3 JavaScript State Management

**playground.js** maintains:
```javascript
let routingMode = "standard";           // "standard" or "direct"
let backends = [];                      // Populated from /admin/backends

// ENDPOINTS_BY_TYPE - maps backend type to available endpoints
const ENDPOINTS_BY_TYPE = {
  ollama: [
    { value: "api/chat", label: "api/chat (Chat)" },
    { value: "api/generate", label: "api/generate (Generate)" },
    { value: "api/tags", label: "api/tags (List Models)" },
  ],
  vllm: [
    { value: "v1/chat/completions", label: "v1/chat/completions" },
    { value: "v1/completions", label: "v1/completions" },
    { value: "v1/models", label: "v1/models" },
  ],
  openai: [
    { value: "v1/chat/completions", label: "..." },
    { value: "v1/embeddings", label: "..." },
  ],
  // ... other providers
};
```

**Key Functions**:
```javascript
getCurrentBackendType()      // Extracts type from "standard" or "direct" mode
updateEndpointDropdown()     // Populates endpoints based on type
updateModelDropdown()        // Filters models from backends array
updateRequestPreview()       // Shows curl-like preview
submitRequest()              // Sends to /admin/... or /direct/...
```

---

## 4. API Key & Authentication Flow

### 4.1 User Login Process

```
[Browser] → POST /auth/login
              ├─ Credential validation (bcrypt)
              ├─ JWT token generation
              └─ HttpOnly cookie set: fastapi-users-SESSION

[Browser] → GET /admin/view/playground
              ├─ AuthMiddleware extracts cookie
              ├─ Validates JWT
              └─ Renders playground.html with user context
```

### 4.2 Backend API Key Management

**Encryption**:
```python
from cryptography.fernet import Fernet

ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY")  # Base64
cipher = Fernet(ENCRYPTION_KEY.encode())

# Store encrypted
encrypted_key = cipher.encrypt(backend.api_key.encode()).decode()

# Retrieve decrypted
decrypted_key = cipher.decrypt(encrypted_key.encode()).decode()
```

**Rate Limiting**:
```python
def apply_rate_limit(request):
    owner_id = get_owner_id_from_request(request)
    key = f"ratelimit:{owner_id}"
    # Token bucket per owner
    # Default: Allow up to RPM (requests per minute) tokens
    # Each request costs 1 token
```

---

## 5. Model Fetching & Synchronization

### 5.1 Backend Model Sync Endpoint

**Route**: `POST /admin/backends/{name}/sync-models`

```
┌─ LLM Gateway ─────────────────────────────────────────┐
│                                                       │
│  sync_backend_models(name: str):                     │
│  ├─ Get backend config (URL, API key, type)         │
│  ├─ Based on backend_type:                          │
│  │  ├─ ollama    → GET {base_url}/api/tags          │
│  │  └─ vllm      → GET {base_url}/v1/models         │
│  ├─ Parse response:                                  │
│  │  ├─ ollama    → Extract models[].name            │
│  │  └─ vllm      → Extract data[].id                │
│  ├─ Update PostgreSQL:                               │
│  │  UPDATE llm_backend SET models = {...}           │
│  └─ Return:                                          │
│     {                                                │
│       "backend": "vllm-primary",                    │
│       "synced": true,                                │
│       "models": ["EleutherAI/pythia-70m"],          │
│       "count": 1                                     │
│     }                                                │
│                                                       │
└───────────────────────────────────────────────────────┘
```

**vLLM-Specific**:
```python
# Request
GET http://localhost:7887/v1/models
Authorization: Bearer <optional-api-key>

# Response
{
  "object": "list",
  "data": [
    {
      "id": "EleutherAI/pythia-70m",
      "object": "model",
      "created": 1712590800,
      "owned_by": "vllm"
    }
  ]
}
```

### 5.2 Model Federation

If `ENABLE_MODEL_FEDERATION=true`, the gateway provides aggregated endpoints:

```python
@router.get("/api/tags")
async def aggregate_ollama_tags():
    # Returns unified model list across ALL backends
    # Deduplicates: models_set = set()
    # Simulates a single large provider

@router.get("/v1/models")
async def aggregate_openai_models():
    # Same for OpenAI-compatible providers
```

---

## 6. Configuration Files Review

### 6.1 Current .env (llm-secure-gateway)

```
# SMTP Settings (Gmail for alerts)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=kushawahaanshu8858@gmail.com
SMTP_PASSWORD=[REDACTED]   # Needs to be filled for email alerts
EMAILS_FROM_EMAIL=kushawahaanshu8858@gmail.com
ADMIN_EMAIL=kushawahaanshu8858@gmail.com

# Authentication & Encryption
AUTH_SECRET=gw-secret-8f6c9b6b6d8c4f2fb6b5e2d7a4f3b9e1
ENCRYPTION_KEY=DTPU6-hS9dJ_g0-d5K0y5fU9xJ0h5dS9_f5K0y5fU9w=

# Database
DATABASE_URL=postgresql+asyncpg://gateway:password@postgres:5432/gateway_db

# Frontend
BASE_URL=http://localhost:6130

# Registration
REQUIRE_INVITE=false   # Allow open registration

# Phoenix Observability
PHOENIX_ENABLE_AUTH=true
PHOENIX_ENABLE_LOCAL_AUTH=true
PHOENIX_SECRET=[SET]
PHOENIX_ADMIN_SECRET=[SET]
PHOENIX_API_KEY=[JWT-SIGNED]
PHOENIX_COLLECTOR_ENDPOINT=http://host.docker.internal:6006

# Admin Defaults
DEFAULT_ADMIN_EMAIL=admin@example.com
DEFAULT_ADMIN_PASSWORD=admin123   # Change in production!
```

**Key Issues**:
- ✅ ENCRYPTION_KEY is set (Fernet key)
- ✅ AUTH_SECRET is set
- ⚠️ SMTP_PASSWORD not filled for email alerts
- ⚠️ DEFAULT_ADMIN_PASSWORD should be changed
- ✅ DATABASE_URL points to postgres service

### 6.2 Current .env (vllm-docker)

```
PORT=7887
MODEL=EleutherAI/pythia-70m         # Small model for CPU testing
GPU_COUNT=all
HF_TOKEN=                           # Empty (model is public)
DTYPE=float16
GPU_MEM_UTIL=0.90
```

**Key Issues**:
- ✅ PORT is unique (7887, not conflicting with gateway 6130)
- ✅ Model is public (no HF_TOKEN needed)
- ✅ CPU-friendly model chosen

---

## 7. Connection & Request Validation

### 7.1 Playground → vLLM Request Flow

```
[Browser]
  │ (Standard Mode)
  │ Provider: "vllm"
  │ Endpoint: "v1/chat/completions"
  │ Headers: {Authorization: "Bearer <api-key>"}
  │ Body: {model: "EleutherAI/pythia-70m", messages: [...]}
  ↓
[LLM Gateway - playground.js]
  POST /v1/chat/completions
  (with session cookie)
  ↓
[LLM Gateway - proxy.py]
  │ 1. AuthMiddleware validates session
  │ 2. get_owner_id_from_request()          → owner_123
  │ 3. apply_rate_limit()                   → check tokens
  │ 4. check_owner_permissions()            → v1/chat/completions allowed?
  │ 5. get_target_backend("vllm")           → vllm-primary
  │ 6. build_backend_auth_headers()         → Add vllm api_key (if set)
  │ 7. try_backend_with_fallback()
  │    ├─ GET vllm-primary base_url         → http://localhost:7887
  │    └─ Circuit breaker status?           → CLOSED? continue : fallback
  │ 8. Forward request:
  │    POST http://localhost:7887/v1/chat/completions
  │      {
  │        model: "EleutherAI/pythia-70m",
  │        messages: [...],
  │        max_tokens: 100,
  │        stream: true
  │      }
  ↓
[vLLM - localhost:7887]
  │ 1. Parse request
  │ 2. Load model from cache
  │ 3. Tokenize input
  │ 4. Forward pass (CPU/GPU)
  │ 5. Streaming output (Server-Sent Events)
  ↓
[LLM Gateway - stream_with_telemetry()]
  │ 1. Stream SSE chunks from vLLM
  │ 2. Record telemetry:
  │    ├─ tokens_in, tokens_out
  │    ├─ latency_ms
  │    ├─ provider: "vllm"
  │    └─ model: "EleutherAI/pythia-70m"
  │ 3. Send to Phoenix (http://localhost:6006)
  ↓
[Browser - playground.js]
  │ 1. Receive streaming chunks
  │ 2. Append to response textarea
  │ 3. Update latency display
```

### 7.2 Direct Backend Mode

```
[Browser]
  │ (Direct Mode)
  │ Backend: "vllm-primary" (dropdown)
  │ Endpoint: "v1/chat/completions"
  ↓
[LLM Gateway - playground.js]
  POST /direct/vllm-primary/v1/chat/completions
  (routes to specific database entry)
  ↓
[LLM Gateway - v2_proxy.py]
  │ Similar flow as above
  │ Gets backend from: backends.find(b => b.name == "vllm-primary")
  │ Uses: b.base_url, b.api_key, b.backend_type
  ↓
[vLLM]
  (same as above)
```

---

## 8. Environment Variables Summary

### vllm-docker/.env
```
PORT=7887                    # Expose on localhost:7887
MODEL=EleutherAI/pythia-70m # Model name (HuggingFace format)
GPU_COUNT=all               # All available GPUs
HF_TOKEN=                   # Hugging Face token (if needed)
DTYPE=float16              # Data type
GPU_MEM_UTIL=0.90          # GPU memory fraction
```

### llm-secure-gateway/.env
```
DATABASE_URL=postgresql+asyncpg://gateway:password@postgres:5432/gateway_db
ENCRYPTION_KEY=<Fernet-key>        # For encrypting backend API keys
AUTH_SECRET=<random-string>        # For JWT signing
BASE_URL=http://localhost:6130     # Frontend URL
DEFAULT_ADMIN_EMAIL=admin@example.com
DEFAULT_ADMIN_PASSWORD=admin123
PHOENIX_COLLECTOR_ENDPOINT=http://host.docker.internal:6006
```

---

## 9. Docker Compose Integration

### How They Connect

**llm-secure-gateway/docker-compose.yml** services:
```
- gateway:    localhost:6130 (Jinja2 templates, FastAPI proxy)
- postgres:   Not exposed (internal only)
- migrate:    One-shot database setup

(No vLLM defined here)
```

**vllm-docker/docker-compose.yml** services:
```
- vllm:       localhost:7887 (OpenAI-compatible API)

(No gateway defined here)
```

**Integration**: Gateway configured to point to vLLM via IP
```
Backend URL: http://localhost:7887
(From admin UI, manually add as "/admin/backends")

OR via environment variable for docker-compose internal networking:
http://vllm:8000 (if using docker network)
```

---

## 10. Troubleshooting Checklist

### Backend Registration
- [ ] vLLM running on correct port (7887)
- [ ] Gateway can reach vLLM (telnet/curl test)
- [ ] `/v1/models` endpoint returns models list
- [ ] Admin user can see "vllm-primary" in `/admin/view/backends`

### Model Fetching
- [ ] POST `/admin/backends/vllm-primary/sync-models` succeeds
- [ ] Models appear in dropdown on playground
- [ ] No 401/403 errors in logs

### Playground Requests
- [ ] User authenticated (session cookie set)
- [ ] Can submit request without errors
- [ ] Response appears in textarea (not streaming?)
- [ ] Telemetry logged to Phoenix (optional)

### API Key Validation
- [ ] Encryption/decryption works (ENCRYPTION_KEY valid Fernet)
- [ ] No "Invalid API key" errors
- [ ] Rate limits respected

---

## 11. Key Files Reference

| File | Purpose |
|------|---------|
| `llm-secure-gateway/.env` | Gateway configuration (DB, encryption, auth) |
| `llm-secure-gateway/docker-compose.yml` | Gateway + PostgreSQL stack |
| `llm-secure-gateway/src/llm_gateway/routers/backends.py` | Backend CRUD + sync-models |
| `llm-secure-gateway/src/llm_gateway/routers/proxy.py` | Request forwarding logic |
| `llm-secure-gateway/src/llm_gateway/templates/playground.html` | UI template |
| `llm-secure-gateway/src/llm_gateway/static/js/playground.js` | Frontend logic |
| `vllm-docker/.env` | vLLM configuration |
| `vllm-docker/docker-compose.yml` | vLLM container definition |

---

## 12. Next Steps

1. **Verify vLLM is running**:
   ```bash
   curl http://localhost:7887/v1/models
   ```

2. **Register vLLM backend in gateway**:
   - Login to `http://localhost:6130/auth/login`
   - Navigate to `/admin/view/backends`
   - Click "Register Backend"
   - Name: `vllm-primary`
   - Type: `vllm`
   - URL: `http://localhost:7887`

3. **Sync models**:
   - Click "Sync Models" on vllm-primary
   - Verify `EleutherAI/pythia-70m` appears

4. **Test from playground**:
   - Go to `/admin/view/playground`
   - Select Provider: vLLM
   - Endpoint: v1/chat/completions
   - Submit test request

---

**End of Analysis**
