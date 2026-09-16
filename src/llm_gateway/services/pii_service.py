"""PII detection and redaction service.

Regex-based scanner for sensitive data patterns: credit cards, SSNs,
email addresses, API keys. Configurable via SystemSetting PII_PATTERNS.
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Built-in PII patterns
DEFAULT_PATTERNS: dict[str, dict] = {
    "credit_card_visa": {
        "pattern": r"\b4[0-9]{12}(?:[0-9]{3})?\b",
        "label": "CREDIT_CARD",
        "description": "Visa card number",
    },
    "credit_card_mc": {
        "pattern": r"\b(?:5[1-5][0-9]{2}|222[1-9]|22[3-9][0-9]|2[3-6][0-9]{2}|27[01][0-9]|2720)[0-9]{12}\b",
        "label": "CREDIT_CARD",
        "description": "Mastercard number",
    },
    "credit_card_amex": {
        "pattern": r"\b3[47][0-9]{13}\b",
        "label": "CREDIT_CARD",
        "description": "American Express card number",
    },
    "ssn": {
        "pattern": r"\b\d{3}-\d{2}-\d{4}\b",
        "label": "SSN",
        "description": "US Social Security Number",
    },
    "email": {
        "pattern": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        "label": "EMAIL",
        "description": "Email address",
    },
    "aws_key": {
        "pattern": r"\b(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b",
        "label": "API_KEY",
        "description": "AWS Access Key ID",
    },
    "aws_secret": {
        "pattern": r"\b[A-Za-z0-9/+=]{40}\b",
        "label": "API_SECRET",
        "description": "Potential AWS Secret Key (40-char base64)",
    },
    "generic_api_key": {
        "pattern": r"\b(?:sk-|pk_live_|pk_test_|rk_live_|rk_test_|whsec_)[A-Za-z0-9_-]{20,}\b",
        "label": "API_KEY",
        "description": "API key (OpenAI sk-, Stripe pk_/rk_/whsec_)",
    },
    "phone_us": {
        "pattern": r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        "label": "PHONE",
        "description": "US phone number",
    },
}


class PIIScanner:
    def __init__(self, custom_patterns: Optional[dict] = None):
        self._patterns = dict(DEFAULT_PATTERNS)
        if custom_patterns:
            self._patterns.update(custom_patterns)
        # Pre-compile regexes
        self._compiled = {
            name: re.compile(info["pattern"])
            for name, info in self._patterns.items()
        }

    def scan(self, text: str) -> list[dict]:
        """Scan text for PII patterns. Returns list of findings."""
        findings = []
        for name, regex in self._compiled.items():
            for match in regex.finditer(text):
                findings.append({
                    "type": self._patterns[name]["label"],
                    "pattern_name": name,
                    "matched": match.group(),
                    "start": match.start(),
                    "end": match.end(),
                })
        return findings

    def redact(self, text: str) -> tuple[str, list[dict]]:
        """Scan and redact PII from text. Returns (redacted_text, findings)."""
        findings = self.scan(text)
        if not findings:
            return text, []

        # Sort by position descending to replace from end to start
        findings.sort(key=lambda f: f["start"], reverse=True)
        redacted = text
        for f in findings:
            label = f["type"]
            redacted = redacted[:f["start"]] + f"[{label}_REDACTED]" + redacted[f["end"]:]

        return redacted, findings

    def scan_messages(self, messages: list[dict]) -> tuple[list[dict], list[dict]]:
        """Scan and redact PII from a list of chat messages."""
        all_findings = []
        redacted_messages = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                redacted_content, findings = self.redact(content)
                new_msg = dict(msg)
                new_msg["content"] = redacted_content
                redacted_messages.append(new_msg)
                all_findings.extend(findings)
            else:
                redacted_messages.append(msg)
        return redacted_messages, all_findings


# Module-level singleton
_scanner: Optional[PIIScanner] = None


def get_pii_scanner(custom_patterns: Optional[dict] = None) -> PIIScanner:
    global _scanner
    if _scanner is None:
        _scanner = PIIScanner(custom_patterns)
    return _scanner
