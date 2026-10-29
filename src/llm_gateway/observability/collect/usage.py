"""Token usage extraction for every upstream format the gateway proxies.

Supported shapes:
  * OpenAI JSON body and SSE chunks: ``usage.prompt_tokens`` / ``completion_tokens``
  * Anthropic: ``usage.input_tokens`` / ``output_tokens`` (``message_start`` carries the
    input count, ``message_delta`` the cumulative output count)
  * Ollama: NDJSON lines (no ``data:`` prefix) and non-stream bodies with
    ``prompt_eval_count`` / ``eval_count`` / ``eval_duration`` (nanoseconds)
  * Fallback: when no usage arrives at all, the caller estimates completion tokens from
    the number of content deltas and marks the result ``estimated=True``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

_TRUTHY = {"1", "true", "yes", "on", "enabled"}

#: Backends whose OpenAI-compatible streams omit usage unless asked.
_FORCE_USAGE_TYPES = {"vllm", "llamacpp"}


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    #: Ollama's own generation time in seconds (eval_duration / 1e9), when reported.
    eval_duration_s: Optional[float] = None
    estimated: bool = False

    def has_counts(self) -> bool:
        return bool(self.prompt_tokens or self.completion_tokens or self.total_tokens)

    def merge(self, other: "Usage") -> "Usage":
        """Later non-zero values win. Used across SSE events (Anthropic output is cumulative)."""
        if other is None:
            return self
        if other.prompt_tokens:
            self.prompt_tokens = other.prompt_tokens
        if other.completion_tokens:
            self.completion_tokens = other.completion_tokens
        if other.total_tokens:
            self.total_tokens = other.total_tokens
        if other.eval_duration_s:
            self.eval_duration_s = other.eval_duration_s
        return self

    def finalize_total(self) -> "Usage":
        if not self.total_tokens:
            self.total_tokens = self.prompt_tokens + self.completion_tokens
        return self


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def usage_from_event(obj: Any) -> Optional[Usage]:
    """Extract usage from one parsed JSON object (SSE data, NDJSON line, or full body).

    Returns None when the object carries no usage information at all.
    """
    if not isinstance(obj, dict):
        return None
    found = False
    u = Usage()

    usage = obj.get("usage")
    if isinstance(usage, dict):
        found = True
        u.prompt_tokens = _int(usage.get("prompt_tokens") or usage.get("input_tokens"))
        u.completion_tokens = _int(usage.get("completion_tokens") or usage.get("output_tokens"))
        u.total_tokens = _int(usage.get("total_tokens"))

    # Anthropic message_start: usage nested under "message"
    msg = obj.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
        mu = msg["usage"]
        found = True
        if not u.prompt_tokens:
            u.prompt_tokens = _int(mu.get("input_tokens") or mu.get("prompt_tokens"))
        if not u.completion_tokens:
            u.completion_tokens = _int(mu.get("output_tokens") or mu.get("completion_tokens"))

    # Ollama: counts and durations sit at the top level of the final object.
    if "prompt_eval_count" in obj or "eval_count" in obj or "eval_duration" in obj:
        found = True
        if "prompt_eval_count" in obj:
            u.prompt_tokens = _int(obj.get("prompt_eval_count"))
        if "eval_count" in obj:
            u.completion_tokens = _int(obj.get("eval_count"))
        ed = obj.get("eval_duration")
        if isinstance(ed, (int, float)) and ed > 0:
            u.eval_duration_s = float(ed) / 1e9

    if not found:
        return None
    return u.finalize_total()


def usage_from_body(raw: bytes | str) -> Optional[Usage]:
    """Parse a complete non-streaming body, or NDJSON that arrived as one buffer."""
    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else raw
    if not text.strip():
        return None
    try:
        return usage_from_event(json.loads(text))
    except (json.JSONDecodeError, ValueError):
        pass
    merged: Optional[Usage] = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            u = usage_from_event(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
        if u is not None:
            merged = u if merged is None else merged.merge(u)
    return merged


def parse_sse_data_line(line: str) -> Optional[Any]:
    """Return the JSON payload of an SSE ``data:`` line, an NDJSON object line, or None."""
    s = line.strip()
    if not s:
        return None
    if s.startswith("data:"):
        payload = s[5:].strip()
        if not payload or payload == "[DONE]":
            return None
    elif s.startswith("{"):
        payload = s
    else:
        return None
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return None


def estimate_completion(delta_count: int) -> Usage:
    """Fallback when the upstream reported no usage: one content delta is roughly one token."""
    return Usage(completion_tokens=max(0, delta_count), total_tokens=max(0, delta_count), estimated=True)


def force_stream_usage_enabled() -> bool:
    return os.getenv("OBS_FORCE_STREAM_USAGE", "false").strip().lower() in _TRUTHY


def inject_stream_usage(body: Any, backend_type: str) -> Any:
    """Add ``stream_options.include_usage`` for vLLM/llama.cpp streaming requests.

    Off unless ``OBS_FORCE_STREAM_USAGE=true``. It adds one ``choices: []`` chunk the client
    did not ask for, which is why it is opt-in. Returns a new dict; the caller's body is not
    mutated.
    """
    if not force_stream_usage_enabled():
        return body
    if not isinstance(body, dict) or not body.get("stream"):
        return body
    if str(backend_type or "").lower() not in _FORCE_USAGE_TYPES:
        return body
    opts = body.get("stream_options")
    if isinstance(opts, dict) and opts.get("include_usage") is True:
        return body
    new = dict(body)
    new["stream_options"] = {**(opts if isinstance(opts, dict) else {}), "include_usage": True}
    return new
