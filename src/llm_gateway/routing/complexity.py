import re
from enum import Enum
from typing import List, Dict, Any, Optional

class ComplexityTier(str, Enum):
    TIER_1_FAST = "fast"
    TIER_2_STANDARD = "standard"
    TIER_3_REASONING = "reasoning"

# Regex patterns indicating complex reasoning, mathematical proofs, or algorithmic code
REASONING_PATTERNS = [
    re.compile(r"\b(prove|proof|theorem|mathematical\s+rigor|formal\s+verification)\b", re.IGNORECASE),
    re.compile(r"\b(step\s*by\s*step\s+reasoning|chain\s+of\s+thought|think\s+deeply)\b", re.IGNORECASE),
    re.compile(r"```(python|rust|cpp|c|go|typescript|javascript|java|sql|scala|bash)", re.IGNORECASE),
    re.compile(r"\b(architect(ure)?|distributed\s+systems?|race\s+condition|deadlock)\b", re.IGNORECASE),
]

TIER_DEFAULTS = {
    ComplexityTier.TIER_1_FAST: "gpt-4o-mini",
    ComplexityTier.TIER_2_STANDARD: "gpt-4o",
    ComplexityTier.TIER_3_REASONING: "claude-3-5-sonnet",
}

def classify_complexity(messages: Optional[List[Dict[str, Any]]]) -> ComplexityTier:
    """Classify prompt into fast, standard, or reasoning tier based on heuristics."""
    if not isinstance(messages, list) or not messages:
        return ComplexityTier.TIER_1_FAST

    total_chars = 0
    full_text = []
    valid_msgs = 0

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        valid_msgs += 1
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
            full_text.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    t = part.get("text") or ""
                    if isinstance(t, str):
                        total_chars += len(t)
                        full_text.append(t)

    combined = "\n".join(full_text)

    # 1. Check reasoning / code triggers
    for pat in REASONING_PATTERNS:
        if pat.search(combined):
            return ComplexityTier.TIER_3_REASONING

    # 2. Token / context volume estimation (~4 chars/token)
    est_tokens = total_chars // 4
    if est_tokens > 2500 or valid_msgs >= 8:
        return ComplexityTier.TIER_3_REASONING
    elif est_tokens > 300 or valid_msgs >= 3:
        return ComplexityTier.TIER_2_STANDARD

    return ComplexityTier.TIER_1_FAST

def resolve_complexity_model(
    tier: ComplexityTier,
    custom_mappings: Optional[Dict[ComplexityTier, str]] = None
) -> str:
    mappings = {**TIER_DEFAULTS, **(custom_mappings or {})}
    return mappings.get(tier, TIER_DEFAULTS[ComplexityTier.TIER_2_STANDARD])
