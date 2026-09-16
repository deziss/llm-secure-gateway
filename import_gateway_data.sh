#!/bin/bash
# Import data from gateway_db.sql into the running PostgreSQL container.
#
# Usage: ./import_gateway_data.sh
#
# This script is safe to run after the app has started and created the full schema.
# It imports ONLY the data rows from the old dump into existing tables,
# skipping any that already exist (ON CONFLICT DO NOTHING).

set -e

COMPOSE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$COMPOSE_DIR"

echo "=== Importing API Keys ==="
docker compose exec -T postgres psql -U gateway -d gateway_db <<'SQL'
INSERT INTO apikey (owner, expires_at, rate_limit_rpm, key_hash, prefix, created_at, is_active, scopes)
VALUES
  ('dev-team', NULL, 600, '24d39179c1fec6c49a46a33317dcc0f0c502a578f19b62b8238026ae96be1103', 'sk-gateway-Jzop...', '2026-01-10 06:33:19.437367', true, '["chat"]'),
  ('kaveri-user', NULL, 60, '224ed241c49fa4197aae1a7d2fe27f6bcdb6ed65d343985512acb297b769015b', 'sk-gateway-yLPZ...', '2026-02-21 05:14:59.444514', true, '["chat"]'),
  ('kaveri-user', NULL, 60, 'a6a031ae6f243a73cdf3e4d0370fc7c00789bd5932934835ec8e66a76c6a3541', 'sk-gateway-wiy-...', '2026-02-21 05:15:29.70209', true, '["chat"]'),
  ('nuomics', NULL, 60, 'd1148bb0075297cc50f817c9f8a585e722f59bd2bcf3eca4505907d3392b7b53', 'sk-gateway-oKm2...', '2026-02-18 12:02:20.993719', true, '["chat","generate","embeddings","models"]'),
  ('kaveri-user', NULL, 60, '47de58465ed4cce06e032f976d81079417bcf23787e23b6dd2f4509f77b7630a', 'sk-gateway-Qrdj...', '2026-02-25 05:19:14.260175', true, '["chat"]')
ON CONFLICT (key_hash) DO NOTHING;
SQL

echo "=== Importing LLM Backends ==="
docker compose exec -T postgres psql -U gateway -d gateway_db <<'SQL'
INSERT INTO llmbackend (name, base_url, backend_type, api_key, models)
VALUES
  ('local-ollama', 'http://host.docker.internal:11434', 'OLLAMA', NULL, '["llama3.2:latest"]'),
  ('ollama-kaveri', 'http://10.120.130.55:11434', 'OLLAMA', NULL, '["llama3.2:latest"]'),
  ('ollama-kimi', 'http://10.120.130.55:11434', 'OLLAMA', NULL, '["kimi-k2.5:cloud"]')
ON CONFLICT (name) DO UPDATE SET
  base_url = EXCLUDED.base_url,
  models = EXCLUDED.models;
SQL

echo "=== Import complete! ==="
echo "Verify with: docker compose exec postgres psql -U gateway -d gateway_db -c 'SELECT name, base_url FROM llmbackend;'"
