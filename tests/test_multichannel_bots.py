import pytest
from llm_gateway.models import LLMBot
from llm_gateway.services.discord_bot import DiscordBotService
from llm_gateway.services.slack_bot import SlackBotService


@pytest.mark.asyncio
async def test_discord_ping():
    # Discord sends type=1 for ping
    res = await DiscordBotService.process_interaction(
        bot=None, payload={"type": 1}, session=None, service=None
    )
    assert res == {"type": 1}


@pytest.mark.asyncio
async def test_slack_url_verification():
    # Slack sends type=url_verification with challenge string
    res = await SlackBotService.process_event(
        bot=None, payload={"type": "url_verification", "challenge": "secret-challenge-123"},
        session=None, service=None
    )
    assert res == {"challenge": "secret-challenge-123"}
