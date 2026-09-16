"""Tests for API key auto-rotation policy (KEY_EXPIRY_DAYS)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta


class TestKeyExpiryPolicy:

    def _make_session(self):
        session = AsyncMock()
        session.add = MagicMock()
        return session

    @pytest.mark.asyncio
    async def test_no_expiry_when_setting_is_zero(self):
        """KEY_EXPIRY_DAYS=0 means keys never expire."""
        from llm_gateway.services.auth_service import AuthService
        from llm_gateway.models import APIKeyCreate

        service = AuthService()
        data = APIKeyCreate(owner="user:test", scopes=["llm:chat"])
        session = self._make_session()

        with patch("llm_gateway.services.config_service.get_config_service") as mock_cfg:
            mock_cfg.return_value.get_setting = AsyncMock(return_value="0")
            raw_key, api_key = await service.create_api_key(session, data)

        assert api_key.expires_at is None

    @pytest.mark.asyncio
    async def test_expiry_set_when_days_positive(self):
        """KEY_EXPIRY_DAYS=30 sets expires_at ~30 days from now."""
        from llm_gateway.services.auth_service import AuthService
        from llm_gateway.models import APIKeyCreate

        service = AuthService()
        data = APIKeyCreate(owner="user:test", scopes=["llm:chat"])
        session = self._make_session()

        with patch("llm_gateway.services.config_service.get_config_service") as mock_cfg:
            mock_cfg.return_value.get_setting = AsyncMock(return_value="30")
            raw_key, api_key = await service.create_api_key(session, data)

        assert api_key.expires_at is not None
        expected = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)
        assert abs((api_key.expires_at - expected).total_seconds()) < 5

    @pytest.mark.asyncio
    async def test_explicit_expiry_overrides_policy(self):
        """Caller-provided expires_at takes precedence over policy."""
        from llm_gateway.services.auth_service import AuthService
        from llm_gateway.models import APIKeyCreate

        service = AuthService()
        explicit = datetime(2026, 12, 31)
        data = APIKeyCreate(owner="user:test", scopes=["llm:chat"], expires_at=explicit)
        session = self._make_session()

        with patch("llm_gateway.services.config_service.get_config_service") as mock_cfg:
            mock_cfg.return_value.get_setting = AsyncMock(return_value="30")
            raw_key, api_key = await service.create_api_key(session, data)

        assert api_key.expires_at == explicit

    @pytest.mark.asyncio
    async def test_config_failure_defaults_to_no_expiry(self):
        """If reading KEY_EXPIRY_DAYS fails, key has no expiry."""
        from llm_gateway.services.auth_service import AuthService
        from llm_gateway.models import APIKeyCreate

        service = AuthService()
        data = APIKeyCreate(owner="user:test", scopes=["llm:chat"])
        session = self._make_session()

        with patch("llm_gateway.services.config_service.get_config_service") as mock_cfg:
            mock_cfg.return_value.get_setting = AsyncMock(side_effect=Exception("DB down"))
            raw_key, api_key = await service.create_api_key(session, data)

        assert api_key.expires_at is None
