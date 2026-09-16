#!/bin/bash
set -e

if [ -z "$1" ]; then
    echo "Usage: ./import_db.sh <backup_file.sql>"
    exit 1
fi

FILE="$1"

if [ ! -f "$FILE" ]; then
    echo "Error: File $FILE not found!"
    exit 1
fi

echo "Importing $FILE into llm-gateway-postgres..."
# Import the SQL dump
cat "$FILE" | docker exec -i llm-gateway-postgres psql -U gateway -d gateway_db

echo "Import complete!"
