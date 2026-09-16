#!/bin/bash
set -e

# Dump the core LLM Gateway database
DUMP_FILE="gateway_db_backup_$(date +%Y%m%d_%H%M%S).sql"

echo "Creating database dump from llm-gateway-postgres into $DUMP_FILE..."
# use -O -x to ignore ownership and privileges which makes importing smoother
docker exec -t llm-gateway-postgres pg_dump -U gateway -d gateway_db -c -O -x > "$DUMP_FILE"

echo "Dump successful! File saved at: $DUMP_FILE"
