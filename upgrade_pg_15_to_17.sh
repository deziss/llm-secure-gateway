#!/bin/bash
set -e

echo "================================================="
echo "   PostgreSQL 15 to 17 Upgrade Auto-Script       "
echo "================================================="

CURRENT_DB_CONTAINER="llm-gateway-postgres"
BACKUP_FILE="gateway_db_backup_pg15_$(date +%Y%m%d_%H%M%S).sql"

echo "[1/6] Starting PostgreSQL 15 Dump..."
docker exec -t $CURRENT_DB_CONTAINER pg_dump -U gateway -d gateway_db -c -O -x > "$BACKUP_FILE"
echo "  ✓ Backup saved directly to $BACKUP_FILE"

echo "[2/6] Stopping existing database & gateway..."
docker compose down

echo "[3/6] Modifying docker-compose.yml for PostgreSQL 17..."
# Use sed to gracefully apply DB upgrades in docker-compose.yml

# 1. Update the postgres image safely
sed -i 's/image: postgres:15/image: postgres:17/g' docker-compose.yml

# 2. To avoid startup crashes from incompatible DB data, rename the mapped volume
# This ensures PG17 starts with a fresh empty database, ready for our import
sed -i 's/- postgres_data:\/var\/lib\/postgresql\/data/- postgres17_data:\/var\/lib\/postgresql\/data/g' docker-compose.yml
sed -i 's/postgres_data:/postgres17_data:/g' docker-compose.yml

echo "  ✓ Docker Compose updated to postgres:17 and new postgres17_data volume."

echo "[4/6] Booting up new PostgreSQL 17 Server..."
export PHOENIX_COLLECTOR_ENDPOINT="http://localhost:6006"
docker compose up -d postgres

echo "  ⏳ Waiting 15 seconds for new database initialization..."
sleep 15

echo "[5/6] Restoring data into PostgreSQL 17..."
cat "$BACKUP_FILE" | docker exec -i $CURRENT_DB_CONTAINER psql -U gateway -d gateway_db

echo "[6/6] Restarting all remaining services (Gateway)..."
docker compose up -d

echo "================================================="
echo "  🎉 Upgrade to PostgreSQL 17 is Complete!       "
echo "================================================="
