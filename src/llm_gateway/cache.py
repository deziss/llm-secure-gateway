"""Simple in-memory TTL cache — no external dependencies required.

Used to reduce per-request database round-trips for data that changes
infrequently (API keys, backend list, system settings).  Falls back
gracefully: a cache miss simply hits the DB as before.
"""

import time
import threading
from typing import Any, Optional, Dict

# Sentinel for caching "not found" results so we don't re-query the DB
_MISSING = object()


class TTLCache:
    """Thread-safe in-memory cache with per-key TTL expiry."""

    def __init__(self, ttl_seconds: float = 60.0, max_size: int = 1024):
        self._store: Dict[str, tuple[Any, float]] = {}
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._lock = threading.Lock()

    def get(self, key: str) -> tuple[bool, Any]:
        """Return (hit, value).  hit=False means cache miss."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False, None
            value, expires_at = entry
            if time.time() > expires_at:
                del self._store[key]
                return False, None
            return True, value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        with self._lock:
            if len(self._store) >= self._max_size:
                self._evict_expired()
            self._store[key] = (value, time.time() + (ttl or self._ttl))

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def _evict_expired(self) -> None:
        now = time.time()
        expired = [k for k, (_, exp) in self._store.items() if now > exp]
        for k in expired:
            del self._store[k]
