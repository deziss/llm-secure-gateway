"""Autonomous JSON Response Healing & Schema Repair Engine.

Repairs malformed or truncated JSON strings emitted by LLMs during structured
outputs, tool calling, and JSON schema mode.
"""

import json
import logging
import re
from typing import Any, Tuple

logger = logging.getLogger(__name__)


def extract_json_from_markdown(text: str) -> str:
    """Extract JSON content from markdown code fence blocks if present."""
    if not text:
        return text

    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if match:
        candidate = match.group(1).strip()
        if candidate.startswith(("{", "[")):
            return candidate

    return text.strip()


def remove_trailing_commas(json_str: str) -> str:
    """Remove trailing commas before closing braces and brackets."""
    # Match comma followed by whitespace and a closing brace or bracket
    return re.sub(r",\s*([\]}])", r"\g<1>", json_str)


def repair_truncated_json(text: str) -> str:
    """Attempt to balance unclosed brackets and quotation marks."""
    s = text.strip()
    if not s:
        return s

    in_string = False
    escape = False
    open_brackets = []

    for char in s:
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            if char in ("{", "["):
                open_brackets.append(char)
            elif char == "}":
                if open_brackets and open_brackets[-1] == "{":
                    open_brackets.pop()
            elif char == "]":
                if open_brackets and open_brackets[-1] == "[":
                    open_brackets.pop()

    if in_string:
        s += '"'

    s = re.sub(r",\s*$", "", s)

    for b in reversed(open_brackets):
        if b == "{":
            s += "}"
        elif b == "[":
            s += "]"

    return s


def heal_json(raw_text: str) -> Tuple[bool, Any, str]:
    """Heal and parse a potentially malformed or truncated JSON response.

    Returns:
        (success: bool, parsed_object: Any, healed_string: str)
    """
    if not raw_text or not isinstance(raw_text, str):
        return False, None, ""

    stripped = raw_text.strip()

    # Step 1: Direct JSON parsing
    try:
        parsed = json.loads(stripped)
        return True, parsed, stripped
    except Exception:
        pass

    # Step 2: Strip markdown formatting
    extracted = extract_json_from_markdown(stripped)
    try:
        parsed = json.loads(extracted)
        return True, parsed, extracted
    except Exception:
        pass

    # Step 3: Remove trailing commas
    no_commas = remove_trailing_commas(extracted)
    try:
        parsed = json.loads(no_commas)
        return True, parsed, no_commas
    except Exception:
        pass

    # Step 4: Repair truncated braces/quotes
    repaired = repair_truncated_json(no_commas)
    try:
        parsed = json.loads(repaired)
        return True, parsed, repaired
    except Exception:
        pass

    # Step 5: Extract first JSON object {...} or array [...] from mixed conversational text
    obj_match = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", stripped)
    if obj_match:
        cand = obj_match.group(1)
        cand = remove_trailing_commas(cand)
        cand = repair_truncated_json(cand)
        try:
            parsed = json.loads(cand)
            return True, parsed, cand
        except Exception:
            pass

    return False, None, raw_text
