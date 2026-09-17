# Upgrade Guide

## Upgrading to v0.5.4 & PostgreSQL 18

LLM Secure Gateway v0.5.4 upgrades PostgreSQL from version 15 to **PostgreSQL 18** (`postgres:18-alpine`).

> **Note on PG18 Storage Layout**: PostgreSQL 18 uses a major-version-specific directory layout (`/var/lib/postgresql/18/docker`). The container volume must be mounted at `/var/lib/postgresql` (instead of the legacy `/var/lib/postgresql/data`).

### Automated Upgrade (PG15 -> PG18)

Run the automated upgrade script:
```bash
./upgrade_pg_15_to_18.sh
```

### Manual Upgrade Steps

1. **Backup existing PG15 database**:
   ```bash
   docker exec -t llm-gateway-postgres pg_dump -U gateway -d gateway_db -c -O -x > gateway_db_backup.sql
   ```

2. **Stop existing containers**:
   ```bash
   docker compose down
   ```

3. **Verify docker-compose.yml uses postgres:18-alpine and postgres18_data**:
   ```yaml
   postgres:
     image: postgres:18-alpine
     volumes:
       - postgres18_data:/var/lib/postgresql
   ```

4. **Start PostgreSQL 18 and apply schema migrations**:
   ```bash
   docker compose up -d postgres
   docker compose run --rm migrate
   ```

5. **(Optional) Restore backed-up data**:
   ```bash
   cat gateway_db_backup.sql | docker exec -i llm-gateway-postgres psql -U gateway -d gateway_db
   ```

6. **Start all services**:
   ```bash
   docker compose up -d
   ```

---

## Upgrading from `main-auth` branch to v0.5.0

If you are running the `main-auth` branch (where tables were created by `create_all`, no Alembic history), follow these steps.

### Prerequisites

```bash
# 1. Back up your database
docker exec llm-gateway-postgres pg_dump -U gateway gateway_db > backup_main_auth.sql

# 2. Note your current API keys and owners
docker exec llm-gateway-postgres psql -U gateway gateway_db -c "SELECT prefix, owner FROM apikey;"
docker exec llm-gateway-postgres psql -U gateway gateway_db -c "SELECT id, name FROM owner;"
```

### Step 1: Switch to v0.5.0 branch

```bash
git fetch origin
git checkout v0.4.2    # branch name is v0.4.2, version is 0.5.0
docker compose build
```

### Step 2: Initialize Alembic tracking

Since `main-auth` never used Alembic, stamp the database to tell Alembic where you are:

```bash
# Create the alembic_version table and stamp it at the base
docker exec llm-gateway alembic stamp base

# Then apply all migrations (001, 002, 003 — idempotent, safe to run)
docker exec llm-gateway alembic upgrade head
```

Migration `003` is specifically designed for `main-auth` upgrades — it checks each column/constraint before creating it, so it is safe even if your database is partially migrated.

### Step 3: Restart the gateway

```bash
docker compose down && docker compose up -d
```

### Step 4: Verify

```bash
# Check health
curl http://localhost:6130/health
# Expected: {"status":"ok","service":"llm-gateway","database":"up"}

# Check schema changes applied
docker exec llm-gateway-postgres psql -U gateway gateway_db -c "\d apikey" | grep owner_id
# Should show: owner_id | character varying | ...

docker exec llm-gateway-postgres psql -U gateway gateway_db -c "\d owner" | grep block_endpoints
# Should show: block_endpoints | boolean | not null default true
```

### What changed (schema)

| Table | Change | Default |
|---|---|---|
| `owner` | Added `block_endpoints` (bool) | `true` |
| `owner` | Added `is_active` (bool) | `true` |
| `owner` | Added `description` (text) | `null` |
| `owner` | Added `max_keys` (int) | `5` |
| `apikey` | Renamed `owner` → `owner_id` | — |
| `apikey` | Added FK to `owner.id` with CASCADE delete | — |
| `ownerpermission` | Added unique constraint `(owner_id, backend_name)` | — |

### What changed (application)

| Area | Change |
|---|---|
| **Policy engine** | Endpoint-aware scopes: `chat`, `embeddings`, `read_only`. Admin/wildcard bypass. |
| **Rate limiter** | Returns `Retry-After` header. Optional Redis backend via `REDIS_URL`. |
| **Database** | Optional PgBouncer via `PGBOUNCER_URL`. Connection pool tuning. |
| **Caching** | API keys (60s), backends (10s), settings (30s) cached in-memory. |
| **HTTPX** | Persistent connection pool per backend (no per-request TCP handshake). |
| **Frontend** | XSS fixes, keyboard nav, focus trap, local Tailwind CSS fallback. |
| **Infrastructure** | `docker-compose.prod.yml` with 2 gateways + nginx LB. |

### New environment variables (all optional)

| Variable | Purpose |
|---|---|
| `REDIS_URL` | Redis for cross-worker rate limiting. Falls back to in-process. |
| `PGBOUNCER_URL` | PgBouncer for connection pooling. Falls back to direct DB. |

### Rollback

```bash
# Restore from backup
docker exec -i llm-gateway-postgres psql -U gateway gateway_db < backup_main_auth.sql
git checkout main-auth
docker compose build && docker compose up -d
```

---

# Upgrade Guide: llm-secure-gateway → llm-gateway (Renamed)

This guide covers migrating from the original `llm-secure-gateway` to `llm-gateway`.

---

## Prerequisites

- Backup your existing database
- Note your current backend configurations
- Export API keys (they will need to be regenerated)

---

## Step 1: Database Migration

### Automated PostgreSQL 15 to 17 Upgrade

We provide ready-to-use scripts to effortlessly dump your data and upgrade the engine from PostgreSQL 15 to 17 safely.

```bash
# 1. Run the auto-upgrade script
./upgrade_pg_15_to_17.sh

# That's it! The script will automatically dump your PG15 database,
# swap the image in docker-compose, start PG17 on a fresh volume, and import your data!
```

### Manual Backup & Import Tools

If you want to manually migrate databases or create snapshots during your operation:

```bash
./dump_db.sh                      # Safely dumps gateway_db into a timestamped SQL file
./import_db.sh <backup_file.sql>  # Restores a SQL dump into the active postgres container
```

### Option A: Fresh Start (Recommended)

The v0.3.0 schema is significantly different. For a clean migration:

```bash
# Stop old gateway
docker compose down

# Remove old database (backup first!)
docker volume rm llm-secure-gateway_postgres_data

# Start v0.3.0
cd llm-gateway
docker compose up -d
```

### Option B: Manual Migration

If you need to preserve data, apply these SQL changes:

```sql
-- Add new tables
CREATE TYPE ownertype AS ENUM ('user', 'project');

CREATE TABLE owner (
    id VARCHAR PRIMARY KEY,
    type ownertype NOT NULL,
    name VARCHAR NOT NULL,
    email VARCHAR,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP
);

CREATE TABLE providerkey (
    id SERIAL PRIMARY KEY,
    owner_id VARCHAR REFERENCES owner(id) ON DELETE CASCADE,
    provider_id VARCHAR NOT NULL,
    encrypted_key VARCHAR NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP,
    UNIQUE(owner_id, provider_id)
);

-- Modify LLMBackend table
ALTER TABLE llmbackend
ADD COLUMN allowed_endpoints JSONB DEFAULT '[]';

-- Create indexes
CREATE INDEX idx_apikey_owner ON apikey(owner);
CREATE INDEX idx_apikey_prefix ON apikey(prefix);
CREATE INDEX idx_providerkey_owner ON providerkey(owner_id);
CREATE INDEX idx_providerkey_provider ON providerkey(provider_id);
```

---

## Step 2: Configuration Changes

### Environment Variables

| Old               | New                          | Notes                        |
| ----------------- | ---------------------------- | ---------------------------- |
| `OLLAMA_ENDPOINT` | Removed                      | Configure via Admin API      |
| `DATABASE_URL`    | Same                         | No change                    |
| -                 | `PHOENIX_COLLECTOR_ENDPOINT` | Phoenix telemetry endpoint   |
| -                 | `PHOENIX_API_KEY`            | Phoenix authentication       |
| -                 | `FERNET_KEY`                 | For encrypting provider keys |

### docker-compose.yml Changes

```yaml
# Old (llm-secure-gateway)
services:
  gateway:
    ports:
      - "6120:8000"

# New (llm-gateway)
services:
  gateway:
    ports:
      - "6130:8000"  # Different port to avoid conflicts
    environment:
      - PHOENIX_COLLECTOR_ENDPOINT=http://host.docker.internal:6006
      - PHOENIX_API_KEY=${PHOENIX_API_KEY:-}
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

---

## Step 3: Re-register Backends

Old backends need to be re-registered with new endpoint whitelisting:

### Old Way (V1)

```bash
curl -X POST http://localhost:6120/admin/backends \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ollama",
    "base_url": "http://10.10.110.25:11434",
    "backend_type": "ollama",
    "models": ["llama3.2:latest"]
  }'
```

### New Way

```bash
# Login first
TOKEN=$(curl -s -X POST http://localhost:6130/auth/jwt/login \
  -d "username=admin@example.com&password=admin" | jq -r '.access_token')

# Register backend with endpoint whitelist
curl -X POST http://localhost:6130/admin/backends \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ollama",
    "base_url": "http://10.10.110.25:11434",
    "backend_type": "ollama",
    "models": ["llama3.2:latest"],
    "allowed_endpoints": ["/api/chat", "/api/generate", "/v1/chat/completions"]
  }'
```

---

## Step 4: Create Owners

In latest v0.3.0 introduces the Owner concept. Create owners for your users/projects:

```bash
curl -X POST http://localhost:6130/admin/owners \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "my-project",
    "name": "My Project",
    "type": "project",
    "email": "project@example.com"
  }'
```

---

## Step 5: Regenerate API Keys

Old API keys are not compatible. Generate new ones:

```bash
curl -X POST http://localhost:6130/admin/keys \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "owner": "my-project",
    "scopes": ["chat"],
    "rate_limit_rpm": 60
  }'
```

---

## Step 6: Update API Endpoints

### Old Endpoints (V1)

```bash
# Chat completions
POST /v1/chat/completions
Authorization: Bearer sk-gateway-...
```

### New Endpoints (V2)

````bash
# Direct routing (explicit backend)
POST /direct/ollama/api/chat
X-API-Key: sk-gateway-...

# Dynamic routing (by provider)
POST /ollama/api/chat
X-API-Key: sk-gateway-...

```bash
# OpenAI-compatible (still supported)
POST /v1/chat/completions
Authorization: Bearer sk-gateway-...
````

> **For a full explanation of these routing strategies, see the [User Guide](USER_GUIDE.md).**

---

## Step 7: Optional - Set Provider Keys

If you want owners to use their own LLM API keys:

```bash
curl -X POST http://localhost:6130/admin/owners/my-project/keys \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "openai",
    "key": "sk-your-openai-key"
  }'
```

---

## Step 8: Verify Migration

Run the verification script:

```bash
# Inside container
docker exec -e GATEWAY_URL=http://localhost:8000 \
  llm-gateway-gateway-1 python3 /app/tests/verify_gateway.py v3
```

Or use the Admin UI at http://localhost:6130/auth/login/

---

## Breaking Changes Checklist

- [ ] Update `DATABASE_URL` if needed
- [ ] Re-register all backends with `allowed_endpoints`
- [ ] Create owners for existing users
- [ ] Regenerate all API keys
- [ ] Update client applications to use new endpoints
- [ ] Update authentication headers (`X-API-Key` or `Authorization`)
- [ ] Configure Phoenix telemetry (optional)
- [ ] Test all integrations

---

## Troubleshooting

### "Owner not found" errors

Create the owner first before creating API keys.

### "Endpoint not allowed" errors

Add the endpoint to the backend's `allowed_endpoints` list.

### Missing telemetry in Phoenix

Check `PHOENIX_API_KEY` and `PHOENIX_COLLECTOR_ENDPOINT` environment variables.

### 401 Unauthorized

- Verify API key is active
- Ensure correct header (`X-API-Key` or `Authorization: Bearer`)
- Check admin token hasn't expired

---

## Rollback Plan

If migration fails, restore your backup:

```bash
# Stop
docker compose down

# Restore old database backup
# ... your restore procedure ...

# Restart old gateway
cd ../llm-secure-gateway
docker compose up -d
```

---

## Support

For issues during migration:

1. Check logs: `docker logs llm-gateway-gateway-1`
2. Run verification: `python tests/verify_gateway.py v3`
3. Check Admin UI at `/ui/` for configuration issues
