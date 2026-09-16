import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
import respx
import httpx

from llm_gateway.models import LLMBot, LLMBotMessage, LLMBackend, BackendType
from llm_gateway.services import get_bot_service, get_config_service


@pytest.mark.asyncio
async def test_bot_encryption_and_decryption(db_session: AsyncSession):
    bot_service = get_bot_service()
    
    # Create test bot
    original_token = "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ"
    bot = await bot_service.create_bot(
        session=db_session,
        name="Security Bot",
        platform="telegram",
        token=original_token,
        backend_name="test-backend",
        model_name="gpt-4",
        system_prompt="Be secure.",
        history_limit=5
    )
    
    db_session.add(bot)
    await db_session.commit()
    await db_session.refresh(bot)
    
    # Ensure encrypted token is masked/different from plain-text
    assert bot.encrypted_token != original_token
    
    # Ensure decrypt method retrieves exact original token
    decrypted = bot_service.get_decrypted_token(bot)
    assert decrypted == original_token


@pytest.mark.asyncio
async def test_bot_admin_crud(db_session: AsyncSession, client: AsyncClient, admin_token_headers):
    config_service = get_config_service()
    
    # Create a backend first (prerequisite for bot creation)
    backend = LLMBackend(
        name="test-ollama",
        backend_type=BackendType.OLLAMA,
        base_url="http://localhost:11434",
        models=["llama3.2:latest"],
        allowed_endpoints=["/v1/chat/completions"]
    )
    await config_service.register_backend(db_session, backend)
    await db_session.commit()
    
    # 1. Create Bot
    create_payload = {
        "name": "Admin Bot Assistant",
        "platform": "telegram",
        "token": "bot_token_abc_123",
        "backend_name": "test-ollama",
        "model_name": "llama3.2:latest",
        "system_prompt": "Answer shortly.",
        "enabled": True,
        "history_limit": 8
    }
    
    # Mock Telegram setWebhook request inside bot creation
    with respx.mock:
        respx.post("https://api.telegram.org/botbot_token_abc_123/setWebhook").respond(
            json={"ok": True, "description": "Webhook set"}
        )
        
        resp = await client.post("/admin/bots", json=create_payload, headers=admin_token_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Admin Bot Assistant"
        assert data["backend_name"] == "test-ollama"
        assert data["enabled"] is True
        bot_id = data["id"]
        
    # 2. List Bots
    resp = await client.get("/admin/bots", headers=admin_token_headers)
    assert resp.status_code == 200
    bots_list = resp.json()
    assert len(bots_list) >= 1
    # Check that plain token is NOT exposed in list response
    assert "token" not in bots_list[0]
    assert "encrypted_token" not in bots_list[0]
    
    # 3. Update Bot
    update_payload = {
        "name": "Updated Admin Bot Assistant",
        "enabled": False
    }
    
    with respx.mock:
        respx.post("https://api.telegram.org/botbot_token_abc_123/deleteWebhook").respond(
            json={"ok": True}
        )
        
        resp = await client.patch(f"/admin/bots/{bot_id}", json=update_payload, headers=admin_token_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Updated Admin Bot Assistant"
        assert data["enabled"] is False
        
    # 4. Delete Bot
    resp = await client.delete(f"/admin/bots/{bot_id}", headers=admin_token_headers)
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}


@pytest.mark.asyncio
async def test_telegram_webhook_handling(db_session: AsyncSession, client: AsyncClient):
    config_service = get_config_service()
    bot_service = get_bot_service()
    
    # Set up backend
    backend = LLMBackend(
        name="test-openai",
        backend_type=BackendType.OPENAI,
        base_url="https://api.openai.com",
        api_key="sk-proj-test",
        models=["gpt-4-turbo"],
        allowed_endpoints=["/v1/chat/completions"]
    )
    await config_service.register_backend(db_session, backend)
    
    # Set up bot
    bot = await bot_service.create_bot(
        session=db_session,
        name="Interactive Bot",
        platform="telegram",
        token="tg_token_999",
        backend_name="test-openai",
        model_name="gpt-4-turbo",
        system_prompt="You speak like a pirate.",
        enabled=True,
        history_limit=10
    )
    db_session.add(bot)
    await db_session.commit()
    await db_session.refresh(bot)
    
    secret = bot.webhook_secret
    webhook_url = f"/api/v1/bots/telegram/{bot.id}/webhook/{secret}"
    
    # 1. Test Webhook Command: /start
    with respx.mock as mock:
        mock.post("https://api.telegram.org/bottg_token_999/sendMessage").respond(json={"ok": True})
        
        start_payload = {
            "update_id": 10001,
            "message": {
                "message_id": 1,
                "chat": {"id": 12345, "type": "private"},
                "text": "/start"
            }
        }
        resp = await client.post(webhook_url, json=start_payload)
        assert resp.status_code == 200
        assert resp.json()["action"] == "sent_greeting"
        
    # 2. Test Webhook standard chat message relay with model mock response
    with respx.mock as mock:
        # Mock downstream LLM completion response
        mock.post("https://api.openai.com/v1/chat/completions").respond(
            json={
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "<think>He is asking a question</think>Ahoy, matey! Welcome aboard."
                    }
                }]
            }
        )
        
        # Mock Telegram response relay
        mock.post("https://api.telegram.org/bottg_token_999/sendMessage").respond(json={"ok": True})
        
        chat_payload = {
            "update_id": 10002,
            "message": {
                "message_id": 2,
                "chat": {"id": 12345, "type": "private"},
                "text": "Hello bot"
            }
        }
        
        resp = await client.post(webhook_url, json=chat_payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        
        # Verify history database entries were written
        hist_res = await db_session.execute(
            select(LLMBotMessage).where(LLMBotMessage.bot_id == bot.id, LLMBotMessage.chat_id == "12345")
        )
        messages = hist_res.scalars().all()
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].content == "Hello bot"
        assert messages[1].role == "assistant"
        assert messages[1].content == "<think>He is asking a question</think>Ahoy, matey! Welcome aboard."
