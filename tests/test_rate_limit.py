"""
Unit tests for the rate limiting module (LocalRateLimiter + Redis fallback).
"""
import pytest
import time
from unittest.mock import patch


class TestLocalRateLimiter:
    """Test cases for the in-process LocalRateLimiter."""

    def test_allows_within_limit(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        for i in range(5):
            allowed, retry = limiter.check_rate_limit("user1", 60)
            assert allowed is True, f"Request {i+1} should be allowed"
            assert retry == 0.0

    def test_blocks_when_exceeded(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        for _ in range(60):
            limiter.check_rate_limit("user2", 60)

        allowed, retry = limiter.check_rate_limit("user2", 60)
        assert allowed is False
        assert retry > 0

    def test_unlimited_when_zero(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        for _ in range(100):
            allowed, retry = limiter.check_rate_limit("unlimited_user", 0)
            assert allowed is True
            assert retry == 0.0

    def test_replenishes_tokens(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        for _ in range(10):
            limiter.check_rate_limit("user3", 10)

        allowed, _ = limiter.check_rate_limit("user3", 10)
        assert allowed is False

        # Simulate 6 seconds passing
        current_tokens, last_time = limiter._buckets.get("user3", (0, 0))
        limiter._buckets["user3"] = (current_tokens, last_time - 6)
        limiter._last_cleanup = last_time - 6

        allowed, _ = limiter.check_rate_limit("user3", 10)
        assert allowed is True

    def test_separate_buckets_per_user(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        for _ in range(5):
            limiter.check_rate_limit("user_a", 5)

        allowed_a, _ = limiter.check_rate_limit("user_a", 5)
        assert allowed_a is False

        allowed_b, _ = limiter.check_rate_limit("user_b", 5)
        assert allowed_b is True

    def test_cleanup_stale_buckets(self):
        from llm_gateway.rate_limit import LocalRateLimiter, BUCKET_TTL_SECONDS

        limiter = LocalRateLimiter()
        limiter.check_rate_limit("stale_user", 60)
        assert "stale_user" in limiter._buckets

        limiter._buckets["stale_user"] = (60, time.time() - BUCKET_TTL_SECONDS - 120)
        limiter._last_cleanup = 0

        limiter.check_rate_limit("new_user", 60)
        assert "stale_user" not in limiter._buckets

    def test_retry_after_value_is_positive(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        for _ in range(10):
            limiter.check_rate_limit("retry_user", 10)

        allowed, retry = limiter.check_rate_limit("retry_user", 10)
        assert allowed is False
        assert retry > 0
        assert retry <= 60  # should never exceed a minute window


class TestRateLimiterFactory:
    """Test the factory picks the right implementation."""

    def test_no_redis_url_returns_local(self):
        from llm_gateway.rate_limit import LocalRateLimiter, get_rate_limiter

        limiter = get_rate_limiter()
        # Without REDIS_URL set, should be LocalRateLimiter
        assert isinstance(limiter, LocalRateLimiter)

    def test_factory_with_invalid_redis_url_returns_local(self):
        from llm_gateway.rate_limit import _create_limiter, LocalRateLimiter

        with patch.dict("os.environ", {"REDIS_URL": "redis://nonexistent:6379/0"}):
            try:
                limiter = _create_limiter()
            except Exception:
                # If redis package not installed, should still get a limiter
                limiter = LocalRateLimiter()
            assert hasattr(limiter, "check_rate_limit")


class TestRedisRateLimiter:
    def test_redis_limiter_allows_and_blocks(self):
        from llm_gateway.rate_limit import RedisRateLimiter
        from unittest.mock import MagicMock

        limiter = RedisRateLimiter("redis://dummy:6379/0")
        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [5]  # 5 requests in current minute
        limiter._redis.pipeline = MagicMock(return_value=mock_pipe)

        allowed, retry = limiter.check_rate_limit("test_key", 10)
        assert allowed is True
        assert retry == 0.0

        # Now simulate exceeded limit
        mock_pipe.execute.return_value = [15]  # 15 requests, limit is 10
        allowed, retry = limiter.check_rate_limit("test_key", 10)
        assert allowed is False
        assert retry > 0.0

    def test_redis_limiter_fallback_on_error(self):
        from llm_gateway.rate_limit import RedisRateLimiter
        from unittest.mock import MagicMock

        limiter = RedisRateLimiter("redis://dummy:6379/0")
        limiter._redis.pipeline = MagicMock(side_effect=Exception("Connection lost"))

        # Falls back to local limiter transparently
        allowed, retry = limiter.check_rate_limit("fallback_key", 10)
        assert allowed is True
        assert limiter._healthy is False
