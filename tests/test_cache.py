"""Unit tests for the TTL cache module."""
import time


class TestTTLCache:

    def test_set_and_get(self):
        from llm_gateway.cache import TTLCache
        cache = TTLCache(ttl_seconds=60)
        cache.set("key1", "value1")
        hit, val = cache.get("key1")
        assert hit is True
        assert val == "value1"

    def test_miss_on_unknown_key(self):
        from llm_gateway.cache import TTLCache
        cache = TTLCache()
        hit, val = cache.get("nonexistent")
        assert hit is False
        assert val is None

    def test_expires_after_ttl(self):
        from llm_gateway.cache import TTLCache
        cache = TTLCache(ttl_seconds=0.1)
        cache.set("expire_key", "data")
        time.sleep(0.15)
        hit, val = cache.get("expire_key")
        assert hit is False

    def test_custom_ttl_per_key(self):
        from llm_gateway.cache import TTLCache
        cache = TTLCache(ttl_seconds=60)
        cache.set("short", "data", ttl=0.1)
        cache.set("long", "data", ttl=60)
        time.sleep(0.15)
        assert cache.get("short")[0] is False
        assert cache.get("long")[0] is True

    def test_invalidate(self):
        from llm_gateway.cache import TTLCache
        cache = TTLCache()
        cache.set("key", "val")
        cache.invalidate("key")
        assert cache.get("key")[0] is False

    def test_clear(self):
        from llm_gateway.cache import TTLCache
        cache = TTLCache()
        for i in range(10):
            cache.set(f"k{i}", i)
        cache.clear()
        for i in range(10):
            assert cache.get(f"k{i}")[0] is False

    def test_max_size_evicts_expired(self):
        from llm_gateway.cache import TTLCache
        cache = TTLCache(ttl_seconds=0.01, max_size=5)
        for i in range(5):
            cache.set(f"old{i}", i)
        time.sleep(0.02)
        # All 5 expired, setting a new key should evict them
        cache.set("new", "fresh")
        hit, val = cache.get("new")
        assert hit is True
        assert val == "fresh"

    def test_caches_none_values(self):
        from llm_gateway.cache import TTLCache
        cache = TTLCache()
        cache.set("nullable", None)
        hit, val = cache.get("nullable")
        assert hit is True
        assert val is None
