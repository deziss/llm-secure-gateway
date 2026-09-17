# LLM Gateway User Guide

Welcome to the LLM Gateway! This guide covers everything you need to know about navigating the Admin UI, managing tenants (owners), securing your LLM backends, and making API requests through the gateway.

---

## 1. Authentication & Access

### Admin Dashboard Login

The gateway provides a full web interface for administration.

- **URL**: `http://localhost:6130/ui` (or your production URL)
- **Default Credentials** (configurable via `.env`):
  - Email: `admin@example.com`
  - Password: `admin`

Once logged in, you will receive a JWT token stored in an HTTP-only cookie, allowing you to access all `/admin` API routes either through the UI or directly via scripts.

### Programmatic Admin Login

If you are scripting admin actions, you can retrieve an access token via the API:

```bash
TOKEN=$(curl -s -X POST http://localhost:6130/auth/jwt/login \
  -d "username=admin@example.com&password=admin" | jq -r '.access_token')

# Use it in subsequent requests
curl -X GET http://localhost:6130/admin/backends \
  -H "Authorization: Bearer $TOKEN"
```

---

## 2. Platform Concepts

The gateway relies on three core entities to secure and route traffic: **Backends**, **Owners**, and **API Keys**.

### Backends

A Backend is an upstream LLM provider (e.g., a local Ollama instance, vLLM, llama.cpp server, or an external provider like OpenAI/Anthropic).

- You must create a backend to tell the gateway where to route traffic.
- Backends define **allowed endpoints** (whitelist) and supported **models**.
- Backends can be added dynamically without restarting the gateway.

### Owners (Tenancy)

Owners represent the logical tenants in the gateway. An Owner can be a specific User (`user`) or a broader Project scope (`project`).

- **All API Keys must belong to an Owner.**
- Owners allow you to track aggregated metrics (like token counts or Phoenix telemetry) across all keys belonging to that project/user.

### API Keys

API Keys are the tokens clients use to access the gateway.

- They are scoped with **RPM (Requests Per Minute)** limits.
- They can be restricted to specific **scopes** (e.g., `["chat", "generate"]`).
- They inherit the telemetry grouping of their parent Owner.

---

## 3. Configuring the Gateway via API

While you can perform all these actions in the Admin UI (`/ui`), below are the curl commands for automation. Ensure you have your `$TOKEN` from the login step.

### Step 3a: Create an LLM Backend

Tell the gateway where your models live. Here, we register a local Ollama instance.

```bash
curl -X POST http://localhost:6130/admin/backends \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ollama",
    "base_url": "http://host.docker.internal:11434",
    "backend_type": "ollama",
    "models": ["llama3.2:latest", "mistral:latest"],
    "allowed_endpoints": ["/api/chat", "/api/generate", "/api/tags"]
  }'
```

```bash
# Register a local llama.cpp server
curl -X POST http://localhost:6130/admin/backends \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "llamacpp-local",
    "base_url": "http://host.docker.internal:8080/v1",
    "backend_type": "llamacpp",
    "models": ["llama-3.1-8b-instruct"],
    "allowed_endpoints": ["/v1/chat/completions", "/v1/models"]
  }'
```

### Step 3b: Create an Owner

Register a tenant (e.g., a new project that needs LLM access).

```bash
curl -X POST http://localhost:6130/admin/owners \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "my-first-project",
    "name": "My First Project",
    "type": "project",
    "email": "team@example.com"
  }'
```

### Step 3c: Generate an API Key

Generate a key for the new owner. Save the resulting `api_key` securely; you cannot view it again.

```bash
curl -X POST http://localhost:6130/admin/keys \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "owner": "my-first-project",
    "scopes": ["chat", "models"],
    "rate_limit_rpm": 60
  }'
```

---

## 4. Making LLM Requests (Proxying)

Once you have an API Key (e.g., `sk-gateway-xxx...`), you can make requests to the gateway exactly as you would to the upstream provider!

For authentication, you can pass your API key in one of two ways:

- **Header**: `Authorization: Bearer sk-gateway-...`
- **Header**: `X-API-Key: sk-gateway-...`

### V2 Routing Strategies

The LLM Gateway uniquely supports two forms of routing.

#### 1. Direct Routing (`/direct/{backend_name}/...`)

Use Direct Routing when you want to explicitly target a registered backend by its exact `name` (e.g., "ollama"). This is required for endpoints that do not specify a model in a JSON payload (like `GET` requests).

```bash
# Example: List available models explicitly from your "ollama" backend
curl -X GET http://localhost:6130/direct/ollama/api/tags \
  -H "Authorization: Bearer $GATEWAY_API_KEY"
```

#### 2. Dynamic Provider Routing (`/{provider_type}/...`)

Use Dynamic Provider Routing when you want the gateway to automatically find the best backend based on the `model` specified in your JSON payload.

```bash
# Example: Standard chat request.
# The gateway reads "llama3.2:latest", finds an "ollama" type backend that supports it, and routes it.
curl -X POST http://localhost:6130/ollama/api/chat \
  -H "Authorization: Bearer $GATEWAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.2:latest",
    "messages": [
      {
        "role": "user",
        "content": "Hello! How are you?"
      }
    ]
  }'
```

> **Note:** For a full reference of Ollama endpoints mapped through the gateway, please see [`docs/gateway_ollama_endpoints.md`](docs/gateway_ollama_endpoints.md).

---

## 5. Bring Your Own Key (Provider Keys)

Instead of the gateway paying for inference or routing to local models, you can configure an Owner to bring their own public API key (e.g., an OpenAI API key).

- These keys are encrypted in the database using the `FERNET_KEY`.
- When the owner makes a request to the OpenAI endpoint, the gateway injects their specific provider key into the upstream request.

### Adding a Provider Key

```bash
curl -X POST http://localhost:6130/admin/owners/my-first-project/keys \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "openai",
    "key": "sk-proj-YOUR-OPENAI-KEY"
  }'
```

---

## 6. Telemetry & Observability

If enabled in your `.env`, all requests routed through the gateway are automatically captured as standard OpenTelemetry traces via Arize Phoenix.

- You can view the live telemetry dashboard at **`http://localhost:6006`**
- Traces capture input prompts, output responses, time-to-first-token, streaming latency, and exact token usage.
- Telemetry streams are dynamically grouped into projects matching your Owner IDs.

For deep-dive documentation on tracing, OpenInference specs, and troubleshooting metrics, please see the [`TELEMETRY_GUIDE.md`](TELEMETRY_GUIDE.md).

---

## 7. Side-by-Side Model Arena

The Model Arena allows operators and developers to compare responses and latency between two different models side-by-side using identical prompts.

- **URL**: `http://localhost:6130/admin/view/playground/compare`
- **Backend Filter**: Choose any registered backend (e.g. `vllm-42`, `ollama`) to isolate models by provider or server.
- **Searchable Model Selector**: Live instant search through model lists.
- **Metrics Evaluated**:
  - Time to First Token (TTFT)
  - Total Completion Latency
  - Token Generation Speed (tokens/sec)
  - Response text rendering with full markdown and code highlighting

---

## 8. Financial Governance & Spend Quotas

The gateway enforces strict financial governance to avoid surprise bills from upstream LLM providers.

- **URL**: `http://localhost:6130/admin/view/spend`
- **Budget Caps**: Set `monthly_budget_usd` on an Owner or API Key. When exceeded, the gateway returns HTTP `429 Too Many Requests` with a budget exhaustion message.
- **Model Cost Metering**: Built-in per-token pricing table tracks prompt and completion token expenditures by Model and Owner in real time.

---

## 9. Model Aliases & Cross-Provider Failover

Virtual model names decouple client code from specific provider infrastructure.

- **URL**: `http://localhost:6130/admin/view/aliases`
- **Virtual Aliases**: Map client model requests like `smart-model` to `llama-3.3-70b` on `vllm-42`.
- **Failover Chains**: Define prioritized fallback targets to automatically retry queries against backup models or providers on 429 rate limits or 5xx server errors.
