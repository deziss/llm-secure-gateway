"""Slack bot integration service.

Handles Slack Events API webhooks (url_verification challenge, app_mention, messages).
"""

import json
import logging
from typing import Optional, Dict, Any
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import LLMBot, LLMBotMessage

logger = logging.getLogger(__name__)


class SlackBotService:
    @staticmethod
    async def post_message_to_slack(
        bot_token: str,
        channel: str,
        text: str,
        thread_ts: Optional[str] = None,
    ) -> bool:
        """Send message back to Slack channel using chat.postMessage."""
        url = "https://slack.com/api/chat.postMessage"
        headers = {
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        body: Dict[str, Any] = {"channel": channel, "text": text}
        if thread_ts:
            body["thread_ts"] = thread_ts

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(url, headers=headers, json=body)
                if res.status_code == 200 and res.json().get("ok"):
                    return True
                logger.warning("Slack postMessage error: %s", res.text)
        except Exception as exc:
            logger.error("Failed to post message to Slack: %s", exc)
        return False

    @staticmethod
    async def process_event(
        bot: LLMBot,
        payload: Dict[str, Any],
        session: AsyncSession,
        service: Any,
    ) -> Dict[str, Any]:
        """Process incoming Slack Events API payload."""
        # 1. URL Verification Challenge
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}

        # 2. Event Callback
        if payload.get("type") == "event_callback":
            event = payload.get("event", {})
            event_type = event.get("type")

            # Ignore bot messages to avoid infinite loops
            if event.get("bot_id") or event.get("subtype") == "bot_message":
                return {"ok": True}

            channel_id = str(event.get("channel", "general"))
            user_text = event.get("text", "")
            thread_ts = event.get("thread_ts") or event.get("ts")

            # Strip bot mention tag like <@U12345>
            import re
            clean_text = re.sub(r"<@[A-Z0-9]+>", "", user_text).strip()

            if clean_text:
                await service.save_message(session, bot.id, channel_id, "user", clean_text)
                history = await service.get_conversation_history(
                    session, bot.id, channel_id, limit=bot.history_limit
                )

                messages = []
                if bot.system_prompt:
                    messages.append({"role": "system", "content": bot.system_prompt})
                messages.extend(history)

                reply_text = "I could not process your request at this time."
                try:
                    from ..services import get_config_service
                    config_service = get_config_service()
                    backend = await config_service.get_backend(session, bot.backend_name)
                    if backend:
                        token = service.get_decrypted_token(bot)
                        model = bot.model_name or (backend.models[0] if backend.models else "default")
                        
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            resp = await client.post(
                                f"{backend.base_url.rstrip('/')}/v1/chat/completions",
                                headers={
                                    "Authorization": f"Bearer {token}",
                                    "Content-Type": "application/json",
                                },
                                json={"model": model, "messages": messages},
                            )
                            if resp.status_code == 200:
                                res_json = resp.json()
                                reply_text = (
                                    res_json.get("choices", [{}])[0]
                                    .get("message", {})
                                    .get("content", reply_text)
                                )
                except Exception as exc:
                    logger.error("Error generating Slack bot response: %s", exc)
                    reply_text = f"Error: {exc}"

                await service.save_message(session, bot.id, channel_id, "assistant", reply_text)
                await session.commit()

                bot_token = service.get_decrypted_token(bot)
                await SlackBotService.post_message_to_slack(
                    bot_token, channel_id, reply_text, thread_ts=thread_ts
                )

            return {"ok": True}

        return {"ok": True}
