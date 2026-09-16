import os
import logging
import secrets
from typing import List, Optional, Dict
from datetime import datetime, timezone
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from cryptography.fernet import Fernet
import httpx

from ..models import LLMBot, LLMBotMessage

logger = logging.getLogger(__name__)


class BotService:
    def __init__(self) -> None:
        raw_key = os.getenv("ENCRYPTION_KEY")
        if not raw_key:
            raise RuntimeError(
                "ENCRYPTION_KEY environment variable is required but not set. "
                "Please configure ENCRYPTION_KEY to enable secure bot storage."
            )
        self.fernet = Fernet(raw_key.encode() if isinstance(raw_key, str) else raw_key)

    def encrypt(self, data: str) -> str:
        return self.fernet.encrypt(data.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self.fernet.decrypt(token.encode()).decode()

    async def create_bot(
        self,
        session: AsyncSession,
        name: str,
        platform: str,
        token: str,
        backend_name: str,
        model_name: Optional[str] = None,
        system_prompt: Optional[str] = None,
        enabled: bool = True,
        history_limit: int = 10,
    ) -> LLMBot:
        encrypted_token = self.encrypt(token)
        webhook_secret = secrets.token_hex(16)
        
        bot = LLMBot(
            name=name,
            platform=platform,
            encrypted_token=encrypted_token,
            backend_name=backend_name,
            model_name=model_name,
            system_prompt=system_prompt,
            webhook_secret=webhook_secret,
            enabled=enabled,
            history_limit=history_limit,
        )
        session.add(bot)
        return bot

    async def get_bot(self, session: AsyncSession, bot_id: int) -> Optional[LLMBot]:
        return await session.get(LLMBot, bot_id)

    async def update_bot(self, session: AsyncSession, bot_id: int, updates: Dict) -> Optional[LLMBot]:
        bot = await self.get_bot(session, bot_id)
        if not bot:
            return None
            
        for key, value in updates.items():
            if key == "token" and value:
                bot.encrypted_token = self.encrypt(value)
            elif value is not None and hasattr(bot, key):
                setattr(bot, key, value)
                
        session.add(bot)
        return bot

    async def delete_bot(self, session: AsyncSession, bot_id: int) -> bool:
        bot = await self.get_bot(session, bot_id)
        if bot:
            await session.delete(bot)
            return True
        return False

    async def list_bots(
        self, session: AsyncSession, *, skip: int = 0, limit: int = 50
    ) -> List[LLMBot]:
        query = select(LLMBot).offset(skip).limit(limit)
        result = await session.execute(query)
        return list(result.scalars().all())

    def get_decrypted_token(self, bot: LLMBot) -> str:
        return self.decrypt(bot.encrypted_token)

    async def register_telegram_webhook(self, bot: LLMBot, base_url: str) -> bool:
        """Call Telegram setWebhook API to direct bot updates to this gateway."""
        if bot.platform != "telegram":
            return False
            
        token = self.get_decrypted_token(bot)
        webhook_url = f"{base_url.rstrip('/')}/api/v1/bots/telegram/{bot.id}/webhook/{bot.webhook_secret}"
        telegram_url = f"https://api.telegram.org/bot{token}/setWebhook"
        
        logger.info("Registering Telegram webhook for bot %s (%d) to %s", bot.name, bot.id, webhook_url)
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    telegram_url,
                    json={
                        "url": webhook_url,
                        "secret_token": bot.webhook_secret,
                    }
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok"):
                        logger.info("Telegram webhook registered successfully for bot %d", bot.id)
                        return True
                    else:
                        logger.error("Telegram setWebhook returned error: %s", data.get("description"))
                else:
                    logger.error("Telegram setWebhook status code %d: %s", resp.status_code, resp.text)
        except Exception as e:
            logger.error("Failed to call Telegram setWebhook: %s", e)
            
        return False

    async def unregister_telegram_webhook(self, bot: LLMBot) -> bool:
        """Call Telegram deleteWebhook API."""
        if bot.platform != "telegram":
            return False
            
        token = self.get_decrypted_token(bot)
        telegram_url = f"https://api.telegram.org/bot{token}/deleteWebhook"
        
        logger.info("Deleting Telegram webhook for bot %s (%d)", bot.name, bot.id)
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(telegram_url)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok"):
                        logger.info("Telegram webhook deleted successfully for bot %d", bot.id)
                        return True
        except Exception as e:
            logger.error("Failed to call Telegram deleteWebhook: %s", e)
            
        return False

    async def get_conversation_history(
        self, session: AsyncSession, bot_id: int, chat_id: str, limit: int = 10
    ) -> List[Dict[str, str]]:
        """Retrieve last N messages from LLMBotMessage table formatted for prompt sequence."""
        query = (
            select(LLMBotMessage)
            .where(LLMBotMessage.bot_id == bot_id, LLMBotMessage.chat_id == chat_id)
            .order_by(LLMBotMessage.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(query)
        messages = list(result.scalars().all())
        
        # Order them chronologically (ascending) for LLM consumption
        messages.reverse()
        
        return [{"role": msg.role, "content": msg.content} for msg in messages]

    async def save_message(
        self, session: AsyncSession, bot_id: int, chat_id: str, role: str, content: str
    ) -> LLMBotMessage:
        """Record user or assistant message to database session."""
        msg = LLMBotMessage(bot_id=bot_id, chat_id=chat_id, role=role, content=content)
        session.add(msg)
        return msg

    async def respond_to_telegram(self, bot: LLMBot, chat_id: str, text: str) -> bool:
        """Send message text back to Telegram using sendMessage endpoint with HTML parser support."""
        token = self.get_decrypted_token(bot)
        telegram_url = f"https://api.telegram.org/bot{token}/sendMessage"
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    telegram_url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                    }
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return bool(data.get("ok"))
                else:
                    logger.error("Telegram sendMessage status code %d: %s", resp.status_code, resp.text)
        except Exception as e:
            logger.error("Failed to call Telegram sendMessage: %s", e)
            
        return False


def _make_bot_service() -> BotService:
    try:
        return BotService()
    except RuntimeError as e:
        logger.error(str(e))
        raise


bot_service = _make_bot_service()


def get_bot_service() -> BotService:
    return bot_service
