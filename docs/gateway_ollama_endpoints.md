# Ollama API Endpoints via Secure Gateway

This document provides `curl` examples for all standard Ollama endpoints, routed through the **LLM Secure Gateway** running on `http://localhost:6130`.

> **Dynamic Routing Note:** The gateway automatically injects credentials and routes requests based on the `model` parameter. For endpoints without a `model` payload (like `api/tags`), use the Direct or Provider routing path: `http://localhost:6130/direct/<backend-name>/api/tags` or `http://localhost:6130/provider/ollama/api/tags`. See the **[User Guide](../USER_GUIDE.md#v2-routing-strategies)** for a detailed explanation of routing.

> **Danger-Block Note:** The gateway blocks `/api/pull`, `/api/delete`, `/api/push`, and `/api/create` by default for all owners with `block_endpoints=true` (the default). Admin UI users bypass this block. To allow these endpoints for an owner, set `block_endpoints=false` via the Admin API.

To make the snippets cleaner to copy/paste, export your gateway key first:

```bash
export GATEWAY_KEY="sk-gateway-oKm2pDIIPdvSaVd07hv-UpBOwAF6enZFsGeaC-4Zk0A"
```

---

## 1. Chat Completion (`/api/chat`)

Generate the next message in a chat with a provided model.

```bash
curl -X POST http://localhost:6130/api/chat \
  -H "Authorization: Bearer $GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.2:1b",
    "messages": [
      {
        "role": "user",
        "content": "Hello from the gateway!"
      }
    ],
    "stream": false
  }'
```

---

## 2. Generate a Completion (`/api/generate`)

Generate a response for a given prompt (raw text generation, not chat).

```bash
curl -X POST http://localhost:6130/api/generate \
  -H "Authorization: Bearer $GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.2:1b",
    "prompt": "Why is the sky blue?",
    "stream": false
  }'
```

---

## 3. Generate Embeddings (`/api/embeddings`)

Generate embeddings from a model.

```bash
curl -X POST http://localhost:6130/api/embeddings \
  -H "Authorization: Bearer $GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.2:1b",
    "prompt": "The quick brown fox jumps over the lazy dog."
  }'
```

---

## 4. List Models (`/api/tags`)

List models that are available locally. _(Uses Direct routing since there's no body payload)._

```bash
# Using Direct Routing to a specific backend
curl -X GET http://localhost:6130/direct/local-ollama/api/tags \
  -H "Authorization: Bearer $GATEWAY_KEY"

# OR Using Provider Routing
curl -X GET http://localhost:6130/provider/ollama/api/tags \
  -H "Authorization: Bearer $GATEWAY_KEY"
```

---

## 5. Show Model Information (`/api/show`)

Show information about a model including details, modelfile, template, parameters, license, and system prompt.

```bash
curl -X POST http://localhost:6130/api/show \
  -H "Authorization: Bearer $GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.2:1b"
  }'
```

---

## 6. Pull a Model (`/api/pull`)

Download a model from the ollama library.

```bash
curl -X POST http://localhost:6130/api/pull \
  -H "Authorization: Bearer $GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mistral:latest",
    "stream": false
  }'
```

---

## 7. Create a Model (`/api/create`)

Create a model from a Modelfile.

```bash
curl -X POST http://localhost:6130/api/create \
  -H "Authorization: Bearer $GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mario",
    "modelfile": "FROM llama3.2:1b\nSYSTEM You are mario from super mario bros."
  }'
```

---

## 8. Delete a Model (`/api/delete`)

Delete a model and its data.

```bash
curl -X DELETE http://localhost:6130/api/delete \
  -H "Authorization: Bearer $GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mario"
  }'
```

---

## 9. Copy a Model (`/api/copy`)

Creates a model with another name from an existing model.
_(Requires Direct/Provider routing since source/destination aren't standard "model" fields the gateway proxy expects)._

```bash
curl -X POST http://localhost:6130/direct/local-ollama/api/copy \
  -H "Authorization: Bearer $GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "llama3.2:1b",
    "destination": "llama3-copy"
  }'
```

---

## 10. Check Running Models (`/api/ps`)

List models that are currently loaded into memory. _(Uses Direct routing)._

```bash
curl -X GET http://localhost:6130/direct/local-ollama/api/ps \
  -H "Authorization: Bearer $GATEWAY_KEY"
```
