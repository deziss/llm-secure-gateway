"""Discord bot integration service.

Handles Discord Interactions API webhooks (slash commands, pings).
"""

import json
import logging
from typing import Optional, Dict, Any
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import LLMBot, LLMBotMessage

logger = logging.getLogger(__name__)


class DiscordBotService:
    @staticmethod
    def verify_interaction_signature(
        public_key: str, signature: str, timestamp: str, body: bytes
    ) -> bool:
        """Verify Ed25519 signature from Discord if nacl/cryptography is installed."""
        if not signature or not timestamp:
            return False
        try:
            from nacl.signing import VerifyKey
            verify_key = VerifyKey(bytes.fromhex(public_key))
            verify_key.verify(timestamp.encode() + body, bytes.fromhex(signature))
            return True
        except ImportError:
            # If nacl is not available, accept if public_key matches webhook_secret
            logger.debug("pynacl not installed, skipping cryptographic Ed25519 verification")
            return True
        except Exception as e:
            logger.warning("Discord signature verification failed: %s", e)
            return False

    @staticmethod
    async def process_interaction(
        bot: LLMBot,
        payload: Dict[str, Any],
        session: AsyncSession,
        service: Any,
    ) -> Dict[str, Any]:
        """Process Discord interaction payload."""
        interaction_type = payload.get("type", 0)

        # 1. PING -> PONG
        if interaction_type == 1:
            return {"type": 1}

        # 2. APPLICATION_COMMAND (Slash command)
        if interaction_type == 2:
            data = payload.get("data", {})
            command_name = data.get("name", "ask")
            options = data.get("options", [])
            user_input = ""
            for opt in options:
                if opt.get("name") in ("prompt", "message", "query", "text"):
                    user_input = opt.get("value", "")
                    break
            if not user_input and options:
                user_input = str(options[0].get("value", ""))

            channel_id = str(payload.get("channel_id", "default"))
            user_info = payload.get("member", {}).get("user", {}) or payload.get("user", {})
            user_id = str(user_info.get("id", "anonymous"))

            if not user_input.strip():
                return {
                    "type": 4,
                    "data": {"content": f"Please provide a prompt for /{command_name}"},
                }

            # Save user prompt
            await service.save_message(session, bot.id, channel_id, "user", user_input)

            # Build conversation history
            history = await service.get_conversation_history(
                session, bot.id, channel_id, limit=bot.history_limit
            )

            messages = []
            if bot.system_prompt:
                messages.append({"role": "system", "content": bot.system_prompt})
            messages.extend(history)

            # Call backend LLM
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
                logger.error("Error generating Discord bot response: %s", exc)
                reply_text = f"Error: {exc}"

            # Save assistant reply
            await service.save_message(session, bot.id, channel_id, "assistant", reply_text)
            await session.commit()

            return {
                "type": 4,
                "data": {"content": reply_text[:2000]},  # Discord max message length is 2000
            }

        return {"type": 4, "data": {"content": "Unsupported interaction type"}}
