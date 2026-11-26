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


# Map PII labels to broad categories so callers can redact subsets, e.g.
# {"identity", "credentials"} or {"financial"} only.
_CATEGORY_BY_LABEL = {
    "CREDIT_CARD": "financial",
    "SSN": "identity",
    "EMAIL": "contact",
    "PHONE": "contact",
    "API_KEY": "credentials",
    "API_SECRET": "credentials",
}

# Attach a category to every built-in pattern from its label so callers can
# redact e.g. only {"identity", "credentials"} or {"financial"} sets.
for _info in DEFAULT_PATTERNS.values():
    _info.setdefault("category", _CATEGORY_BY_LABEL.get(_info["label"], "generic"))


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
        """Scan text for PII patterns. Returns list of findings.

        All matches are kept (even overlapping ones, e.g. an email that also
        contains a phone-like digit run); redact() handles overlaps.
        """
        findings = []
        for name, regex in self._compiled.items():
            for match in regex.finditer(text):
                findings.append({
                    "type": self._patterns[name]["label"],
                    "pattern_name": name,
                    "category": self._patterns[name].get("category", "generic"),
                    "matched": match.group(),
                    "start": match.start(),
                    "end": match.end(),
                })
        return findings

    def redact(self, text: str, categories: Optional[set] = None,
               keep_original: bool = False) -> tuple[str, list[dict]]:
        """Scan and redact PII from text. Returns (redacted_text, findings).

        ``categories`` optionally limits which pattern categories are redacted
        (e.g. {"identity", "credentials"}). Overlapping matches are merged so
        replacement positions stay consistent. When ``keep_original`` is true
        each finding carries the untouched text (used by response masking,
        which rewrites the JSON body from original values).
        """
        findings = self.scan(text)
        if categories is not None:
            findings = [f for f in findings if f.get("category") in categories]
        if not findings:
            return text, []
        if keep_original:
            for f in findings:
                f["original"] = f["matched"]

        # Merge overlapping/enclosed spans, keeping per-label replacement text
        ordered = sorted(findings, key=lambda f: (f["start"], -f["end"]))
        merged: list[dict] = []
        for f in ordered:
            if merged and f["start"] < merged[-1]["end"]:
                contig = f["end"] == merged[-1]["end"] or f["category"] == merged[-1]["category"]
                if not contig:
                    merged.append(dict(f))  # disjoint label overlap: emit both for counts
                    continue
                if f["end"] > merged[-1]["end"]:
                    merged[-1]["end"] = f["end"]
            else:
                merged.append(dict(f))

        redacted = text
        for f in sorted(merged, key=lambda f: f["start"], reverse=True):
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


class PIIScrubber:
    """Stateful incremental scrubber for streamed text.

    Stream chunks split PII at arbitrary points, so the last ``carry_len``
    characters of each fed fragment are held back and re-scanned with the next
    fragment. ``feed`` returns the text safe to emit now; ``flush`` releases
    the tail. ``record`` collects original -> masked replacements (and counts)
    so callers can audit what was masked.
    """

    def __init__(self, scanner: "PIIScanner", categories: Optional[set] = None, carry_len: int = 128):
        self._scanner = scanner
        self._categories = categories
        self._carry_len = max(carry_len, 32)
        self._pending = ""
        self.total_masked = 0
        self.categories_seen: set[str] = set()
        self._replacements: dict[str, str] = {}

    @property
    def replacements(self) -> dict[str, str]:
        """original -> masked, for rewriting already-captured trace fragments."""
        return self._replacements

    def feed(self, text: str) -> str:
        """Scan buffered text; return the portion safe to emit now."""
        if not text:
            return ""
        buf = self._pending + text
        if len(buf) <= self._carry_len:
            self._pending = buf
            return ""
        scan_len = len(buf) - self._carry_len
        redacted, findings = self._scanner.redact(buf[:scan_len], categories=self._categories,
                                                  keep_original=True)
        # A match ending near the cut boundary may be a prefix of a longer
        # match completed by the next chunk; rewind the emit point to its
        # start and re-scan it together with the held tail next time.
        hold: Optional[int] = None
        for f in findings:
            if f["end"] > scan_len - self._carry_len and (hold is None or f["start"] < hold):
                hold = f["start"]
        if hold is not None:
            emit, emit_findings = self._scanner.redact(buf[:hold], categories=self._categories,
                                                       keep_original=True)
            self._pending = buf[hold:]
            self._record(emit_findings)
        else:
            emit = redacted
            self._pending = buf[scan_len:]
            self._record(findings)
        return emit

    def flush(self) -> str:
        tail = self._pending
        self._pending = ""
        if not tail:
            return ""
        redacted, findings = self._scanner.redact(tail, categories=self._categories,
                                                  keep_original=True)
        self._record(findings)
        return redacted

    def scrub_full(self, text: str) -> str:
        """One-shot redaction (no carry) that still records counts."""
        if not text:
            return text
        redacted, findings = self._scanner.redact(text, categories=self._categories,
                                                  keep_original=True)
        self._record(findings)
        return redacted

    def _record(self, findings: list[dict]) -> None:
        for f in findings:
            orig = f.get("original") or f.get("matched") or ""
            if orig:
                self._replacements[orig] = f"[{f['type']}_REDACTED]"
            self.categories_seen.add(f.get("category", "generic"))
        self.total_masked += len(findings)


# Module-level singleton
_scanner: Optional[PIIScanner] = None


def get_pii_scanner(custom_patterns: Optional[dict] = None) -> PIIScanner:
    global _scanner
    if _scanner is None:
        _scanner = PIIScanner(custom_patterns)
    return _scanner


async def get_pii_scanner_async(session=None) -> PIIScanner:
    """Scanner optionally extended with custom patterns from the PII_PATTERNS
    SystemSetting (JSON: {"name": {"pattern": ..., "label": ..., "category": ...}},
    "enabled": false to disable one). Falls back to defaults on bad config."""
    if session is None:
        return get_pii_scanner()
    try:
        from . import config_service
        raw = await config_service.get_setting(session, "PII_PATTERNS", "")
    except Exception:
        raw = ""
    if not raw:
        return get_pii_scanner()
    try:
        custom = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(custom, dict):
            raise ValueError("PII_PATTERNS must be a JSON object")
        for name, info in custom.items():
            if not isinstance(info, dict) or not info.get("pattern"):
                raise ValueError(f"PII_PATTERNS.{name} requires a 'pattern'")
            re.compile(info["pattern"])
        return PIIScanner(custom)
    except Exception as exc:
        logger.warning("PII_PATTERNS setting invalid, using defaults: %s", exc)
        return get_pii_scanner()
