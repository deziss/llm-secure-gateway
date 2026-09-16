"""Token-bucket rate limiter with optional Redis backend.

When REDIS_URL is set and Redis is reachable, rate limits are shared across
all gateway workers/instances.  When Redis is unavailable (not configured,
unreachable, or crashes mid-flight), the limiter falls back transparently to
the in-process token bucket — the app keeps running, just without cross-worker
consistency.
"""

import logging
import os
import time
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

BUCKET_TTL_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# In-process fallback (always available)
# ---------------------------------------------------------------------------
class LocalRateLimiter:
    def __init__(self) -> None:
        self._buckets: Dict[str, Tuple[float, float]] = {}
        self._last_cleanup: float = time.time()

    def _cleanup_stale_buckets(self) -> None:
        now = time.time()
        if now - self._last_cleanup < 60:
            return
        cutoff = now - BUCKET_TTL_SECONDS
        stale = [k for k, (_, ts) in self._buckets.items() if ts < cutoff]
        for k in stale:
            del self._buckets[k]
        self._last_cleanup = now

    def check_rate_limit(self, key: str, limit_rpm: int) -> tuple[bool, float]:
        if limit_rpm <= 0:
            return True, 0.0

        self._cleanup_stale_buckets()

        now = time.time()
        tokens, last_update = self._buckets.get(key, (limit_rpm, now))

        elapsed = now - last_update
        replenish_rate = limit_rpm / 60.0
        tokens = min(limit_rpm, tokens + elapsed * replenish_rate)

        if tokens >= 1:
            self._buckets[key] = (tokens - 1, now)
            return True, 0.0

        self._buckets[key] = (tokens, now)
        retry_after = (1.0 - tokens) / replenish_rate if replenish_rate > 0 else 60.0
        return False, round(retry_after, 1)


# ---------------------------------------------------------------------------
# Redis-backed limiter (optional — requires REDIS_URL env var + redis pkg)
# ---------------------------------------------------------------------------
class RedisRateLimiter:
    """Sliding-window rate limiter stored in Redis.

    Uses a simple INCR + EXPIRE pattern per minute window.
    Falls back to LocalRateLimiter on any Redis error.
    """

    def __init__(self, redis_url: str) -> None:
        import redis  # type: ignore[import-untyped]

        self._redis = redis.from_url(redis_url, decode_responses=True, socket_timeout=1.0)
        self._fallback = LocalRateLimiter()
        self._healthy = True

    def check_rate_limit(self, key: str, limit_rpm: int) -> tuple[bool, float]:
        """Synchronous Redis sliding window check with fallback to local limiter."""
        if limit_rpm <= 0:
            return True, 0.0

        if not self._healthy:
            return self._fallback.check_rate_limit(key, limit_rpm)

        try:
            window_key = f"rl:{key}:{int(time.time()) // 60}"
            pipe = self._redis.pipeline()
            pipe.incr(window_key)
            pipe.expire(window_key, 120)
            results = pipe.execute()
            count = results[0]

            if count <= limit_rpm:
                return True, 0.0

            seconds_left = 60 - (int(time.time()) % 60)
            return False, float(seconds_left)
        except Exception as exc:
            logger.warning("Redis rate limiter failed (%s), falling back to local", exc)
            self._healthy = False
            return self._fallback.check_rate_limit(key, limit_rpm)

    async def check_rate_limit_async(self, key: str, limit_rpm: int) -> tuple[bool, float]:
        """Async wrapper delegating to synchronous Redis check."""
        return self.check_rate_limit(key, limit_rpm)

    def close(self) -> None:
        try:
            self._redis.close()
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Factory — picks the right implementation at import time
# ---------------------------------------------------------------------------
def _create_limiter() -> LocalRateLimiter | RedisRateLimiter:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        logger.info("REDIS_URL not set — using in-process rate limiter")
        return LocalRateLimiter()

    try:
        limiter = RedisRateLimiter(redis_url)
        logger.info("Redis rate limiter configured (%s)", redis_url.split("@")[-1])
        return limiter
    except Exception as exc:
        logger.warning("Failed to init Redis rate limiter (%s), using local fallback", exc)
        return LocalRateLimiter()


rate_limiter = _create_limiter()


def get_rate_limiter() -> LocalRateLimiter | RedisRateLimiter:
    return rate_limiter
