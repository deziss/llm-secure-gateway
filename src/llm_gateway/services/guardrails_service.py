"""Prompt injection detection and content guardrails.

Pattern-based detection for common prompt injection vectors.
Configurable sensitivity (low/medium/high) and action (warn/block).
"""

import logging
import re
import json
from typing import Optional, Any

logger = logging.getLogger(__name__)

# Injection patterns grouped by severity
INJECTION_PATTERNS: list[dict] = [
    # High severity — direct override attempts
    {"pattern": r"ignore\s+(all\s+)?previous\s+(instructions|prompts|rules)", "severity": "high", "category": "override"},
    {"pattern": r"disregard\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts)", "severity": "high", "category": "override"},
    {"pattern": r"forget\s+(all\s+)?(your|previous|prior)\s+(instructions|rules|guidelines)", "severity": "high", "category": "override"},
    {"pattern": r"you\s+are\s+now\s+(a|an|the)\s+\w+", "severity": "high", "category": "role_hijack"},
    {"pattern": r"new\s+instructions?\s*:", "severity": "high", "category": "override"},
    {"pattern": r"system\s*prompt\s*:", "severity": "high", "category": "extraction"},
    
    # Medium severity — indirect manipulation
    {"pattern": r"what\s+(is|are)\s+your\s+(system\s+)?instructions", "severity": "medium", "category": "extraction"},
    {"pattern": r"reveal\s+(your|the)\s+(system\s+)?prompt", "severity": "medium", "category": "extraction"},
    {"pattern": r"repeat\s+(the\s+)?(text|words|instructions)\s+above", "severity": "medium", "category": "extraction"},
    {"pattern": r"print\s+(your|the)\s+(initial|system|original)\s+(prompt|instructions)", "severity": "medium", "category": "extraction"},
    {"pattern": r"act\s+as\s+(if\s+)?(you\s+)?(are|were)\s+(a|an)\s+", "severity": "medium", "category": "role_hijack"},
    {"pattern": r"pretend\s+(you\s+)?(are|to\s+be)", "severity": "medium", "category": "role_hijack"},
    
    # Low severity — suspicious but may be legitimate
    {"pattern": r"jailbreak", "severity": "low", "category": "jailbreak"},
    {"pattern": r"DAN\s+mode", "severity": "low", "category": "jailbreak"},
    {"pattern": r"developer\s+mode\s+(enabled|on|activated)", "severity": "low", "category": "jailbreak"},
]

# Pre-compile patterns
_COMPILED_PATTERNS = [
    {**p, "regex": re.compile(p["pattern"], re.IGNORECASE)}
    for p in INJECTION_PATTERNS
]

SENSITIVITY_THRESHOLDS = {
    "low": {"high"},               # Only block high severity
    "medium": {"high", "medium"},  # Block high + medium
    "high": {"high", "medium", "low"},  # Block everything
}


def check_prompt_injection(
    messages: list[dict],
    sensitivity: str = "medium",
) -> tuple[bool, float, list[dict]]:
    """Check messages for prompt injection patterns.

    Returns (is_safe, confidence, matched_patterns).
    confidence: 0.0 (definitely injection) to 1.0 (definitely safe).
    """
    blocked_severities = SENSITIVITY_THRESHOLDS.get(sensitivity, SENSITIVITY_THRESHOLDS["medium"])
    matched = []

    for msg in messages:
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        for p in _COMPILED_PATTERNS:
            if p["regex"].search(content):
                matched.append({
                    "severity": p["severity"],
                    "category": p["category"],
                    "pattern": p["pattern"],
                    "role": msg.get("role", "unknown"),
                })

    if not matched:
        return True, 1.0, []

    # Determine if any matched pattern exceeds our threshold
    is_blocked = any(m["severity"] in blocked_severities for m in matched)
    highest = "low"
    for m in matched:
        if m["severity"] == "high":
            highest = "high"
            break
        if m["severity"] == "medium":
            highest = "medium"

    confidence_map = {"high": 0.1, "medium": 0.4, "low": 0.7}
    confidence = confidence_map.get(highest, 0.5)

    return not is_blocked, confidence, matched


# ---------------------------------------------------------------------------
# Multi-Phase Guardrail Architecture (Inspired by inference-gateway)
# ---------------------------------------------------------------------------
from enum import Enum


class GuardrailPhase(str, Enum):
    PRE_CALL = "pre_call"
    POST_CALL = "post_call"
    TOOL_ARGS = "tool_args"
    TOOL_OUTPUT = "tool_output"


class GuardrailAction(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    REDACT = "redact"
    WARN = "warn"


# Patterns for dangerous commands in tool arguments
DANGEROUS_TOOL_PATTERNS = [
    re.compile(r"\b(rm\s+-rf|chmod\s+777|chown|mkfs|dd\s+if=)\b", re.IGNORECASE),
    re.compile(r"\b(DROP\s+TABLE|DROP\s+DATABASE|TRUNCATE\s+TABLE|DELETE\s+FROM\s+\w+\s*;)\b", re.IGNORECASE),
    re.compile(r"\b(__import__|exec\(|eval\(|os\.system|subprocess\.Popen)\b"),
    re.compile(r"(;\s*(rm|cat\s+/etc/shadow|nc\s+-e|curl\s+.*\|\s*sh))", re.IGNORECASE),
]

# Patterns for sensitive leakages in post_call outputs
SENSITIVE_OUTPUT_PATTERNS = [
    re.compile(r"-----BEGIN\s+[A-Z0-9\s_-]+KEY-----", re.IGNORECASE),
    re.compile(r"\b(sk-[a-zA-Z0-9]{32,}|ghp_[a-zA-Z0-9]{36}|AKIA[0-9A-Z]{16})\b"),
]


class GuardrailDecision:
    def __init__(
        self,
        action: GuardrailAction = GuardrailAction.ALLOW,
        reason: Optional[str] = None,
        matches: Optional[list] = None,
    ):
        self.action = action
        self.reason = reason
        self.matches = matches or []

    @property
    def is_allowed(self) -> bool:
        return self.action in (GuardrailAction.ALLOW, GuardrailAction.WARN)


def evaluate_guardrail(
    phase: GuardrailPhase,
    payload: Any,
    sensitivity: str = "medium",
    fail_mode: str = "closed",
) -> GuardrailDecision:
    """Evaluate guardrail across specific phase."""
    try:
        if phase == GuardrailPhase.PRE_CALL:
            messages = payload if isinstance(payload, list) else []
            is_safe, conf, matches = check_prompt_injection(messages, sensitivity=sensitivity)
            if not is_safe:
                return GuardrailDecision(
                    action=GuardrailAction.BLOCK,
                    reason="Prompt injection detected in input messages",
                    matches=matches,
                )
            return GuardrailDecision(action=GuardrailAction.ALLOW)

        elif phase == GuardrailPhase.TOOL_ARGS:
            # Payload is arguments dict
            args_str = json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)
            matched = []
            for pat in DANGEROUS_TOOL_PATTERNS:
                if pat.search(args_str):
                    matched.append(pat.pattern)
            if matched:
                return GuardrailDecision(
                    action=GuardrailAction.BLOCK,
                    reason=f"Dangerous command pattern in tool arguments: {matched}",
                    matches=matched,
                )
            return GuardrailDecision(action=GuardrailAction.ALLOW)

        elif phase == GuardrailPhase.POST_CALL or phase == GuardrailPhase.TOOL_OUTPUT:
            text = payload if isinstance(payload, str) else json.dumps(payload)
            matched = []
            for pat in SENSITIVE_OUTPUT_PATTERNS:
                if pat.search(text):
                    matched.append(pat.pattern)
            if matched:
                return GuardrailDecision(
                    action=GuardrailAction.BLOCK,
                    reason=f"Sensitive secret pattern detected in response output",
                    matches=matched,
                )
            return GuardrailDecision(action=GuardrailAction.ALLOW)

        return GuardrailDecision(action=GuardrailAction.ALLOW)

    except Exception as exc:
        logger.warning("Guardrail evaluation error (%s): %s", phase, exc)
        if fail_mode == "open":
            return GuardrailDecision(action=GuardrailAction.WARN, reason=str(exc))
        return GuardrailDecision(action=GuardrailAction.BLOCK, reason=f"Guardrail check failed: {str(exc)}")
