"""
Unit tests for the IP tracking module.
Updated to use _active_ips private state after refactor.
"""
import pytest
import time
import llm_gateway.tracking as tracking_mod


class TestIPTracking:
    def setup_method(self):
        tracking_mod._active_ips.clear()
        tracking_mod._last_cleanup = 0.0

    def test_active_ips_initially_empty(self):
        from llm_gateway.tracking import get_active_ips
        assert get_active_ips() == []

    def test_get_active_ips_returns_recent(self):
        from llm_gateway.tracking import get_active_ips

        now = time.time()
        tracking_mod._active_ips["192.168.1.1"] = now
        tracking_mod._active_ips["192.168.1.2"] = now - 60

        result = get_active_ips()
        assert len(result) == 2
        ips = {r["ip"] for r in result}
        assert "192.168.1.1" in ips
        assert "192.168.1.2" in ips

    def test_get_active_ips_filters_old(self):
        from llm_gateway.tracking import get_active_ips

        now = time.time()
        tracking_mod._active_ips["recent.ip"] = now
        tracking_mod._active_ips["old.ip"] = now - 400  # > 5 min

        result = get_active_ips()
        assert len(result) == 1
        assert result[0]["ip"] == "recent.ip"

    def test_get_active_ips_includes_last_seen(self):
        from llm_gateway.tracking import get_active_ips

        tracking_mod._active_ips["test.ip"] = time.time() - 30

        result = get_active_ips()
        assert len(result) == 1
        assert "last_seen_seconds_ago" in result[0]
        assert 25 <= result[0]["last_seen_seconds_ago"] <= 35

    @pytest.mark.asyncio
    async def test_middleware_records_ip(self):
        from unittest.mock import MagicMock, AsyncMock
        from llm_gateway.tracking import IPTrackingMiddleware

        middleware = IPTrackingMiddleware(app=MagicMock())
        request = MagicMock()
        request.client.host = "10.0.0.99"
        call_next = AsyncMock(return_value=MagicMock())

        await middleware.dispatch(request, call_next)
        assert "10.0.0.99" in tracking_mod._active_ips

    @pytest.mark.asyncio
    async def test_middleware_cleans_up_stale_ips(self):
        from unittest.mock import MagicMock, AsyncMock
        from llm_gateway.tracking import IPTrackingMiddleware

        middleware = IPTrackingMiddleware(app=MagicMock())
        tracking_mod._active_ips["stale.ip"] = time.time() - 400
        tracking_mod._last_cleanup = 0.0  # force cleanup sweep

        request = MagicMock()
        request.client.host = "10.0.0.1"
        call_next = AsyncMock(return_value=MagicMock())

        await middleware.dispatch(request, call_next)
        assert "stale.ip" not in tracking_mod._active_ips
        assert "10.0.0.1" in tracking_mod._active_ips
