# LLM Gateway — Database Migration Guide

## Overview

The LLM Gateway uses **Alembic** for schema migrations with a single idempotent
baseline migration (`001_baseline_v051`) that handles all upgrade paths
automatically.

---

## Quick Start

### Fresh Install

```bash
# Start the stack — migrations run automatically on boot
docker compose up -d
```

That's it. The Docker entrypoint runs `scripts/migrate.py` before starting the
app, which creates the complete v0.5.1 schema from scratch.

### Upgrading from v0.3.x (SQL Dump)

If you have an existing v0.3.x database (e.g., `gateway_db.sql`):

```bash
# 1. Start the stack
docker compose up -d

# 2. Import your dump (the migration will have already run)
docker exec -i llm-gateway-postgres psql -U gateway -d gateway_db < gateway_db.sql

# 3. Re-run migrations to patch the imported schema
docker exec llm-gateway python scripts/migrate.py
```

The migration automatically:
- Renames `apikey.owner` → `apikey.owner_id` (with FK)
- Adds `owner.block_endpoints`, `is_active`, `description`, `max_keys`
- Adds unique constraint `uq_owner_backend_perm`
- Creates `auditlog` table
- Adds missing indexes

### Upgrading from Old Alembic Chain (001→004)

If your database was managed by the old 4-migration chain:

```bash
docker exec llm-gateway python scripts/migrate.py
```

The script detects old revision IDs (001, 002, 003, 004) and stamps them to the
new baseline before running `upgrade head`. No manual intervention needed.

---

## Migration Helper Script

```bash
# Auto-detect state and upgrade
python scripts/migrate.py

# Show current state (empty / legacy / old_alembic / current)
python scripts/migrate.py --status

# Stamp head (after manual SQLModel.metadata.create_all)
python scripts/migrate.py --stamp

# Rollback one revision (DANGER — interactive confirmation)
python scripts/migrate.py --downgrade
```

---

## Upgrade Paths at a Glance

| Starting State | `alembic_version` | What Happens |
|---|---|---|
| **Fresh database** | (none) | Creates complete v0.5.1 schema |
| **v0.3.x SQL dump** | (none) | Detects tables, adds missing columns/tables |
| **Old Alembic (001-004)** | `004` | Stamps → `001_baseline_v051`, no-op upgrade |
| **Already at baseline** | `001_baseline_v051` | No-op |

---

## Manual Alembic Commands

```bash
# Inside the gateway container
docker exec -it llm-gateway bash

# Check current revision
alembic current

# Apply all pending migrations
alembic upgrade head

# Generate a new migration after model changes
alembic revision --autogenerate -m "description"

# Show migration history
alembic history --verbose
```

---

## Schema Reference (v0.5.1)

| Table | Primary Key | Description |
|---|---|---|
| `user` | `id` (UUID) | Auth users with RBAC roles |
| `llmbackend` | `name` | LLM backend configurations |
| `apikey` | `key_hash` | Scoped API keys (FK → owner.id) |
| `owner` | `id` | Multi-tenant resource owners |
| `providerkey` | `id` (serial) | Encrypted per-owner LLM API keys |
| `ownerpermission` | `id` (serial) | Owner-backend permission mappings |
| `systemsetting` | `key` | Key-value configuration store |
| `invitecode` | `code` | Registration invite codes |
| `auditlog` | `id` (serial) | Persistent audit trail |

---

## Importing a v0.3.x SQL Dump

### Recommended: Direct Import + Re-Migrate

```bash
# 1. Import the raw dump into the database
docker exec -i llm-gateway-postgres psql -U gateway -d gateway_db < gateway_db.sql

# 2. Run migrations (idempotent — only patches what's missing)
docker exec llm-gateway python scripts/migrate.py

# 3. Verify
docker exec llm-gateway python scripts/migrate.py --status
```

### Alternative: Upsert Script

If the direct import fails due to constraint violations (e.g., tables already
populated), convert COPY statements to `INSERT ... ON CONFLICT DO UPDATE`:

```sql
INSERT INTO public.apikey (owner_id, expires_at, rate_limit_rpm, key_hash, prefix, created_at, is_active, scopes)
VALUES ('dev-team', NULL, 600, '24d3917...', 'sk-gateway-Jzop...', NOW(), true, '["chat"]')
ON CONFLICT (key_hash) DO UPDATE SET
  owner_id = EXCLUDED.owner_id,
  rate_limit_rpm = EXCLUDED.rate_limit_rpm,
  scopes = EXCLUDED.scopes;
```

---

## PostgreSQL Version Upgrade (15 → 17)

```bash
# Use the provided upgrade script
./upgrade_pg_15_to_17.sh

# Manual backup & restore
./dump_db.sh                       # Creates timestamped SQL dump
./import_db.sh <backup_file.sql>   # Restores into active container
```

---

## Debugging

```bash
# Enable SQLAlchemy query logging
SQL_ECHO=true docker compose up gateway

# Check migration state
docker exec llm-gateway python scripts/migrate.py --status

# View Alembic history
docker exec llm-gateway alembic history --verbose
```

> For API examples and routing strategies, see the **[User Guide](../USER_GUIDE.md)**.
