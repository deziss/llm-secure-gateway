"""Content-capture levels for observability (governance).

OBSERVABILITY_CAPTURE_LEVEL controls whether prompt/response text is stored
alongside agent-step metadata:

* ``metadata`` (default) -- no content is ever captured (current behaviour).
* ``redacted``           -- content is PII-redacted (when the PII service is
  available) and truncated to OBSERVABILITY_MAX_CONTENT_CHARS.
* ``full``               -- content is stored verbatim up to the max chars.

Everything here is best-effort and must never raise: capture failures degrade
to "no content" rather than breaking the request path. Levels are read lazily
from the config module so tests (and runtime tooling) can monkeypatch them.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

CAPTURE_LEVELS = ("metadata", "redacted", "full")

# Hard cap on text we even consider joining/serializing before truncation,
# so a pathological multi-MB body can't bloat memory on the hot path.
_PRE_TRUNCATE_CHARS = 65536


def get_capture_level() -> str:
    """Current capture level, read from config at call time (monkeypatch-friendly)."""
    from .. import config

    level = getattr(config, "OBSERVABILITY_CAPTURE_LEVEL", "metadata")
    return level if level in CAPTURE_LEVELS else "metadata"


def get_max_content_chars() -> int:
    from .. import config

    try:
        return max(1, int(getattr(config, "OBSERVABILITY_MAX_CONTENT_CHARS", 2000)))
    except (TypeError, ValueError):
        return 2000


def captures_content() -> bool:
    return get_capture_level() != "metadata"


def extract_last_user_text(body: Optional[dict]) -> Optional[str]:
    """Best-effort extraction of prompt text from an OpenAI/Anthropic-style JSON body.

    Prefers the last user message; falls back to a truncated JSON dump so that
    at least *something* representative is captured at the higher levels.
    """
    if not isinstance(body, dict):
        return None
    try:
        messages = body.get("messages")
        if isinstance(messages, list) and messages:
            for msg in reversed(messages):
                if not isinstance(msg, dict):
                    continue
                if msg.get("role") != "user":
                    continue
                content = msg.get("content")
                if isinstance(content, str) and content:
                    return content[:_PRE_TRUNCATE_CHARS]
                if isinstance(content, list):
                    # multimodal: join text parts
                    parts = [
                        p.get("text", "")
                        for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    ]
                    joined = "\n".join(p for p in parts if p)
                    if joined:
                        return joined[:_PRE_TRUNCATE_CHARS]
        # Anthropic top-level "prompt" or plain prompt field
        prompt = body.get("prompt")
        if isinstance(prompt, str) and prompt:
            return prompt[:_PRE_TRUNCATE_CHARS]
        return None
    except Exception:  # pragma: no cover - defensive
        return None


def extract_text_from_data(data: Any) -> Optional[str]:
    """Best-effort extraction of assistant text from a parsed (non-stream) response body."""
    if not isinstance(data, dict):
        return None
    try:
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            msg = first.get("message") if isinstance(first.get("message"), dict) else {}
            text = msg.get("content") or first.get("text")
            if isinstance(text, str) and text:
                return text[:_PRE_TRUNCATE_CHARS]
        content = data.get("content")
        if isinstance(content, list):
            parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            joined = "\n".join(p for p in parts if p)
            if joined:
                return joined[:_PRE_TRUNCATE_CHARS]
        text = data.get("completion")
        if isinstance(text, str) and text:
            return text[:_PRE_TRUNCATE_CHARS]
        return None
    except Exception:  # pragma: no cover - defensive
        return None


def _redact(text: str) -> str:
    """PII-redact via the configured scanner; on any failure return the text
    unchanged (redacted level falls back to truncate-only, flagged below)."""
    try:
        from ..services.pii_service import get_pii_scanner

        scanner = get_pii_scanner()
        if scanner is None:
            return text
        redacted, _findings = scanner.redact(text)
        return redacted if isinstance(redacted, str) else text
    except Exception:  # pragma: no cover - PII service optional / may be down
        logger.debug("capture: PII redaction unavailable, storing unredacted truncated text", exc_info=True)
        return text


def prepare_captured_content(
    text: Optional[str],
    level: Optional[str] = None,
    max_chars: Optional[int] = None,
) -> Optional[str]:
    """Apply the capture level to a piece of content.

    Returns None when nothing should be stored. Never raises. Truncation is
    applied AFTER redaction so the stored value is always <= max_chars.
    """
    if text is None:
        return None
    try:
        lvl = level if level in CAPTURE_LEVELS else get_capture_level()
        if lvl == "metadata":
            return None
        cap = max_chars if (isinstance(max_chars, int) and max_chars > 0) else get_max_content_chars()
        s = text if len(text) <= _PRE_TRUNCATE_CHARS else text[:_PRE_TRUNCATE_CHARS]
        if lvl == "redacted":
            s = _redact(s)
        return s[:cap]
    except Exception:  # pragma: no cover - capture must never break requests
        return None


def safe_json_dumps(obj: Any) -> Optional[str]:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)[:_PRE_TRUNCATE_CHARS]
    except Exception:  # pragma: no cover
        return None
