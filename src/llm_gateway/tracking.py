from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request, Response
import time

_ACTIVE_WINDOW = 300      # seconds — IPs inactive longer than this are dropped
_CLEANUP_INTERVAL = 60    # minimum seconds between cleanup sweeps
_TRAFFIC_WINDOW = 30      # keep last 30 minutes of per-minute request counts

# Class-level state so there is exactly one tracking dict regardless of how
# many times the middleware class is instantiated (FastAPI wraps it).
_active_ips: dict[str, float] = {}
_last_cleanup: float = 0.0

# Rolling traffic counter: deque of (minute_timestamp, count) pairs
_traffic_buckets: dict[int, int] = {}
_traffic_last_prune: float = 0.0


class IPTrackingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        global _last_cleanup, _traffic_last_prune

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        _active_ips[client_ip] = now

        # Count only proxy requests (LLM traffic), not admin/auth/static/health
        path = request.url.path
        is_proxy = not (
            path in ("/", "/health", "/docs", "/openapi.json", "/favicon.ico")
            or path.startswith(("/admin", "/auth", "/static"))
        )
        if is_proxy:
            minute_key = int(now) // 60
            _traffic_buckets[minute_key] = _traffic_buckets.get(minute_key, 0) + 1

        if now - _last_cleanup >= _CLEANUP_INTERVAL:
            cutoff = now - _ACTIVE_WINDOW
            stale = [ip for ip, ts in list(_active_ips.items()) if ts < cutoff]
            for ip in stale:
                del _active_ips[ip]
            _last_cleanup = now

        # Prune old traffic buckets every 60s
        if now - _traffic_last_prune >= 60:
            cutoff_minute = int(now) // 60 - _TRAFFIC_WINDOW
            old_keys = [k for k in _traffic_buckets if k < cutoff_minute]
            for k in old_keys:
                del _traffic_buckets[k]
            _traffic_last_prune = now

        return await call_next(request)


def get_active_ips() -> list[dict]:
    """Return all IPs seen within the last 5 minutes."""
    now = time.time()
    cutoff = now - _ACTIVE_WINDOW
    return [
        {"ip": ip, "last_seen_seconds_ago": int(now - ts)}
        for ip, ts in _active_ips.items()
        if ts >= cutoff
    ]


def get_traffic_history(minutes: int = 15) -> list[dict]:
    """Return per-minute request counts for the last N minutes.

    Returns a list of {"label": "HH:MM", "count": N} sorted chronologically.
    Missing minutes are filled with 0.
    """
    now_minute = int(time.time()) // 60
    result = []
    for offset in range(minutes - 1, -1, -1):
        minute_key = now_minute - offset
        count = _traffic_buckets.get(minute_key, 0)
        # Convert to HH:MM label
        ts = minute_key * 60
        t = time.localtime(ts)
        label = f"{t.tm_hour:02d}:{t.tm_min:02d}"
        result.append({"label": label, "count": count})
    return result
