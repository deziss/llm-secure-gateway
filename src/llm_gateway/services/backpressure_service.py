"""Adaptive in-flight concurrency limiter and backpressure shedding.

Protects gateway workers and downstream LLM inference backends from cascading
connection pool exhaustion under sudden traffic spikes. When active inference
streams exceed the threshold, excess requests are shed immediately with
HTTP 529 (Gateway Overloaded) and Retry-After: 1.
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Global in-flight inference counter across async event loop tasks
_inflight_count: int = 0
_max_inflight_default: int = int(os.getenv("GATEWAY_MAX_INFLIGHT_REQUESTS", "500"))


def get_inflight_count() -> int:
    """Return the current number of active in-flight inference requests."""
    return _inflight_count


def set_max_inflight(value: int) -> None:
    """Dynamically set the max in-flight threshold."""
    global _max_inflight_default
    _max_inflight_default = max(1, value)


def get_max_inflight() -> int:
    """Get the current max in-flight threshold."""
    return _max_inflight_default


def is_inference_path(path: str) -> bool:
    """Return True if the request path represents a long-running inference endpoint."""
    norm = "/" + path.lstrip("/")
    inference_prefixes = (
        "/v1/chat/completions",
        "/v1/completions",
        "/v1/embeddings",
        "/v1/messages",
        "/api/chat",
        "/api/generate",
        "/api/embeddings",
    )
    return any(norm.startswith(prefix) for prefix in inference_prefixes)


@asynccontextmanager
async def backpressure_guard(max_concurrent: int | None = None) -> AsyncGenerator[None, None]:
    """Context manager to guard inference execution slots against concurrency limits."""
    global _inflight_count
    limit = max_concurrent if max_concurrent is not None else _max_inflight_default

    if _inflight_count >= limit:
        logger.warning(
            "Backpressure shedding active: %d in-flight inference requests (limit=%d)",
            _inflight_count,
            limit,
        )
        raise HTTPException(
            status_code=529,
            detail="Gateway overloaded, please retry",
            headers={"Retry-After": "1"},
        )

    _inflight_count += 1
    try:
        yield
    finally:
        _inflight_count = max(0, _inflight_count - 1)
