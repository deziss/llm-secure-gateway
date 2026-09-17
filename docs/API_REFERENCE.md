# API Reference

Base URL: `http://localhost:6130`

Interactive docs (Swagger UI): `GET /docs`

---

## Authentication

All proxy and admin API endpoints require one of:

| Method | Header / Cookie | Notes |
|---|---|---|
| API Key | `Authorization: Bearer sk-gateway-...` | Primary method for programmatic access |
| API Key | `x-api-key: sk-gateway-...` | Alternative header |
| Cookie | `fastapiusersauth=<token>` | Set automatically after browser login |
| SPIFFE | `X-Spiffe-ID: spiffe://...` | Zero-trust mesh workloads only |

Unauthenticated requests return `401 Unauthorized`.

---

## Pagination

All list endpoints support pagination via query parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `skip` | int | `0` | Number of records to skip (min: 0) |
| `limit` | int | `50` | Max records to return (min: 1, max: 1000) |

Example: `GET /admin/backends?skip=0&limit=20`

---

## Auth Endpoints

### `POST /auth/jwt/login`
Obtain a Bearer token.

**Request** (`application/x-www-form-urlencoded`)
```
username=admin@example.com&password=yourpassword
```

**Response `200`**
```json
{ "access_token": "eyJ...", "token_type": "bearer" }
```

### `POST /auth/jwt/logout`
Invalidate the current Bearer token.

### `POST /auth/cookie/login`
Obtain a session cookie. Used by the Admin UI.

**Request** (`application/x-www-form-urlencoded`)
```
username=admin@example.com&password=yourpassword
```

**Response `204`** — Sets `fastapiusersauth` cookie.

### `POST /auth/cookie/logout`
Clear the session cookie.

### `POST /auth/register`
Register a new user. Requires a valid invite code if `REQUIRE_INVITE=true` (system setting).

**Request**
```json
{
  "email": "dev@example.com",
  "password": "securepassword",
  "invite_code": "abc123"
}
```

**Response `201`**
```json
{
  "id": "uuid",
  "email": "dev@example.com",
  "is_active": true,
  "is_superuser": false,
  "is_verified": false
}
```

### `POST /auth/forgot-password`
Trigger a password reset email.

**Request**
```json
{ "email": "user@example.com" }
```

### `POST /auth/reset-password`
Reset password using a token from the email.

**Request**
```json
{ "token": "reset-token", "password": "newpassword" }
```

---

## Admin — System

> All `/admin/*` routes require an authenticated session (cookie or Bearer). Most require `role=ADMIN` or `role=MANAGER`.

### `GET /health`
Liveness probe. No authentication required.

**Response `200`**
```json
{ "status": "ok", "service": "llm-gateway" }
```

### `GET /admin/dashboard`
Returns admin dashboard HTML (UI endpoint).

### `GET /admin/active-ips`
List IP addresses active within the last 5 minutes.

**Response `200`**
```json
[
  { "ip": "192.168.1.10", "last_seen_seconds_ago": 12 }
]
```

### `GET /admin/metrics`
System metrics snapshot.

**Response `200`**
```json
{
  "active_ips": [...],
  "total_active_ips": 3,
  "total_backends": 2,
  "total_users": 5
}
```

---

## Backends

### `GET /admin/backends`
List all registered LLM backends.

**Response `200`**
```json
[
  {
    "name": "local-ollama",
    "base_url": "http://ollama:11434",
    "backend_type": "ollama",  // "ollama" | "vllm" | "llamacpp" | "openai" | "anthropic" | "google" | "groq" | "custom"
    "models": ["llama3.2:latest", "mistral:latest"],
    "fallback_urls": [],
    "allowed_endpoints": ["api/chat", "api/generate", "v1/chat/completions"],
    "created_at": "2026-03-01T10:00:00"
  }
]
```

### `POST /admin/backends`
Register a new LLM backend.

**Request**
```json
{
  "name": "local-ollama",
  "base_url": "http://ollama:11434",
  "backend_type": "ollama",
  "api_key": null,
  "models": ["llama3.2:latest"],
  "fallback_urls": ["http://backup-ollama:11434"],
  "allowed_endpoints": ["api/chat", "api/generate", "v1/chat/completions"]
}
```

**Response `200`** — Returns the created backend object.

### `GET /admin/backends/{name}`
Get a single backend by name.

### `PATCH /admin/backends/{name}`
Update backend fields. Only provided fields are changed.

**Request**
```json
{
  "models": ["llama3.2:latest", "gemma3:4b"],
  "fallback_urls": ["http://backup:11434"]
}
```

### `DELETE /admin/backends/{name}`
Delete a backend and its associated permissions.

---

## Owners

### `GET /admin/owners`
List all owners.

**Response `200`**
```json
[
  {
    "id": "user:alice",
    "type": "user",
    "name": "Alice",
    "email": "alice@example.com",
    "block_endpoints": true,
    "is_active": true,
    "max_keys": 5,
    "created_at": "2026-03-01T10:00:00"
  }
]
```

### `POST /admin/owners`
Create a new owner.

**Request**
```json
{
  "id": "user:alice",
  "type": "user",
  "name": "Alice",
  "email": "alice@example.com",
  "block_endpoints": true,
  "max_keys": 5
}
```

### `GET /admin/owners/{owner_id}`
Get a single owner with their API keys.

### `PUT /admin/owners/{owner_id}`
Update owner fields.

### `DELETE /admin/owners/{owner_id}`
Delete owner and cascade-delete all their API keys, provider keys, and permissions.

### `POST /admin/owners/{owner_id}/api-keys`
Create an API key for an owner.

**Request**
```json
{
  "owner": "user:alice",
  "scopes": ["chat"],
  "expires_at": null,
  "rate_limit_rpm": 60
}
```

**Response `200`**
```json
{
  "key": "sk-gateway-<random>",
  "prefix": "sk-gateway-KpX...",
  "owner_id": "user:alice",
  "scopes": ["chat"],
  "rate_limit_rpm": 60
}
```

> **Important**: The full key is only returned once. Store it immediately.

### `DELETE /admin/owners/{owner_id}/api-keys/{prefix}`
Revoke an API key by its prefix.

### `POST /admin/owners/{owner_id}/provider-keys`
Store an encrypted provider API key for an owner.

**Request**
```json
{
  "provider": "openai",
  "key": "sk-..."
}
```

### `GET /admin/owners/{owner_id}/provider-keys`
List provider key metadata (provider IDs only — keys are never returned).

### `POST /admin/owners/{owner_id}/permissions`
Grant an owner access to a backend.

**Request**
```json
{
  "backend_name": "local-ollama",
  "allowed_models": ["*"],
  "allowed_endpoints": ["*"]
}
```

Use `["*"]` to allow all models or endpoints. Use specific values to restrict:
```json
{
  "allowed_models": ["llama3.2:latest"],
  "allowed_endpoints": ["v1/chat/completions"]
}
```

### `DELETE /admin/owners/{owner_id}/permissions/{backend_name}`
Revoke an owner's access to a backend.

---

## Users

### `GET /admin/users`
List all registered users. Admin/Manager only.

### `GET /admin/users/me`
Get the currently authenticated user.

### `PATCH /admin/users/{user_id}`
Update user fields (role, is_active, etc.). Admin only.

**Request**
```json
{ "role": "DEVELOPER", "is_active": true }
```

### `POST /admin/users/{user_id}/reset-password`
Reset user password. Admin only.

### `DELETE /admin/users/{user_id}`
Delete a user account. Admin only.

---

## Settings

### `GET /admin/settings`
Get all settings. Returns a list of setting objects.

**Response `200`**
```json
[
  { "key": "REQUIRE_INVITE", "value": "false" },
  { "key": "ENABLE_MODEL_FEDERATION", "value": "false" },
  { "key": "ENABLE_EXPERIMENTAL_ROUTING", "value": "false" },
  { "key": "ENABLE_RETRY_BACKOFF", "value": "false" }
]
```

### `PATCH /admin/settings/{key}`
Update a setting.

**Request**
```json
{ "value": "true" }
```

### System Setting Keys

| Key | Values | Description |
|---|---|---|
| `REQUIRE_INVITE` | `true` / `false` | Gate registration behind invite codes |
| `ENABLE_MODEL_FEDERATION` | `true` / `false` | Enable federated model list aggregation |
| `ENABLE_EXPERIMENTAL_ROUTING` | `true` / `false` | Enable random load-balancing across matching backends |
| `ENABLE_RETRY_BACKOFF` | `true` / `false` | Retry failed backend requests with exponential backoff |

### `GET /admin/invites`
List all invite codes and their usage status.

### `POST /admin/invites`
Create a new invite code. Admin only.

**Response `200`**
```json
{ "code": "abc123xyz" }
```

### `DELETE /admin/invites/{code}`
Delete an invite code.

---

## Proxy — V1 (Model-Based Routing)

All V1 routes require API key authentication.

### `GET /api/tags`
Federated Ollama model list (requires `ENABLE_MODEL_FEDERATION=true`).

**Response `200`**
```json
{
  "models": [
    {
      "name": "llama3.2:latest",
      "model": "llama3.2:latest",
      "size": 0,
      "digest": "federated",
      "details": { "family": "federated" }
    }
  ]
}
```

### `GET /api/version`
Ollama-compatible version endpoint for CLI, OpenWebUI, and client handshakes.

**Response `200`**
```json
{
  "version": "0.9.0"
}
```

### `GET /v1/models`
Federated OpenAI-compatible model list (requires `ENABLE_MODEL_FEDERATION=true`).

**Response `200`**
```json
{
  "object": "list",
  "data": [
    { "id": "llama3.2:latest", "object": "model", "owned_by": "local-ollama" }
  ]
}
```

### `ANY /{path}`
Proxy any request to the appropriate backend based on the `model` field in the request body.

**Example**
```bash
curl -X POST http://localhost:6130/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.2:latest",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true
  }'
```

**Error responses**

| Code | Condition |
|---|---|
| `400` | `model` field missing for `/chat/completions` POST |
| `401` | Missing or invalid authentication |
| `403` | Owner has no permission for the backend, or endpoint is blocked |
| `429` | Rate limit exceeded |
| `502` | All backend URLs failed (connection error) |
| `503` | No backends configured |

---

## Proxy — V2 (Direct & Provider Routing)

### `ANY /direct/{backend_name}/{path}`
Route directly to a named backend. Bypasses model-based selection.

```bash
# List models from a specific Ollama backend
curl -X GET http://localhost:6130/direct/local-ollama/api/tags \
  -H "Authorization: Bearer sk-gateway-..."

# Chat via a specific backend
curl -X POST http://localhost:6130/direct/local-ollama/api/chat \
  -H "Authorization: Bearer sk-gateway-..." \
  -d '{"model": "llama3.2:latest", "messages": [...]}'
```

**Error responses** — same as V1 proxy, plus:

| Code | Condition |
|---|---|
| `404` | Backend name not found |

### `GET /provider/{provider}/api/tags`
Federated Ollama model list filtered by provider type (requires `ENABLE_MODEL_FEDERATION=true`).

### `GET /provider/{provider}/v1/models`
Federated OpenAI model list filtered by provider type (requires `ENABLE_MODEL_FEDERATION=true`).

### `ANY /provider/{provider}/{path}`
Route to the best available backend of the given provider type.

```bash
# OpenAI-compatible chat via any OpenAI backend
curl -X POST http://localhost:6130/provider/openai/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-..." \
  -d '{"model": "gpt-4o", "messages": [...]}'

# Ollama chat
curl -X POST http://localhost:6130/provider/ollama/api/chat \
  -H "Authorization: Bearer sk-gateway-..." \
  -d '{"model": "llama3.2:latest", "messages": [...]}'
```

**Valid provider values**: `ollama`, `vllm`, `openai`, `anthropic`, `google`, `groq`, `custom`

---

## Error Response Format

All errors return JSON:

```json
{ "detail": "Human-readable error message" }
```

| Code | Meaning |
|---|---|
| `400` | Bad request (missing required field) |
| `401` | Missing or invalid authentication |
| `403` | Forbidden — permission check failed or endpoint blocked |
| `404` | Resource not found |
| `409` | Conflict (e.g. owner ID already exists) |
| `422` | Validation error (Pydantic) |
| `429` | Rate limit exceeded |
| `500` | Internal server error |
| `502` | All backend URLs failed |
| `503` | No backends configured |

---

## Rate Limiting

Rate limits are enforced per API key using a token-bucket algorithm.

- Default: `60` requests/minute (configurable per key via `rate_limit_rpm`)
- Set `rate_limit_rpm=0` to disable limits for a key
- Exceeded requests return `429` with the response body: `{"detail": "Rate limit exceeded"}`

Configure per key at creation time:
```json
{ "owner": "user:alice", "scopes": ["chat"], "rate_limit_rpm": 120 }
```

---

## CSRF Protection

Browser-based requests to `/admin/*` and `/auth/*` are protected by double-submit cookie CSRF. The gateway sets a `csrf_token` cookie on GET requests.

For state-changing requests (POST, PUT, PATCH, DELETE), include the token:

```
X-CSRF-Token: <value of csrf_token cookie>
```

API key and Bearer token authenticated requests are exempt from CSRF validation.
