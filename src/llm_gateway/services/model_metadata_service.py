"""Community Model Metadata Service.

Provides pricing, context windows, and modality intelligence for 450+ models
sourced from community-maintained datasets (models.dev/inference-gateway).
Features:
- Exact multi-tier token cost calculations (input, output, cache-read, cache-write)
- Maximum context window & completion limits
- Modalities validation (text, vision/image, audio, tools)
- Graceful image stripping for text-only models when VISION_ENABLED is active
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_pricing_table: Dict[str, dict] = {}
_context_window_table: Dict[str, dict] = {}
_modalities_table: Dict[str, dict] = {}
_loaded: bool = False


def _load_tables():
    global _pricing_table, _context_window_table, _modalities_table, _loaded
    if _loaded:
        return

    try:
        pricing_file = _DATA_DIR / "community_pricing.json"
        if pricing_file.exists():
            with open(pricing_file, "r", encoding="utf-8") as f:
                _pricing_table = json.load(f)

        cw_file = _DATA_DIR / "community_context_windows.json"
        if cw_file.exists():
            with open(cw_file, "r", encoding="utf-8") as f:
                _context_window_table = json.load(f)

        mod_file = _DATA_DIR / "community_modalities.json"
        if mod_file.exists():
            with open(mod_file, "r", encoding="utf-8") as f:
                _modalities_table = json.load(f)

        # Merge overrides if present
        for base_name, table in [
            ("community_pricing.overrides.json", _pricing_table),
            ("community_context_windows.overrides.json", _context_window_table),
            ("community_modalities.overrides.json", _modalities_table),
        ]:
            ovr_file = _DATA_DIR / base_name
            if ovr_file.exists():
                with open(ovr_file, "r", encoding="utf-8") as f:
                    ovr = json.load(f)
                    table.update(ovr)

        _loaded = True
        logger.debug(
            "Loaded model metadata: %d pricing, %d context windows, %d modalities",
            len(_pricing_table),
            len(_context_window_table),
            len(_modalities_table),
        )
    except Exception as exc:
        logger.warning("Failed to load community model metadata: %s", exc)


def _lookup_keys(model_name: str) -> List[str]:
    """Generate candidate keys for model lookup (e.g. 'openai/gpt-4o', 'gpt-4o')."""
    model_lower = model_name.lower().strip()
    keys = [model_lower]
    if "/" in model_lower:
        keys.append(model_lower.split("/", 1)[1])
    else:
        # Common provider prefixes to try
        for p in ["openai", "anthropic", "meta-llama", "deepseek", "qwen", "google", "mistralai", "cohere", "groq"]:
            keys.append(f"{p}/{model_lower}")
    return keys


def get_model_pricing(model_name: str) -> Optional[dict]:
    """Return token pricing dictionary for model, or None if unknown."""
    _load_tables()
    for k in _lookup_keys(model_name):
        if k in _pricing_table:
            return _pricing_table[k]
    return None


def get_model_context_window(model_name: str) -> Optional[dict]:
    """Return context window limits for model, or None if unknown."""
    _load_tables()
    for k in _lookup_keys(model_name):
        if k in _context_window_table:
            return _context_window_table[k]
    return None


def get_model_modalities(model_name: str) -> Optional[dict]:
    """Return modality capabilities dictionary for model, or None if unknown."""
    _load_tables()
    for k in _lookup_keys(model_name):
        if k in _modalities_table:
            return _modalities_table[k]
    return None


def model_accepts_images(model_name: str) -> bool:
    """Return False ONLY if model is explicitly documented as text-only (no image input).
    
    If model is unknown or absent from dataset, returns True (safe default).
    """
    _load_tables()
    mods = get_model_modalities(model_name)
    if mods and isinstance(mods, dict) and "input" in mods:
        inputs = mods["input"]
        return "image" in inputs
    return True


def calculate_token_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Calculate exact USD cost for token consumption based on community rates."""
    pricing = get_model_pricing(model_name)
    if not pricing:
        # Fallback default estimate: $1.50/M input, $6.00/M output
        return round((input_tokens * 1.5e-6) + (output_tokens * 6.0e-6), 6)

    try:
        inp_rate = float(pricing.get("input_per_token") or 0.0)
        out_rate = float(pricing.get("output_per_token") or 0.0)
        c_read_rate = float(pricing.get("cache_read_per_token") or inp_rate * 0.25)
        c_write_rate = float(pricing.get("cache_write_per_token") or inp_rate * 1.25)

        cost = (
            (input_tokens * inp_rate)
            + (output_tokens * out_rate)
            + (cache_read_tokens * c_read_rate)
            + (cache_write_tokens * c_write_rate)
        )
        return round(cost, 6)
    except Exception as exc:
        logger.debug("Cost calculation error for %s: %s", model_name, exc)
        return 0.0


def strip_images_if_unsupported(
    messages: List[Dict[str, Any]],
    model_name: str,
    vision_enabled: bool = True,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Strip or placeholder image parts if vision is enabled and model is text-only.
    
    Returns (cleaned_messages, was_modified).
    """
    if not vision_enabled or model_accepts_images(model_name):
        return messages, False

    was_modified = False
    cleaned_messages = []

    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            new_parts = []
            for part in content:
                if isinstance(part, dict):
                    part_type = part.get("type", "")
                    if part_type in ("image_url", "image"):
                        was_modified = True
                        new_parts.append({
                            "type": "text",
                            "text": "[Image omitted: model does not support image input]",
                        })
                    else:
                        new_parts.append(part)
                else:
                    new_parts.append(part)
            new_msg = dict(msg)
            new_msg["content"] = new_parts
            cleaned_messages.append(new_msg)
        else:
            cleaned_messages.append(msg)

    return cleaned_messages, was_modified
