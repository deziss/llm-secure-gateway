#!/usr/bin/env bash
#
# Stress Test for LLM Gateway V3
# Runs concurrent parallel requests to test gateway under load
#
# Usage: ./stress_test.sh [duration] [concurrent]
#   duration:   test duration in seconds (default: 60)
#   concurrent: number of parallel requests (default: 10)
#
# Requires: curl, jq, parallel (gnu-parallel)
#

set -e

# Configuration
GATEWAY_URL="${GATEWAY_URL:-http://localhost:6130}"
API_KEY="${API_KEY:-}"
ENDPOINT="${ENDPOINT:-ollama/api/chat}"

DURATION=${1:-60}
CONCURRENT=${2:-10}

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

URL="$GATEWAY_URL/$ENDPOINT"

# Base payload
BASE_DATA='{
  "model": "llama3.2:latest",
  "messages": [
    { "role": "user", "content": "Hello!" }
  ],
  "stream": false
}'

echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║          LLM Gateway V3 - Stress Test                        ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo
echo "Gateway URL:  $GATEWAY_URL"
echo "Endpoint:     $ENDPOINT"
echo "Duration:     ${DURATION}s"
echo "Concurrent:   $CONCURRENT"
echo

# Check for gnu-parallel
if ! command -v parallel &> /dev/null; then
    echo -e "${YELLOW}Warning: 'parallel' not found. Using sequential fallback.${NC}"
    echo "Install with: apt-get install parallel"
    USE_SEQUENTIAL=true
else
    USE_SEQUENTIAL=false
fi

# Check if API key is provided
if [ -z "$API_KEY" ]; then
    echo -e "${YELLOW}Warning: No API_KEY set. Set with: export API_KEY=sk-gateway-...${NC}"
    echo "Attempting to get API key from admin login..."
    
    LOGIN_RESP=$(curl -s -X POST "$GATEWAY_URL/auth/jwt/login" \
        -d "username=admin@example.com&password=admin" 2>/dev/null || echo "")
    
    if echo "$LOGIN_RESP" | grep -q "access_token"; then
        ACCESS_TOKEN=$(echo "$LOGIN_RESP" | jq -r '.access_token')
        
        KEY_RESP=$(curl -s -X POST "$GATEWAY_URL/admin/keys" \
            -H "Authorization: Bearer $ACCESS_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"owner": "stress-test", "scopes": ["chat"]}' 2>/dev/null || echo "")
        
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

# Export for subshells
export URL API_KEY BASE_DATA

stress_request() {
    local msg_num=$1
    
    # Dynamic message content
    local data=$(echo "$BASE_DATA" | jq ".messages[0].content = \"Stress test request #$msg_num\"")
    
    local result=$(curl -s -o /dev/null -w "%{time_total}|%{http_code}" \
        -X POST "$URL" \
        -H "X-API-Key: $API_KEY" \
        -H "Content-Type: application/json" \
        -d "$data" 2>/dev/null || echo "0|000")
    
    local time_total=$(echo "$result" | cut -d'|' -f1)
    local status=$(echo "$result" | cut -d'|' -f2)
    
    echo "$msg_num|$time_total|$status"
}
export -f stress_request

# Stats file
STATS_FILE=$(mktemp)
echo "0|0|0|999999|0" > "$STATS_FILE"  # req_count|total_time|error_count|min|max

printf "%-8s %-12s %s\n" "Req#" "Time(s)" "Status"
printf "%-8s %-12s %s\n" "----" "--------" "------"

# Run stress test
end_time=$((SECONDS + DURATION))
counter=0

if [ "$USE_SEQUENTIAL" = true ]; then
    # Fallback: sequential requests
    while [ $SECONDS -lt $end_time ]; do
        ((counter++))
        result=$(stress_request $counter)
        
        num=$(echo "$result" | cut -d'|' -f1)
        t=$(echo "$result" | cut -d'|' -f2)
        status=$(echo "$result" | cut -d'|' -f3)
        
        if [ "$status" = "200" ]; then
            printf "%-8s %-12.4f ${GREEN}%s${NC}\n" "$num" "$t" "$status"
        else
            printf "%-8s %-12.4f ${RED}%s${NC}\n" "$num" "$t" "$status"
        fi
        
        # Update stats
        read -r req_count total_time error_count min_time max_time < <(cat "$STATS_FILE" | tr '|' ' ')
        req_count=$((req_count + 1))
        total_time=$(echo "$total_time + $t" | bc -l)
        [ "$status" != "200" ] && error_count=$((error_count + 1))
        (( $(echo "$t * 1000000 < $min_time * 1000000" | bc -l) )) && min_time=$t
        (( $(echo "$t * 1000000 > $max_time * 1000000" | bc -l) )) && max_time=$t
        echo "$req_count|$total_time|$error_count|$min_time|$max_time" > "$STATS_FILE"
    done
else
    # Use parallel
    while [ $SECONDS -lt $end_time ]; do
        seq $((counter + 1)) $((counter + CONCURRENT)) | \
            parallel -j$CONCURRENT stress_request {} 2>/dev/null | \
            while IFS='|' read -r num t status; do
                if [ "$status" = "200" ]; then
                    printf "%-8s %-12.4f ${GREEN}%s${NC}\n" "$num" "$t" "$status"
                else
                    printf "%-8s %-12.4f ${RED}%s${NC}\n" "$num" "$t" "$status"
                fi
                
                # Update stats atomically
                (
                    flock 200
                    read -r req_count total_time error_count min_time max_time < <(cat "$STATS_FILE" | tr '|' ' ')
                    req_count=$((req_count + 1))
                    total_time=$(echo "$total_time + $t" | bc -l)
                    [ "$status" != "200" ] && error_count=$((error_count + 1))
                    (( $(echo "$t * 1000000 < $min_time * 1000000" | bc -l 2>/dev/null || echo 0) )) && min_time=$t
                    (( $(echo "$t * 1000000 > $max_time * 1000000" | bc -l 2>/dev/null || echo 0) )) && max_time=$t
                    echo "$req_count|$total_time|$error_count|$min_time|$max_time" > "$STATS_FILE"
                ) 200>"$STATS_FILE.lock"
            done &
        
        counter=$((counter + CONCURRENT))
        sleep 0.1
    done
    
    wait
fi

# Read final stats
read -r req_count total_time error_count min_time max_time < <(cat "$STATS_FILE" | tr '|' ' ')
rm -f "$STATS_FILE" "$STATS_FILE.lock"

# Calculate final metrics
if [ "$req_count" -gt 0 ]; then
    avg_time=$(echo "scale=4; $total_time / $req_count" | bc -l)
    rps=$(echo "scale=2; $req_count / $DURATION" | bc -l)
    error_pct=$(echo "scale=1; $error_count * 100 / $req_count" | bc -l)
else
    avg_time=0
    rps=0
    error_pct=0
fi

echo
echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║                    STRESS TEST RESULTS                       ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo
printf "Total Requests: %d\n" "$req_count"
printf "Success:        %d\n" "$((req_count - error_count))"
printf "Errors:         %d (%.1f%%)\n" "$error_count" "$error_pct"
echo
printf "Duration:       %ds\n" "$DURATION"
printf "RPS:            %.2f req/s\n" "$rps"
echo
printf "Response Times:\n"
printf "  Average:      %.4fs\n" "$avg_time"
printf "  Min:          %.4fs\n" "$min_time"
printf "  Max:          %.4fs\n" "$max_time"
echo
echo -e "${GREEN}Stress test complete!${NC}"
