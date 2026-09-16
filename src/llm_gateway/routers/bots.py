import logging
import os
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from pydantic import BaseModel
from sqlmodel import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from ..models import LLMBot, LLMBotMessage, LLMBotResponse, LLMBackend
from ..services import get_bot_service, get_config_service, BotService, ConfigService
from ..database import get_session
from ..pagination import pagination_params
from .admin import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bots"])

# Admin Bot update models
class BotCreate(BaseModel):
    name: str
    platform: str
    token: str
    backend_name: str
    model_name: Optional[str] = None
    system_prompt: Optional[str] = None
    enabled: bool = True
    history_limit: int = 10

class BotUpdate(BaseModel):
    name: Optional[str] = None
    platform: Optional[str] = None
    token: Optional[str] = None
    backend_name: Optional[str] = None
    model_name: Optional[str] = None
    system_prompt: Optional[str] = None
    enabled: Optional[bool] = None
    history_limit: Optional[int] = None


def get_base_url(request: Request) -> str:
    """Resolve APP_BASE_URL dynamically from env or the request host."""
    env_base = os.getenv("APP_BASE_URL", "")
    if env_base:
        return env_base.rstrip("/")
    return str(request.base_url).rstrip("/")


# ---------------------------------------------------------------------------
# Admin UI CRUD routes
# ---------------------------------------------------------------------------

@router.get("/admin/bots", response_model=List[LLMBotResponse])
async def list_bots(
    service: BotService = Depends(get_bot_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin),
    pagination: tuple = Depends(pagination_params),
) -> list:
    skip, limit = pagination
    bots = await service.list_bots(session, skip=skip, limit=limit)
    return [
        LLMBotResponse(
            id=b.id,
            name=b.name,
            platform=b.platform,
            backend_name=b.backend_name,
            model_name=b.model_name,
            system_prompt=b.system_prompt,
            enabled=b.enabled,
            history_limit=b.history_limit,
            created_at=b.created_at,
        )
        for b in bots
    ]


@router.post("/admin/bots", response_model=LLMBotResponse)
async def create_bot(
    req: BotCreate,
    request: Request,
    service: BotService = Depends(get_bot_service),
    config_service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin),
) -> LLMBotResponse:
    # Verify backend exists
    backend = await config_service.get_backend(session, req.backend_name)
    if not backend:
        raise HTTPException(status_code=400, detail=f"Backend '{req.backend_name}' does not exist")
        
    bot = await service.create_bot(
        session,
        name=req.name,
        platform=req.platform,
        token=req.token,
        backend_name=req.backend_name,
        model_name=req.model_name,
        system_prompt=req.system_prompt,
        enabled=req.enabled,
        history_limit=req.history_limit,
    )
    
    # Save first to get an ID
    session.add(bot)
    await session.commit()
    await session.refresh(bot)
    
    # Auto-register webhook with Telegram if enabled
    if bot.enabled and bot.platform == "telegram":
        base_url = get_base_url(request)
        await service.register_telegram_webhook(bot, base_url)
        
    return LLMBotResponse(
        id=bot.id,
        name=bot.name,
        platform=bot.platform,
        backend_name=bot.backend_name,
        model_name=bot.model_name,
        system_prompt=bot.system_prompt,
        enabled=bot.enabled,
        history_limit=bot.history_limit,
        created_at=bot.created_at,
    )


@router.patch("/admin/bots/{bot_id}", response_model=LLMBotResponse)
async def update_bot(
    bot_id: int,
    req: BotUpdate,
    request: Request,
    service: BotService = Depends(get_bot_service),
    config_service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin),
) -> LLMBotResponse:
    bot = await service.get_bot(session, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
        
    updates = req.model_dump(exclude_unset=True) if hasattr(req, "model_dump") else req.dict(exclude_unset=True)
    if "backend_name" in updates and updates["backend_name"]:
        backend = await config_service.get_backend(session, updates["backend_name"])
        if not backend:
            raise HTTPException(status_code=400, detail=f"Backend '{updates['backend_name']}' does not exist")
            
    old_token = bot.encrypted_token
    old_enabled = bot.enabled
    
    await service.update_bot(session, bot_id, updates)
    await session.commit()
    await session.refresh(bot)
    
    # Re-sync webhook if enabled status or token changed
    if bot.platform == "telegram":
        base_url = get_base_url(request)
        if bot.enabled and (not old_enabled or bot.encrypted_token != old_token):
            await service.register_telegram_webhook(bot, base_url)
        elif not bot.enabled and old_enabled:
            await service.unregister_telegram_webhook(bot)
            
    return LLMBotResponse(
        id=bot.id,
        name=bot.name,
        platform=bot.platform,
        backend_name=bot.backend_name,
        model_name=bot.model_name,
        system_prompt=bot.system_prompt,
        enabled=bot.enabled,
        history_limit=bot.history_limit,
        created_at=bot.created_at,
    )


@router.delete("/admin/bots/{bot_id}")
async def delete_bot(
    bot_id: int,
    service: BotService = Depends(get_bot_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin),
) -> dict:
    bot = await service.get_bot(session, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
        
    # Unregister Telegram webhook first
    if bot.enabled and bot.platform == "telegram":
        await service.unregister_telegram_webhook(bot)
        
    await service.delete_bot(session, bot_id)
    await session.commit()
    return {"status": "deleted"}


@router.post("/admin/bots/{bot_id}/webhook/sync")
async def sync_webhook(
    bot_id: int,
    request: Request,
    service: BotService = Depends(get_bot_service),
    session: AsyncSession = Depends(get_session),
    user = Depends(require_admin),
) -> dict:
    bot = await service.get_bot(session, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
        
    if bot.platform != "telegram":
        return {"status": "skipped", "reason": "Webhook registration only supported for Telegram"}
        
    base_url = get_base_url(request)
    success = await service.register_telegram_webhook(bot, base_url)
    if success:
        return {"status": "synced"}
    raise HTTPException(status_code=502, detail="Failed to sync webhook with Telegram server")


# ---------------------------------------------------------------------------
# Public Webhook Callback Endpoint
# ---------------------------------------------------------------------------

@router.post("/api/v1/bots/telegram/{bot_id}/webhook/{secret}")
async def telegram_webhook(
    bot_id: int,
    secret: str,
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
    service: BotService = Depends(get_bot_service),
    config_service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
) -> dict:
    # 1. Fetch bot details
    bot = await service.get_bot(session, bot_id)
    if not bot or not bot.enabled:
        raise HTTPException(status_code=404, detail="Bot not active")
        
    # 2. Security validation
    if bot.webhook_secret != secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    if x_telegram_bot_api_secret_token and x_telegram_bot_api_secret_token != bot.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid Telegram secret token header")
        
    # 3. Parse incoming Telegram payload
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
        
    message = payload.get("message")
    if not message or "text" not in message or "chat" not in message:
        return {"status": "ignored", "reason": "No text message in update"}
        
    chat_id = str(message["chat"]["id"])
    text = message["text"].strip()
    
    # 4. Command Processor
    if text.startswith("/"):
        cmd = text.split()[0].lower()
        if cmd in ("/start", "/help"):
            greeting = (
                f"🤖 <b>Welcome to {bot.name}!</b>\n\n"
                f"I am connected to the Secure Gateway backend: <code>{bot.backend_name}</code>.\n"
                f"Send any message to chat with me! You can also type /clear to reset our conversation."
            )
            await service.respond_to_telegram(bot, chat_id, greeting)
            return {"status": "ok", "action": "sent_greeting"}
            
        elif cmd == "/clear":
            # Delete log messages
            await session.execute(
                delete(LLMBotMessage).where(LLMBotMessage.bot_id == bot.id, LLMBotMessage.chat_id == chat_id)
            )
            await session.commit()
            await service.respond_to_telegram(bot, chat_id, "🧹 <i>Conversation history cleared!</i>")
            return {"status": "ok", "action": "cleared_history"}
            
    # 5. Core Chat Proxy Flow
    # Save the user's message
    await service.save_message(session, bot.id, chat_id, "user", text)
    await session.commit()
    
    # Get conversational history (excluding current user message to avoid duplicate addition)
    history = await service.get_conversation_history(session, bot.id, chat_id, limit=bot.history_limit)
    
    # Resolve backend configuration
    backend = await config_service.get_backend(session, bot.backend_name)
    if not backend:
        await service.respond_to_telegram(bot, chat_id, "⚠️ <i>Configuration Error: Configured gateway backend was not found.</i>")
        return {"status": "error", "reason": "Backend not found"}
        
    # Resolve model override
    model_name = bot.model_name
    if not model_name:
        model_name = backend.models[0] if backend.models else "gpt-3.5-turbo"
        
    # Build core OpenAI completion messages list
    messages = []
    if bot.system_prompt:
        messages.append({"role": "system", "content": bot.system_prompt})
    messages.extend(history)
    
    # Construct standard completions payload
    proxy_payload = {
        "model": model_name,
        "messages": messages,
        "stream": False,
    }
    
    # 6. Execute direct downstream HTTP query to the LLM backend
    url = f"{backend.base_url.rstrip('/')}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    
    # Inject authorization keys
    if backend.api_key:
        raw_key = backend.api_key.get_secret_value() if hasattr(backend.api_key, "get_secret_value") else backend.api_key
        headers["Authorization"] = f"Bearer {raw_key}"
        
    # Translate query protocol if translation bridge is operational
    enable_translation = await config_service.get_setting(session, "ENABLE_PROTOCOL_TRANSLATION", "false")
    if enable_translation.lower() == "true" and backend.translation_mode != "none":
        from ..translation import translate_request_body
        proxy_payload = translate_request_body(backend.translation_mode, proxy_payload)
        # Update URL if translated to Anthropic format
        if backend.translation_mode == "openai_to_anthropic":
            url = f"{backend.base_url.rstrip('/')}/v1/messages"
            
    logger.info("Bot %d routing conversation to backend %s: %s", bot.id, backend.name, url)
    
    assistant_reply = ""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=proxy_payload, headers=headers)
            if resp.status_code == 200:
                resp_data = resp.json()
                
                # Parse standard completions shapes
                if "choices" in resp_data and resp_data["choices"]:
                    assistant_reply = resp_data["choices"][0]["message"].get("content", "")
                elif "content" in resp_data: # Anthropic shape
                    # If multiple blocks
                    content_blocks = resp_data["content"]
                    if isinstance(content_blocks, list):
                        assistant_reply = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
                    else:
                        assistant_reply = str(content_blocks)
            else:
                logger.error("Downstream LLM responded with error status %d: %s", resp.status_code, resp.text)
                await service.respond_to_telegram(bot, chat_id, "⚠️ <i>Error: Downstream LLM backend responded with a connection failure.</i>")
                return {"status": "error", "reason": "Downstream backend error"}
    except Exception as e:
        logger.error("Failed to proxy bot request to downstream backend %s: %s", backend.name, e)
        await service.respond_to_telegram(bot, chat_id, "⚠️ <i>Error: Gateway failed to establish backend socket connection.</i>")
        return {"status": "error", "reason": "Downstream connection failed"}
        
    if assistant_reply:
        # 7. Apply Reasoning Block Normalization / Strip Thinking blocks if backend normalizes it
        raw_reply = assistant_reply
        if backend.normalize_thinking:
            import re
            # Extract thinking tag block if present
            think_match = re.search(r"<think>(.*?)</think>", assistant_reply, re.DOTALL)
            if think_match:
                thinking_text = think_match.group(1).strip()
                # Clean assistant reply to exclude thinking tag
                assistant_reply = re.sub(r"<think>.*?</think>", "", assistant_reply, flags=re.DOTALL).strip()
                
                # Check if we should append thinking formatted in HTML or strip it
                enable_thinking_normalizer = await config_service.get_setting(session, "ENABLE_THINKING_NORMALIZATION", "false")
                if enable_thinking_normalizer.lower() == "true":
                    # Append it neatly formatted
                    telegram_text = f"💭 <i>Thinking...</i>\n<blockquote>{thinking_text}</blockquote>\n\n{assistant_reply}"
                else:
                    telegram_text = assistant_reply
            else:
                telegram_text = assistant_reply
        else:
            telegram_text = assistant_reply
            
        # 8. Save assistant reply log and post response to Telegram
        await service.save_message(session, bot.id, chat_id, "assistant", raw_reply)
        await session.commit()
        
        await service.respond_to_telegram(bot, chat_id, telegram_text)
        return {"status": "ok", "reply_len": len(telegram_text)}
        
    return {"status": "error", "reason": "No response text was generated"}


# ---------------------------------------------------------------------------
# Discord Webhook Callback Endpoint
# ---------------------------------------------------------------------------

@router.post("/api/v1/bots/discord/{bot_id}/webhook/{secret}")
async def discord_webhook(
    bot_id: int,
    secret: str,
    request: Request,
    service: BotService = Depends(get_bot_service),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Discord Interaction Webhook receiver."""
    import json
    bot = await service.get_bot(session, bot_id)
    if not bot or not bot.enabled:
        raise HTTPException(status_code=404, detail="Bot not active")
    if bot.webhook_secret != secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    raw_body = await request.body()
    sig = request.headers.get("X-Signature-Ed25519", "")
    timestamp = request.headers.get("X-Signature-Timestamp", "")

    from ..services.discord_bot import DiscordBotService
    if sig and timestamp:
        public_key = service.get_decrypted_token(bot)
        if not DiscordBotService.verify_interaction_signature(public_key, sig, timestamp, raw_body):
            raise HTTPException(status_code=401, detail="Invalid request signature")

    payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    return await DiscordBotService.process_interaction(bot, payload, session, service)


# ---------------------------------------------------------------------------
# Slack Webhook Callback Endpoint
# ---------------------------------------------------------------------------

@router.post("/api/v1/bots/slack/{bot_id}/webhook/{secret}")
async def slack_webhook(
    bot_id: int,
    secret: str,
    request: Request,
    service: BotService = Depends(get_bot_service),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Slack Events API Webhook receiver."""
    bot = await service.get_bot(session, bot_id)
    if not bot or not bot.enabled:
        raise HTTPException(status_code=404, detail="Bot not active")
    if bot.webhook_secret != secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    payload = await request.json()
    from ..services.slack_bot import SlackBotService
    return await SlackBotService.process_event(bot, payload, session, service)
