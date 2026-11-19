import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from llm_gateway.licensing.manager import (
    start_licensing,
    stop_licensing,
    licencia_checkin_loop,
)

@pytest.mark.asyncio
async def test_licensing_lifecycle_startup_and_shutdown():
    with patch("llm_gateway.licensing.manager.get_licencia_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        # Startup
        await start_licensing()
        mock_client.validate.assert_called_once()

        # Shutdown
        await stop_licensing()
        mock_client.deactivate.assert_called_once()

@pytest.mark.asyncio
async def test_licencia_checkin_loop_interval():
    with patch("llm_gateway.licensing.manager.get_licencia_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.state = MagicMock(check_in_interval=1)
        mock_get_client.return_value = mock_client

        # Run loop for a split second with mock sleep
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError]
            try:
                await licencia_checkin_loop(interval_hours=1)
            except asyncio.CancelledError:
                pass

            assert mock_client.validate.call_count >= 1
