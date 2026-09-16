#!/usr/bin/env bash
# Rebuild the local Tailwind CSS bundle.
# Run this after adding new Tailwind classes to templates or JS.
#
# Usage:  ./build-css.sh          (minified production build)
#         ./build-css.sh --watch  (dev mode — rebuilds on file changes)

set -euo pipefail
cd "$(dirname "$0")"

INPUT="src/llm_gateway/static/css/input.css"
OUTPUT="src/llm_gateway/static/css/tailwind.min.css"

if [ "${1:-}" = "--watch" ]; then
  npx tailwindcss -i "$INPUT" -o "$OUTPUT" --watch
else
  npx tailwindcss -i "$INPUT" -o "$OUTPUT" --minify
  echo "Built $OUTPUT ($(du -h "$OUTPUT" | cut -f1) minified)"
fi
