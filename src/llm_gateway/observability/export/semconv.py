"""One call that writes the attributes every OTLP backend in the preset list understands.

Writes OpenInference (``llm.*``, ``input.value``, ``output.value``) and OpenTelemetry GenAI
(``gen_ai.*``) together, plus ``user.id``/``session.id`` (Langfuse, Phoenix) and the gateway's
own ``server.name``/``server.type``. Attribute values are skipped when None or empty, so
callers can pass what they know at each point of the request.
"""
from __future__ import annotations

from typing import Any, Optional


def _set(span, key: str, value: Any) -> None:
    if value is None or value == "":
        return
    span.set_attribute(key, value)


def set_llm_attributes(
    span,
    *,
    model: Optional[str] = None,
    response_model: Optional[str] = None,
    provider: Optional[str] = None,
    operation: Optional[str] = None,
    server_name: Optional[str] = None,
    server_type: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    input_value: Optional[str] = None,
    output_value: Optional[str] = None,
) -> None:
    """Write LLM semantic attributes onto ``span``. No-op when the span is not recording."""
    try:
        if span is None or not span.is_recording():
            return
    except Exception:
        return

    _set(span, "openinference.span.kind", "LLM")
    # OpenInference
    _set(span, "llm.model_name", model)
    _set(span, "llm.provider", provider)
    if input_tokens is not None:
        _set(span, "llm.token_count.prompt", int(input_tokens))
    if output_tokens is not None:
        _set(span, "llm.token_count.completion", int(output_tokens))
    if total_tokens is not None:
        _set(span, "llm.token_count.total", int(total_tokens))
    _set(span, "input.value", input_value[:10000] if isinstance(input_value, str) else input_value)
    _set(span, "output.value", output_value[:10000] if isinstance(output_value, str) else output_value)

    # OpenTelemetry GenAI
    _set(span, "gen_ai.system", provider)
    _set(span, "gen_ai.operation.name", operation)
    _set(span, "gen_ai.request.model", model)
    _set(span, "gen_ai.response.model", response_model or model)
    if input_tokens is not None:
        _set(span, "gen_ai.usage.input_tokens", int(input_tokens))
    if output_tokens is not None:
        _set(span, "gen_ai.usage.output_tokens", int(output_tokens))

    # Session / user and gateway routing
    _set(span, "user.id", user_id)
    _set(span, "session.id", session_id)
    _set(span, "server.name", server_name)
    _set(span, "server.type", server_type)
