"""Token-bucket and sliding-window rate limiter with optional Redis backend.

When REDIS_URL is set and Redis is reachable, rate limits are shared across
all gateway workers/instances.  When Redis is unavailable (not configured,
unreachable, or crashes mid-flight), the limiter falls back transparently to
the in-process token bucket and tracking — the app keeps running, just without
cross-worker consistency.
"""

import logging
import os
import time
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

BUCKET_TTL_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# In-process fallback (always available)
# ---------------------------------------------------------------------------
class LocalRateLimiter:
    def __init__(self) -> None:
        self._buckets: Dict[str, Tuple[float, float]] = {}
        self._concurrency: Dict[str, int] = {}
        self._token_records: Dict[str, List[Tuple[float, int]]] = {}
        self._last_cleanup: float = time.time()

    def _cleanup_stale_buckets(self) -> None:
        now = time.time()
        if now - self._last_cleanup < 60:
            return
        cutoff = now - BUCKET_TTL_SECONDS
        stale = [k for k, (_, ts) in self._buckets.items() if ts < cutoff]
        for k in stale:
            del self._buckets[k]

        # Clean up stale token sliding-window records (older than 60s)
        tpm_cutoff = now - 60.0
        empty_tpm = []
        for k, records in self._token_records.items():
            active = [r for r in records if r[0] > tpm_cutoff]
            if active:
                self._token_records[k] = active
            else:
                empty_tpm.append(k)
        for k in empty_tpm:
            del self._token_records[k]

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

    def acquire_concurrency(self, key: str, max_concurrent: int) -> tuple[bool, int]:
        """Acquire in-flight concurrency slot.

        If max_concurrent <= 0, return (True, 0).
        If current in-flight >= max_concurrent, return (False, current).
        Else increment and return (True, current+1).
        """
        if max_concurrent <= 0:
            return True, 0

        current = self._concurrency.get(key, 0)
        if current >= max_concurrent:
            return False, current

        new_count = current + 1
        self._concurrency[key] = new_count
        return True, new_count

    def release_concurrency(self, key: str) -> None:
        """Decrement active in-flight count safely, not dropping below 0."""
        current = self._concurrency.get(key, 0)
        if current > 1:
            self._concurrency[key] = current - 1
        else:
            self._concurrency.pop(key, None)

    def check_token_limit(self, key: str, limit_tpm: int, tokens_to_consume: int = 0) -> tuple[bool, float]:
        """Track tokens consumed in a 60-second sliding window.

        If exceeding limit, return (False, retry_after).
        If tokens_to_consume > 0 and within limit, consumes tokens and returns (True, 0.0).
        """
        if limit_tpm <= 0:
            return True, 0.0

        self._cleanup_stale_buckets()

        now = time.time()
        cutoff = now - 60.0
        records = [r for r in self._token_records.get(key, []) if r[0] > cutoff]
        self._token_records[key] = records

        current_tokens = sum(r[1] for r in records)

        if tokens_to_consume > 0:
            if current_tokens + tokens_to_consume > limit_tpm:
                needed = (current_tokens + tokens_to_consume) - limit_tpm
                freed = 0
                retry_after = 60.0
                for ts, cnt in records:
                    freed += cnt
                    if freed >= needed:
                        retry_after = max(1.0, 60.0 - (now - ts))
                        break
                return False, round(retry_after, 1)

            records.append((now, tokens_to_consume))
            self._token_records[key] = records
            return True, 0.0
        else:
            if current_tokens >= limit_tpm:
                needed = (current_tokens - limit_tpm) + 1
                freed = 0
                retry_after = 60.0
                for ts, cnt in records:
                    freed += cnt
                    if freed >= needed:
                        retry_after = max(1.0, 60.0 - (now - ts))
                        break
                return False, round(retry_after, 1)
            return True, 0.0

    def record_tokens(self, key: str, tokens: int) -> None:
        """Record consumed tokens in sliding window."""
        if tokens <= 0:
            return
        now = time.time()
        cutoff = now - 60.0
        records = [r for r in self._token_records.get(key, []) if r[0] > cutoff]
        records.append((now, tokens))
        self._token_records[key] = records

    async def acquire_concurrency_async(self, key: str, max_concurrent: int) -> tuple[bool, int]:
        return self.acquire_concurrency(key, max_concurrent)

    async def release_concurrency_async(self, key: str) -> None:
        self.release_concurrency(key)

    async def check_token_limit_async(self, key: str, limit_tpm: int, tokens_to_consume: int = 0) -> tuple[bool, float]:
        return self.check_token_limit(key, limit_tpm, tokens_to_consume)

    async def record_tokens_async(self, key: str, tokens: int) -> None:
        self.record_tokens(key, tokens)


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

    def acquire_concurrency(self, key: str, max_concurrent: int) -> tuple[bool, int]:
        """Atomic concurrency tracking via Redis INCR / DECR with fallback to LocalRateLimiter."""
        if max_concurrent <= 0:
            return True, 0

        if not self._healthy:
            return self._fallback.acquire_concurrency(key, max_concurrent)

        try:
            conc_key = f"concurrency:{key}"
            val = self._redis.incr(conc_key)
            if val > max_concurrent:
                self._redis.decr(conc_key)
                return False, val - 1

            # Auto-expire concurrency key after 1 hour to prevent indefinite leaks on hard kill
            self._redis.expire(conc_key, 3600)
            return True, val
        except Exception as exc:
            logger.warning("Redis acquire_concurrency failed (%s), falling back to local", exc)
            self._healthy = False
            return self._fallback.acquire_concurrency(key, max_concurrent)

    def release_concurrency(self, key: str) -> None:
        """Decrement active in-flight count safely, not dropping below 0."""
        if not self._healthy:
            self._fallback.release_concurrency(key)
            return

        try:
            conc_key = f"concurrency:{key}"
            val = self._redis.decr(conc_key)
            if val <= 0:
                self._redis.delete(conc_key)
        except Exception as exc:
            logger.warning("Redis release_concurrency failed (%s), falling back to local", exc)
            self._healthy = False
            self._fallback.release_concurrency(key)

    def check_token_limit(self, key: str, limit_tpm: int, tokens_to_consume: int = 0) -> tuple[bool, float]:
        """Sliding window TPM tracking via Redis with fallback to LocalRateLimiter."""
        if limit_tpm <= 0:
            return True, 0.0

        if not self._healthy:
            return self._fallback.check_token_limit(key, limit_tpm, tokens_to_consume)

        try:
            window_key = f"tpm:{key}:{int(time.time()) // 60}"
            seconds_left = max(1.0, float(60 - (int(time.time()) % 60)))
            if tokens_to_consume > 0:
                pipe = self._redis.pipeline()
                pipe.incrby(window_key, tokens_to_consume)
                pipe.expire(window_key, 120)
                results = pipe.execute()
                count = results[0]

                if count <= limit_tpm:
                    return True, 0.0

                self._redis.decrby(window_key, tokens_to_consume)
                return False, seconds_left
            else:
                val = self._redis.get(window_key)
                count = int(val) if val else 0
                if count < limit_tpm:
                    return True, 0.0
                return False, seconds_left
        except Exception as exc:
            logger.warning("Redis check_token_limit failed (%s), falling back to local", exc)
            self._healthy = False
            return self._fallback.check_token_limit(key, limit_tpm, tokens_to_consume)

    def record_tokens(self, key: str, tokens: int) -> None:
        """Record consumed tokens in current minute window via Redis INCRBY."""
        if tokens <= 0:
            return

        if not self._healthy:
            self._fallback.record_tokens(key, tokens)
            return

        try:
            window_key = f"tpm:{key}:{int(time.time()) // 60}"
            pipe = self._redis.pipeline()
            pipe.incrby(window_key, tokens)
            pipe.expire(window_key, 120)
            pipe.execute()
        except Exception as exc:
            logger.warning("Redis record_tokens failed (%s), falling back to local", exc)
            self._healthy = False
            self._fallback.record_tokens(key, tokens)

    async def acquire_concurrency_async(self, key: str, max_concurrent: int) -> tuple[bool, int]:
        return self.acquire_concurrency(key, max_concurrent)

    async def release_concurrency_async(self, key: str) -> None:
        self.release_concurrency(key)

    async def check_token_limit_async(self, key: str, limit_tpm: int, tokens_to_consume: int = 0) -> tuple[bool, float]:
        return self.check_token_limit(key, limit_tpm, tokens_to_consume)

    async def record_tokens_async(self, key: str, tokens: int) -> None:
        self.record_tokens(key, tokens)

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
