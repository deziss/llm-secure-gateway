from opentelemetry import metrics


# Metrics counters/histograms (initialized as None, set in setup_metrics)
METER = None
REQ_COUNTER = None
SUCCESS_COUNTER = None
FAIL_COUNTER = None
PROMPT_TOKEN_COUNTER = None
COMPLETION_TOKEN_COUNTER = None
TOTAL_TOKEN_COUNTER = None
REQUEST_DURATION_HIST = None
BACKEND_LATENCY_HIST = None


def setup_metrics() -> None:
    global METER, REQ_COUNTER, SUCCESS_COUNTER, FAIL_COUNTER
    global PROMPT_TOKEN_COUNTER, COMPLETION_TOKEN_COUNTER, TOTAL_TOKEN_COUNTER
    global REQUEST_DURATION_HIST, BACKEND_LATENCY_HIST

    METER = metrics.get_meter("llm_gateway_metrics")

    # Counters
    REQ_COUNTER = METER.create_counter("llm_gateway_requests_total", description="Total LLM Gateway requests")
    SUCCESS_COUNTER = METER.create_counter("llm_gateway_requests_success_total", description="Total successful requests")
    FAIL_COUNTER = METER.create_counter("llm_gateway_requests_failed_total", description="Total failed requests")

    PROMPT_TOKEN_COUNTER = METER.create_counter("llm_gateway_prompt_tokens_total", description="Total prompt tokens used")
    COMPLETION_TOKEN_COUNTER = METER.create_counter("llm_gateway_completion_tokens_total", description="Total completion tokens used")
    TOTAL_TOKEN_COUNTER = METER.create_counter("llm_gateway_total_tokens_total", description="Total tokens used (prompt + completion)")

    # Histograms
    REQUEST_DURATION_HIST = METER.create_histogram("llm_gateway_request_duration_seconds", description="Total gateway request duration in seconds")
    BACKEND_LATENCY_HIST = METER.create_histogram("llm_gateway_backend_latency_seconds", description="Backend response latency in seconds")

# Record metrics helper
def record_request_metrics(
    backend_url: str,
    model: str,
    endpoint_uri: str,
    user_name: str,
    success: bool = True,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    request_duration: float = 0.0,
    backend_latency: float = 0.0
) -> None:
    # Skip if metrics not yet initialized
    if REQ_COUNTER is None:
        return

    attrs = {
        "backend_url": backend_url,
        "model": model,
        "endpoint_uri": endpoint_uri,
        "user_name": user_name
    }

    REQ_COUNTER.add(1, attributes=attrs)
    if success:
        SUCCESS_COUNTER.add(1, attributes=attrs)
    else:
        FAIL_COUNTER.add(1, attributes=attrs)

    PROMPT_TOKEN_COUNTER.add(prompt_tokens, attributes=attrs)
    COMPLETION_TOKEN_COUNTER.add(completion_tokens, attributes=attrs)
    # Use provided total_tokens if available, otherwise calculate
    total = total_tokens if total_tokens > 0 else (prompt_tokens + completion_tokens)
    TOTAL_TOKEN_COUNTER.add(total, attributes=attrs)

    if request_duration > 0:
        REQUEST_DURATION_HIST.record(request_duration, attributes=attrs)
    if backend_latency > 0:
        BACKEND_LATENCY_HIST.record(backend_latency, attributes=attrs)
