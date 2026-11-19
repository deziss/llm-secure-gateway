import threading
from collections import deque
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class RequestTrace(BaseModel):
    request_id: str
    timestamp: str
    owner_id: Optional[str] = "anonymous"
    model: str
    provider: Optional[str] = "unknown"
    status_code: int = 200
    duration_ms: float = 0.0
    ttft_ms: Optional[float] = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    cache_hit: bool = False
    error_type: Optional[str] = None
    phases: Dict[str, float] = Field(default_factory=dict)


class TraceRingBuffer:
    def __init__(self, capacity: int = 200):
        self.capacity = capacity
        self._deque: deque = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def record(self, trace: RequestTrace) -> None:
        with self._lock:
            self._deque.appendleft(trace)

    def get_all(self, limit: int = 200) -> List[RequestTrace]:
        with self._lock:
            return list(self._deque)[:limit]


_global_trace_buffer = TraceRingBuffer(capacity=200)


def get_trace_buffer() -> TraceRingBuffer:
    return _global_trace_buffer
