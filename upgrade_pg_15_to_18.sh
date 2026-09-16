#!/bin/bash
set -e

echo "================================================="
echo "   PostgreSQL 15 to 18 Upgrade Auto-Script       "
echo "================================================="

CURRENT_DB_CONTAINER="llm-gateway-postgres"
BACKUP_FILE="gateway_db_backup_pg15_$(date +%Y%m%d_%H%M%S).sql"

echo "[1/6] Starting PostgreSQL 15 Dump..."
if docker ps | grep -q "$CURRENT_DB_CONTAINER"; then
  docker exec -t $CURRENT_DB_CONTAINER pg_dump -U gateway -d gateway_db -c -O -x > "$BACKUP_FILE"
  echo "  ✓ Backup saved directly to $BACKUP_FILE"
else
  echo "  ! $CURRENT_DB_CONTAINER is not currently running. Skipping live dump."
fi

echo "[2/6] Stopping existing database & gateway..."
docker compose down

echo "[3/6] Modifying docker-compose.yml for PostgreSQL 18..."
sed -i 's/image: postgres:[0-9]*/image: postgres:18-alpine/g' docker-compose.yml
sed -i 's/postgres[0-9]*_data:/postgres18_data:/g' docker-compose.yml

echo "  ✓ Docker Compose updated to postgres:18-alpine and postgres18_data volume."

echo "[4/6] Booting up new PostgreSQL 18 Server..."
docker compose up -d postgres

echo "  ⏳ Waiting 10 seconds for new database initialization..."
sleep 10

echo "[5/6] Restoring data into PostgreSQL 18..."
if [ -f "$BACKUP_FILE" ]; then
  cat "$BACKUP_FILE" | docker exec -i $CURRENT_DB_CONTAINER psql -U gateway -d gateway_db
  echo "  ✓ Data restored into PostgreSQL 18."
fi

echo "[6/6] Restarting all remaining services (Gateway & Migrate)..."
docker compose up -d

echo "================================================="
echo "  🎉 Upgrade to PostgreSQL 18 is Complete!       "
echo "================================================="
