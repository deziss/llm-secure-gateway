from enum import Enum
from typing import AsyncGenerator, Optional
from . import anthropic_to_openai, openai_to_anthropic

class TranslationMode(str, Enum):
    NONE = "none"
    ANTHROPIC_TO_OPENAI = "anthropic_to_openai"
    OPENAI_TO_ANTHROPIC = "openai_to_anthropic"

def translate_request_body(mode: str, body: dict) -> dict:
    """Translate request body based on the configured translation mode."""
    if not body:
        return body
        
    if mode == TranslationMode.ANTHROPIC_TO_OPENAI or mode == "anthropic_to_openai":
        return anthropic_to_openai.translate_request(body)
    elif mode == TranslationMode.OPENAI_TO_ANTHROPIC or mode == "openai_to_anthropic":
        return openai_to_anthropic.translate_request(body)
    return body

def translate_response_stream(mode: str, raw_response) -> AsyncGenerator[bytes, None]:
    """Translate streaming response chunks based on the configured translation mode."""
    if mode == TranslationMode.ANTHROPIC_TO_OPENAI or mode == "anthropic_to_openai":
        return anthropic_to_openai.translate_stream(raw_response)
    elif mode == TranslationMode.OPENAI_TO_ANTHROPIC or mode == "openai_to_anthropic":
        return openai_to_anthropic.translate_stream(raw_response)
    
    # Fallback to direct raw iterator if no translation configured
    return raw_response.aiter_raw()
