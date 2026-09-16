"""Developer and Coding Agent Detection.

Identifies caller tools (Claude Code, Cursor, Cline, Aider, Copilot, Continue)
from request headers to enable granular developer experience telemetry.
"""

import re
from typing import Optional, Tuple

_AGENT_PATTERNS = [
    ("claude-code", re.compile(r"(?:claude-code|@anthropic-ai/claude-code)(?:/v?([0-9.]+))?", re.I)),
    ("cursor", re.compile(r"cursor(?:/v?([0-9.]+))?", re.I)),
    ("cline", re.compile(r"(?:roo-)?cline(?:/v?([0-9.]+))?", re.I)),
    ("aider", re.compile(r"aider(?:/v?([0-9.]+))?", re.I)),
    ("copilot", re.compile(r"(?:github-?copilot|copilot)(?:/v?([0-9.]+))?", re.I)),
    ("continue", re.compile(r"continue(?:/v?([0-9.]+))?", re.I)),
    ("opencode", re.compile(r"(?:opencode|open-code)(?:/v?([0-9.]+))?", re.I)),
    ("langchain", re.compile(r"langchain(?:/v?([0-9.]+))?", re.I)),
    ("llamaindex", re.compile(r"llama-?index(?:/v?([0-9.]+))?", re.I)),
]


def detect_coding_agent(user_agent: Optional[str] = None, client_header: Optional[str] = None) -> Tuple[str, str]:
    """Detect client agent name and version from headers.

    Returns:
        (agent_name: str, agent_version: str)
    """
    candidates = [client_header, user_agent]
    for text in candidates:
        if not text:
            continue
        for name, pattern in _AGENT_PATTERNS:
            match = pattern.search(text)
            if match:
                version = match.group(1) if match.lastindex and match.group(1) else "unknown"
                return name, version

    return "generic", "unknown"
