"""Stealth Upstream Error Sanitization & Topology Masking.

Prevents leakage of internal backend topology (private IP addresses, internal
ports, secret base URLs, cluster names, and upstream API keys) when forwarding
or handling upstream provider failures.
"""

import re
from http import HTTPStatus
from typing import Optional

# Patterns matching private IP addresses and credentials
_PRIVATE_IP_REGEX = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3})\b"
)
_URL_HOST_REGEX = re.compile(r"https?://[a-zA-Z0-9.-]+(?::\d+)?")
_BEARER_TOKEN_REGEX = re.compile(r"(?:Bearer|sk-[a-zA-Z0-9_-]+)\s*[a-zA-Z0-9_.-]{10,}", re.IGNORECASE)


def sanitize_error_message(
    message: str,
    status_code: Optional[int] = None,
    backend_name: Optional[str] = None,
) -> str:
    """Sanitize raw error messages to protect backend infrastructure confidentiality."""
    if not message:
        if status_code:
            try:
                phrase = HTTPStatus(status_code).phrase
                return f"Upstream provider error ({status_code} {phrase})"
            except ValueError:
                return f"Upstream provider error ({status_code})"
        return "An upstream gateway error occurred"

    # Redact Bearer tokens / API keys
    cleaned = _BEARER_TOKEN_REGEX.sub("[REDACTED_CREDENTIAL]", message)

    # Redact private IP addresses
    cleaned = _PRIVATE_IP_REGEX.sub("[REDACTED_HOST]", cleaned)

    # Redact internal URLs with port numbers (e.g. http://10.10.110.42:13313 -> [UPSTREAM_HOST])
    cleaned = _URL_HOST_REGEX.sub("[UPSTREAM_SERVICE]", cleaned)

    # If status code is available, format standard canonical representation
    if status_code and status_code >= 500:
        try:
            phrase = HTTPStatus(status_code).phrase
            return f"Upstream service error ({status_code} {phrase})"
        except ValueError:
            return f"Upstream service error ({status_code})"

    return cleaned
