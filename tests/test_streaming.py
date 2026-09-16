"""Tests for streaming proxy, circuit breaker, and SSE endpoint."""
import pytest
import time
from unittest.mock import MagicMock, AsyncMock, patch
import httpx


# ─── Circuit Breaker ─────────────────────────────────────────────────────

class TestCircuitBreaker:

    def test_starts_closed(self):
        from llm_gateway.proxy_helpers import CircuitBreaker
        cb = CircuitBreaker()
        assert cb.is_open("http://backend:11434") is False

    def test_opens_after_threshold(self):
        from llm_gateway.proxy_helpers import CircuitBreaker
        cb = CircuitBreaker()
        cb.FAILURE_THRESHOLD = 3
        url = "http://backend:11434"
        for _ in range(3):
            cb.record_failure(url)
        assert cb.is_open(url) is True

    def test_success_resets_failures(self):
        from llm_gateway.proxy_helpers import CircuitBreaker
        cb = CircuitBreaker()
        cb.FAILURE_THRESHOLD = 5
        url = "http://backend:11434"
        for _ in range(4):
            cb.record_failure(url)
        cb.record_success(url)
        assert cb.is_open(url) is False
        assert cb._get(url)["failures"] == 0

    def test_half_open_after_timeout(self):
        from llm_gateway.proxy_helpers import CircuitBreaker
        cb = CircuitBreaker()
        cb.FAILURE_THRESHOLD = 1
        cb.RECOVERY_TIMEOUT = 0.1
        url = "http://backend:11434"
        cb.record_failure(url)
        assert cb.is_open(url) is True
        time.sleep(0.15)
        # After recovery timeout, should be half-open (allows one probe)
        assert cb.is_open(url) is False
        assert cb._get(url)["state"] == "half_open"

    def test_different_urls_independent(self):
        from llm_gateway.proxy_helpers import CircuitBreaker
        cb = CircuitBreaker()
        cb.FAILURE_THRESHOLD = 2
        cb.record_failure("http://a:11434")
        cb.record_failure("http://a:11434")
        assert cb.is_open("http://a:11434") is True
        assert cb.is_open("http://b:11434") is False

    def test_get_status(self):
        from llm_gateway.proxy_helpers import CircuitBreaker
        cb = CircuitBreaker()
        cb.FAILURE_THRESHOLD = 1
        cb.record_failure("http://a:11434")
        cb.record_success("http://b:11434")
        status = cb.get_status()
        assert status["http://a:11434"] == "open"
        assert status["http://b:11434"] == "closed"


# ─── try_backend_with_fallback circuit integration ────────────────────────

class TestTryBackendWithCircuit:

    @pytest.mark.asyncio
    async def test_skips_open_circuit_to_fallback(self):
        """When primary URL circuit is open, falls through to fallback."""
        from llm_gateway.proxy_helpers import try_backend_with_fallback, _circuit_breaker

        backend = MagicMock()
        backend.base_url = "http://dead:11434"
        backend.fallback_urls = ["http://alive:11434"]
        backend.name = "test"

        # Trip the primary circuit
        _circuit_breaker.FAILURE_THRESHOLD = 1
        _circuit_breaker.record_failure("http://dead:11434")

        mock_response = AsyncMock()
        mock_response.status_code = 200

        with patch("llm_gateway.proxy_helpers._get_client") as mock_client_fn:
            mock_client = AsyncMock()
            mock_client.build_request = MagicMock(return_value=MagicMock())
            mock_client.send = AsyncMock(return_value=mock_response)
            mock_client_fn.return_value = mock_client

            r, used_url, _ = await try_backend_with_fallback(
                backend, "api/chat", "POST", {}, {"model": "x"}, None
            )
            assert used_url == "http://alive:11434"

        # Reset for other tests
        _circuit_breaker.record_success("http://dead:11434")


# ─── SSE endpoint format ─────────────────────────────────────────────────

class TestSSEEndpoint:

    def test_traffic_history_format(self):
        """get_traffic_history returns correct shape for SSE."""
        from llm_gateway.tracking import get_traffic_history
        history = get_traffic_history(5)
        assert len(history) == 5
        for entry in history:
            assert "label" in entry
            assert "count" in entry
            assert isinstance(entry["count"], int)
            assert ":" in entry["label"]  # HH:MM format
