"""
Unit and integration tests for Concurrency Limiting and TPM (Tokens-Per-Minute) Rate Limiting.
Tests cover:
- LocalRateLimiter concurrency acquire / release / limits / zero limits / safe decrements.
- LocalRateLimiter TPM tracking, sliding-window expiry, tokens_to_consume, and retry_after.
- RedisRateLimiter concurrency and TPM with atomic Redis operations and local fallback.
- proxy_helpers apply_rate_limit raising 429 and setting request state.
- proxy_helpers release_rate_limit_concurrency safety and idempotency.
- APIKey and APIKeyCreate SQLModel fields for rate_limit_tpm and max_concurrency.
"""
import time
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException


# ===========================================================================
# 1. LocalRateLimiter - Concurrency
# ===========================================================================
class TestLocalRateLimiterConcurrency:
    def test_acquire_under_limit(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        allowed1, count1 = limiter.acquire_concurrency("user1", max_concurrent=3)
        assert allowed1 is True
        assert count1 == 1

        allowed2, count2 = limiter.acquire_concurrency("user1", max_concurrent=3)
        assert allowed2 is True
        assert count2 == 2

        allowed3, count3 = limiter.acquire_concurrency("user1", max_concurrent=3)
        assert allowed3 is True
        assert count3 == 3

    def test_acquire_exceeded(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        # Acquire up to limit of 2
        limiter.acquire_concurrency("user2", max_concurrent=2)
        limiter.acquire_concurrency("user2", max_concurrent=2)

        # 3rd request exceeds limit
        allowed, count = limiter.acquire_concurrency("user2", max_concurrent=2)
        assert allowed is False
        assert count == 2

    def test_release_concurrency(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        limiter.acquire_concurrency("user3", max_concurrent=2)
        limiter.acquire_concurrency("user3", max_concurrent=2)

        # Reached limit
        assert limiter.acquire_concurrency("user3", max_concurrent=2)[0] is False

        # Release one slot
        limiter.release_concurrency("user3")

        # Now acquire should succeed
        allowed, count = limiter.acquire_concurrency("user3", max_concurrent=2)
        assert allowed is True
        assert count == 2

    def test_release_does_not_drop_below_zero(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        # Releasing key with no active concurrency
        limiter.release_concurrency("empty_user")
        assert limiter._concurrency.get("empty_user", 0) == 0

        # Acquire 1, release 3 times
        limiter.acquire_concurrency("user_safe", max_concurrent=2)
        limiter.release_concurrency("user_safe")
        limiter.release_concurrency("user_safe")
        limiter.release_concurrency("user_safe")
        assert limiter._concurrency.get("user_safe", 0) == 0

        # Should still allow full quota
        allowed, count = limiter.acquire_concurrency("user_safe", max_concurrent=2)
        assert allowed is True
        assert count == 1

    def test_zero_or_negative_limit_unlimited(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        # 0 means unlimited
        for _ in range(10):
            allowed, count = limiter.acquire_concurrency("unlimited_user", max_concurrent=0)
            assert allowed is True
            assert count == 0

        # Negative limit means unlimited
        allowed_neg, count_neg = limiter.acquire_concurrency("unlimited_user", max_concurrent=-1)
        assert allowed_neg is True
        assert count_neg == 0

    def test_separate_keys_isolated(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        limiter.acquire_concurrency("key_a", max_concurrent=1)
        # key_a is full
        assert limiter.acquire_concurrency("key_a", max_concurrent=1)[0] is False
        # key_b is still open
        assert limiter.acquire_concurrency("key_b", max_concurrent=1)[0] is True

    @pytest.mark.asyncio
    async def test_async_concurrency_wrappers(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        allowed, count = await limiter.acquire_concurrency_async("async_user", max_concurrent=1)
        assert allowed is True
        assert count == 1

        allowed2, _ = await limiter.acquire_concurrency_async("async_user", max_concurrent=1)
        assert allowed2 is False

        await limiter.release_concurrency_async("async_user")
        allowed3, _ = await limiter.acquire_concurrency_async("async_user", max_concurrent=1)
        assert allowed3 is True


# ===========================================================================
# 2. LocalRateLimiter - TPM
# ===========================================================================
class TestLocalRateLimiterTPM:
    def test_check_token_limit_initially_allowed(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        allowed, retry = limiter.check_token_limit("tpm_user1", limit_tpm=1000)
        assert allowed is True
        assert retry == 0.0

    def test_record_tokens_and_exceed_limit(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        limiter.record_tokens("tpm_user2", 600)
        # 600 is below 1000
        allowed1, _ = limiter.check_token_limit("tpm_user2", limit_tpm=1000)
        assert allowed1 is True

        # Record 450 more -> 1050 > 1000
        limiter.record_tokens("tpm_user2", 450)
        allowed2, retry = limiter.check_token_limit("tpm_user2", limit_tpm=1000)
        assert allowed2 is False
        assert retry > 0.0
        assert retry <= 60.0

    def test_tokens_to_consume_check_and_consume(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        # Request 600 tokens against 1000 limit -> allowed and consumed
        allowed, _ = limiter.check_token_limit("consume_user", limit_tpm=1000, tokens_to_consume=600)
        assert allowed is True

        # Next request for 500 would make 1100 > 1000 -> rejected, not consumed
        allowed2, retry = limiter.check_token_limit("consume_user", limit_tpm=1000, tokens_to_consume=500)
        assert allowed2 is False
        assert retry > 0.0

        # Request for 300 makes 900 <= 1000 -> allowed
        allowed3, _ = limiter.check_token_limit("consume_user", limit_tpm=1000, tokens_to_consume=300)
        assert allowed3 is True

    def test_sliding_window_expiry(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        limiter.record_tokens("sliding_user", 1000)
        assert limiter.check_token_limit("sliding_user", limit_tpm=1000)[0] is False

        # Simulate 65 seconds passing for the recorded token entry
        records = limiter._token_records["sliding_user"]
        old_ts = time.time() - 65.0
        limiter._token_records["sliding_user"] = [(old_ts, records[0][1])]

        # Window expired -> limit should be freed
        allowed, retry = limiter.check_token_limit("sliding_user", limit_tpm=1000)
        assert allowed is True
        assert retry == 0.0

    def test_zero_or_negative_limit_unlimited(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        limiter.record_tokens("unlimited_tpm", 50000)
        # Limit 0 is unlimited
        allowed, retry = limiter.check_token_limit("unlimited_tpm", limit_tpm=0)
        assert allowed is True
        assert retry == 0.0

        # Negative limit is unlimited
        allowed_neg, _ = limiter.check_token_limit("unlimited_tpm", limit_tpm=-100)
        assert allowed_neg is True

    def test_separate_keys_isolated(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        limiter.record_tokens("tpm_a", 1000)
        assert limiter.check_token_limit("tpm_a", limit_tpm=1000)[0] is False
        assert limiter.check_token_limit("tpm_b", limit_tpm=1000)[0] is True

    @pytest.mark.asyncio
    async def test_async_tpm_wrappers(self):
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        await limiter.record_tokens_async("async_tpm", 500)
        allowed, retry = await limiter.check_token_limit_async("async_tpm", limit_tpm=400)
        assert allowed is False
        assert retry > 0.0


# ===========================================================================
# 3. RedisRateLimiter - Concurrency
# ===========================================================================
class TestRedisRateLimiterConcurrency:
    def test_redis_concurrency_acquire_allowed(self):
        from llm_gateway.rate_limit import RedisRateLimiter

        limiter = RedisRateLimiter("redis://dummy:6379/0")
        limiter._redis.incr = MagicMock(return_value=1)
        limiter._redis.expire = MagicMock()

        allowed, count = limiter.acquire_concurrency("redis_user", max_concurrent=2)
        assert allowed is True
        assert count == 1
        limiter._redis.incr.assert_called_once_with("concurrency:redis_user")
        limiter._redis.expire.assert_called_once_with("concurrency:redis_user", 3600)

    def test_redis_concurrency_acquire_exceeded(self):
        from llm_gateway.rate_limit import RedisRateLimiter

        limiter = RedisRateLimiter("redis://dummy:6379/0")
        limiter._redis.incr = MagicMock(return_value=3)  # Exceeds max_concurrent=2
        limiter._redis.decr = MagicMock()

        allowed, count = limiter.acquire_concurrency("redis_user", max_concurrent=2)
        assert allowed is False
        assert count == 2
        limiter._redis.decr.assert_called_once_with("concurrency:redis_user")

    def test_redis_concurrency_release(self):
        from llm_gateway.rate_limit import RedisRateLimiter

        limiter = RedisRateLimiter("redis://dummy:6379/0")
        limiter._redis.decr = MagicMock(return_value=1)
        limiter._redis.delete = MagicMock()

        limiter.release_concurrency("redis_user")
        limiter._redis.decr.assert_called_once_with("concurrency:redis_user")
        limiter._redis.delete.assert_not_called()

        # When decr drops to <= 0, delete is called
        limiter._redis.decr.return_value = 0
        limiter.release_concurrency("redis_user")
        limiter._redis.delete.assert_called_once_with("concurrency:redis_user")

    def test_redis_concurrency_zero_unlimited(self):
        from llm_gateway.rate_limit import RedisRateLimiter

        limiter = RedisRateLimiter("redis://dummy:6379/0")
        limiter._redis.incr = MagicMock()

        allowed, count = limiter.acquire_concurrency("unlimited", max_concurrent=0)
        assert allowed is True
        assert count == 0
        limiter._redis.incr.assert_not_called()

    def test_redis_concurrency_fallback_on_error(self):
        from llm_gateway.rate_limit import RedisRateLimiter

        limiter = RedisRateLimiter("redis://dummy:6379/0")
        limiter._redis.incr = MagicMock(side_effect=Exception("Redis connection dropped"))

        allowed, count = limiter.acquire_concurrency("fallback_user", max_concurrent=2)
        assert allowed is True
        assert count == 1
        assert limiter._healthy is False

        # Release also falls back safely
        limiter.release_concurrency("fallback_user")
        assert limiter._fallback._concurrency.get("fallback_user", 0) == 0

    @pytest.mark.asyncio
    async def test_redis_concurrency_async_wrappers(self):
        from llm_gateway.rate_limit import RedisRateLimiter

        limiter = RedisRateLimiter("redis://dummy:6379/0")
        limiter._redis.incr = MagicMock(return_value=1)
        limiter._redis.expire = MagicMock()
        limiter._redis.decr = MagicMock(return_value=0)
        limiter._redis.delete = MagicMock()

        allowed, count = await limiter.acquire_concurrency_async("async_redis", max_concurrent=2)
        assert allowed is True
        assert count == 1

        await limiter.release_concurrency_async("async_redis")
        limiter._redis.decr.assert_called_once_with("concurrency:async_redis")


# ===========================================================================
# 4. RedisRateLimiter - TPM
# ===========================================================================
class TestRedisRateLimiterTPM:
    def test_redis_tpm_record_tokens(self):
        from llm_gateway.rate_limit import RedisRateLimiter

        limiter = RedisRateLimiter("redis://dummy:6379/0")
        mock_pipe = MagicMock()
        limiter._redis.pipeline = MagicMock(return_value=mock_pipe)

        limiter.record_tokens("tpm_user", 500)
        mock_pipe.incrby.assert_called_once()
        args = mock_pipe.incrby.call_args[0]
        assert args[0].startswith("tpm:tpm_user:")
        assert args[1] == 500
        mock_pipe.expire.assert_called_once()
        assert mock_pipe.expire.call_args[0][1] == 120
        mock_pipe.execute.assert_called_once()

    def test_redis_tpm_check_under_limit(self):
        from llm_gateway.rate_limit import RedisRateLimiter

        limiter = RedisRateLimiter("redis://dummy:6379/0")
        limiter._redis.get = MagicMock(return_value="300")

        allowed, retry = limiter.check_token_limit("tpm_user", limit_tpm=1000)
        assert allowed is True
        assert retry == 0.0

    def test_redis_tpm_check_exceeded(self):
        from llm_gateway.rate_limit import RedisRateLimiter

        limiter = RedisRateLimiter("redis://dummy:6379/0")
        limiter._redis.get = MagicMock(return_value="1200")

        allowed, retry = limiter.check_token_limit("tpm_user", limit_tpm=1000)
        assert allowed is False
        assert retry > 0.0
        assert retry <= 60.0

    def test_redis_tpm_tokens_to_consume_allowed(self):
        from llm_gateway.rate_limit import RedisRateLimiter

        limiter = RedisRateLimiter("redis://dummy:6379/0")
        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [800]  # new total after incrby 500 is 800 <= 1000
        limiter._redis.pipeline = MagicMock(return_value=mock_pipe)

        allowed, retry = limiter.check_token_limit("consume_user", limit_tpm=1000, tokens_to_consume=500)
        assert allowed is True
        assert retry == 0.0

    def test_redis_tpm_tokens_to_consume_exceeded_rolls_back(self):
        from llm_gateway.rate_limit import RedisRateLimiter

        limiter = RedisRateLimiter("redis://dummy:6379/0")
        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [1200]  # new total exceeds limit 1000
        limiter._redis.pipeline = MagicMock(return_value=mock_pipe)
        limiter._redis.decrby = MagicMock()

        allowed, retry = limiter.check_token_limit("consume_user", limit_tpm=1000, tokens_to_consume=500)
        assert allowed is False
        assert retry > 0.0
        limiter._redis.decrby.assert_called_once()
        assert limiter._redis.decrby.call_args[0][1] == 500

    def test_redis_tpm_zero_unlimited(self):
        from llm_gateway.rate_limit import RedisRateLimiter

        limiter = RedisRateLimiter("redis://dummy:6379/0")
        limiter._redis.get = MagicMock()

        allowed, retry = limiter.check_token_limit("unlimited", limit_tpm=0)
        assert allowed is True
        assert retry == 0.0
        limiter._redis.get.assert_not_called()

    def test_redis_tpm_fallback_on_error(self):
        from llm_gateway.rate_limit import RedisRateLimiter

        limiter = RedisRateLimiter("redis://dummy:6379/0")
        limiter._redis.get = MagicMock(side_effect=Exception("Redis down"))

        allowed, retry = limiter.check_token_limit("fallback_user", limit_tpm=1000)
        assert allowed is True
        assert limiter._healthy is False

        # Subsequent record falls back to local
        limiter.record_tokens("fallback_user", 1500)
        allowed2, _ = limiter.check_token_limit("fallback_user", limit_tpm=1000)
        assert allowed2 is False

    @pytest.mark.asyncio
    async def test_redis_tpm_async_wrappers(self):
        from llm_gateway.rate_limit import RedisRateLimiter

        limiter = RedisRateLimiter("redis://dummy:6379/0")
        mock_pipe = MagicMock()
        limiter._redis.pipeline = MagicMock(return_value=mock_pipe)
        limiter._redis.get = MagicMock(return_value="200")

        await limiter.record_tokens_async("async_user", 100)
        mock_pipe.incrby.assert_called_once()

        allowed, _ = await limiter.check_token_limit_async("async_user", limit_tpm=500)
        assert allowed is True


# ===========================================================================
# 5. Integration with apply_rate_limit & release_rate_limit_concurrency
# ===========================================================================
class TestApplyRateLimitIntegration:
    def _create_mock_request(self, rpm=None, tpm=None, max_concurrency=None, key_hash="test_key"):
        user = MagicMock()
        user.key_hash = key_hash
        user.rate_limit_rpm = rpm
        user.rate_limit_tpm = tpm
        user.max_concurrency = max_concurrency

        req = MagicMock()
        req.state = MagicMock()
        req.state.user = user
        req.state.concurrency_acquired = False
        req.state.concurrency_key = None
        return req

    def test_apply_rate_limit_tpm_passes(self):
        from llm_gateway.proxy_helpers import apply_rate_limit
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        with patch("llm_gateway.proxy_helpers.get_rate_limiter", return_value=limiter):
            req = self._create_mock_request(rpm=60, tpm=10000, key_hash="user_tpm_ok")
            apply_rate_limit(req)  # Should not raise

    def test_apply_rate_limit_tpm_exceeded_raises_429(self):
        from llm_gateway.proxy_helpers import apply_rate_limit
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        limiter.record_tokens("user_tpm_over", 15000)

        with patch("llm_gateway.proxy_helpers.get_rate_limiter", return_value=limiter):
            req = self._create_mock_request(rpm=60, tpm=10000, key_hash="user_tpm_over")
            with pytest.raises(HTTPException) as exc_info:
                apply_rate_limit(req)
            assert exc_info.value.status_code == 429
            assert "Retry-After" in exc_info.value.headers

    def test_apply_rate_limit_concurrency_passes_and_sets_state(self):
        from llm_gateway.proxy_helpers import apply_rate_limit
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        with patch("llm_gateway.proxy_helpers.get_rate_limiter", return_value=limiter):
            req = self._create_mock_request(max_concurrency=2, key_hash="conc_ok")
            apply_rate_limit(req)

            assert req.state.concurrency_acquired is True
            assert req.state.concurrency_key == "conc_ok"
            assert limiter._concurrency["conc_ok"] == 1

    def test_apply_rate_limit_concurrency_exceeded_raises_429(self):
        from llm_gateway.proxy_helpers import apply_rate_limit
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        limiter.acquire_concurrency("conc_limit", max_concurrent=1)

        with patch("llm_gateway.proxy_helpers.get_rate_limiter", return_value=limiter):
            req = self._create_mock_request(max_concurrency=1, key_hash="conc_limit")
            with pytest.raises(HTTPException) as exc_info:
                apply_rate_limit(req)
            assert exc_info.value.status_code == 429
            assert exc_info.value.headers.get("Retry-After") == "1"

    def test_release_rate_limit_concurrency(self):
        from llm_gateway.proxy_helpers import apply_rate_limit, release_rate_limit_concurrency
        from llm_gateway.rate_limit import LocalRateLimiter

        limiter = LocalRateLimiter()
        with patch("llm_gateway.proxy_helpers.get_rate_limiter", return_value=limiter):
            req = self._create_mock_request(max_concurrency=1, key_hash="rel_key")
            apply_rate_limit(req)
            assert limiter._concurrency.get("rel_key") == 1

            # Release
            release_rate_limit_concurrency(req)
            assert req.state.concurrency_acquired is False
            assert limiter._concurrency.get("rel_key", 0) == 0

            # Calling again is idempotent (safe no-op)
            release_rate_limit_concurrency(req)
            assert limiter._concurrency.get("rel_key", 0) == 0

    def test_release_concurrency_noop_when_not_acquired(self):
        from llm_gateway.proxy_helpers import release_rate_limit_concurrency
        from llm_gateway.rate_limit import LocalRateLimiter

        req = MagicMock()
        req.state = MagicMock()
        req.state.concurrency_acquired = False
        req.state.concurrency_key = "unacquired"

        limiter = LocalRateLimiter()
        with patch("llm_gateway.proxy_helpers.get_rate_limiter", return_value=limiter):
            release_rate_limit_concurrency(req)
            assert limiter._concurrency.get("unacquired", 0) == 0


# ===========================================================================
# 6. Models - APIKey and APIKeyCreate fields
# ===========================================================================
class TestModelFields:
    def test_api_key_create_with_tpm_and_concurrency(self):
        from llm_gateway.models import APIKeyCreate

        key_create = APIKeyCreate(
            owner="user123",
            rate_limit_rpm=120,
            rate_limit_tpm=50000,
            max_concurrency=5,
        )
        assert key_create.owner == "user123"
        assert key_create.rate_limit_rpm == 120
        assert key_create.rate_limit_tpm == 50000
        assert key_create.max_concurrency == 5

    def test_api_key_create_defaults_none(self):
        from llm_gateway.models import APIKeyCreate

        key_create = APIKeyCreate(owner="user123")
        assert key_create.rate_limit_rpm == 60
        assert key_create.rate_limit_tpm is None
        assert key_create.max_concurrency is None

    def test_api_key_table_model_fields(self):
        from llm_gateway.models import APIKey

        key = APIKey(
            key_hash="hash_abc",
            prefix="sk-...",
            owner_id="user123",
            scopes=["chat"],
            rate_limit_rpm=60,
            rate_limit_tpm=25000,
            max_concurrency=4,
        )
        assert key.rate_limit_tpm == 25000
        assert key.max_concurrency == 4

    def test_api_key_table_model_defaults_none(self):
        from llm_gateway.models import APIKey

        key = APIKey(
            key_hash="hash_def",
            prefix="sk-...",
            owner_id="user123",
            scopes=["chat"],
        )
        assert key.rate_limit_tpm is None
        assert key.max_concurrency is None
