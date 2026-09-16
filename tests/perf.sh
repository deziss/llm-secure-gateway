#!/usr/bin/env bash
#
# Performance Test for LLM Gateway V3
# Measures response times for single-threaded sequential requests
#
# Usage: ./perf.sh [iterations]
#   iterations: number of requests per endpoint (default: 20)
#

set -e

# Configuration
GATEWAY_URL="${GATEWAY_URL:-http://localhost:6130}"
API_KEY="${API_KEY:-}"
ITERATIONS=${1:-20}

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Endpoints to test
ENDPOINTS=(
    "direct/ollama/api/chat"
    "ollama/api/chat"
)

DATA='{
  "model": "llama3.2:latest",
  "messages": [
    { "role": "user", "content": "Hello!" }
  ],
  "stream": false
}'

echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║        LLM Gateway V3 - Performance Test                     ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo
echo "Gateway URL: $GATEWAY_URL"
echo "Iterations:  $ITERATIONS"
echo

# Check if API key is provided
if [ -z "$API_KEY" ]; then
    echo -e "${YELLOW}Warning: No API_KEY set. Set with: export API_KEY=sk-gateway-...${NC}"
    echo "Attempting to get API key from admin login..."
    
    # Try to login and get a key
    LOGIN_RESP=$(curl -s -X POST "$GATEWAY_URL/auth/jwt/login" \
        -d "username=admin@example.com&password=admin" 2>/dev/null || echo "")
    
    if echo "$LOGIN_RESP" | grep -q "access_token"; then
        ACCESS_TOKEN=$(echo "$LOGIN_RESP" | jq -r '.access_token')
        
        # Create a test key
        KEY_RESP=$(curl -s -X POST "$GATEWAY_URL/admin/keys" \
            -H "Authorization: Bearer $ACCESS_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"owner": "perf-test", "scopes": ["chat"]}' 2>/dev/null || echo "")
        
        if echo "$KEY_RESP" | grep -q "api_key"; then
            API_KEY=$(echo "$KEY_RESP" | jq -r '.api_key')
            echo -e "${GREEN}Auto-generated API key: ${API_KEY:0:20}...${NC}"
        else
            echo "Failed to create API key. Exiting."
            exit 1
        fi
    else
        echo "Failed to login. Please set API_KEY manually."
        exit 1
    fi
fi

echo

bench_endpoint() {
    local endpoint="$1"
    local name="$2"
    local url="$GATEWAY_URL/$endpoint"
    
    echo -e "${GREEN}=== Benchmark: $name ===${NC}"
    echo "URL: $url"
    echo
    
    local total=0
    local min=999999
    local max=0
    local success=0
    local failed=0
    
    printf "%-8s %-12s %s\n" "Req#" "Time(s)" "Status"
    printf "%-8s %-12s %s\n" "----" "--------" "------"
    
    for ((i=1; i<=ITERATIONS; i++)); do
        result=$(curl -s -o /dev/null -w "%{time_total}|%{http_code}" \
            -X POST "$url" \
            -H "X-API-Key: $API_KEY" \
            -H "Content-Type: application/json" \
            -d "$DATA" 2>/dev/null || echo "0|000")
        
        t=$(echo "$result" | cut -d'|' -f1)
        status=$(echo "$result" | cut -d'|' -f2)
        
        printf "%-8d %-12.4f %s\n" "$i" "$t" "$status"
        
        # Convert to microseconds for integer math
        us=$(printf "%.0f" "$(echo "$t * 1000000" | bc)")
        total=$((total + us))
        (( us < min )) && min=$us
        (( us > max )) && max=$us
        
        if [ "$status" = "200" ]; then
            ((success++))
        else
            ((failed++))
        fi
    done
    
    avg_us=$((total / ITERATIONS))
    
    echo
    echo -e "${CYAN}--- $name Stats ---${NC}"
    printf "Requests: %d (Success: %d, Failed: %d)\n" "$ITERATIONS" "$success" "$failed"
    printf "Avg:      %.4fs\n" "$(echo "$avg_us / 1000000" | bc -l)"
    printf "Min:      %.4fs\n" "$(echo "$min / 1000000" | bc -l)"
    printf "Max:      %.4fs\n" "$(echo "$max / 1000000" | bc -l)"
    echo
}

# Run benchmarks
for endpoint in "${ENDPOINTS[@]}"; do
    bench_endpoint "$endpoint" "$endpoint"
done

echo -e "${GREEN}Performance test complete!${NC}"
