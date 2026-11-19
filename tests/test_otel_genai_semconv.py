import pytest
from unittest.mock import MagicMock
from llm_gateway.observability.export.semconv import set_llm_attributes


def test_semconv_v130_attributes():
    span = MagicMock()
    span.is_recording.return_value = True

    set_llm_attributes(
        span,
        model="claude-3-5-sonnet",
        provider="anthropic",
        operation="chat",
        time_to_first_token=0.245,
        request_duration=1.82,
        cache_read_input_tokens=1500,
        finish_reasons=["stop"],
        error_type=None,
    )

    span.set_attribute.assert_any_call("gen_ai.system", "anthropic")
    span.set_attribute.assert_any_call("gen_ai.request.model", "claude-3-5-sonnet")
    span.set_attribute.assert_any_call("gen_ai.server.time_to_first_token", 0.245)
    span.set_attribute.assert_any_call("gen_ai.server.request.duration", 1.82)
    span.set_attribute.assert_any_call("gen_ai.usage.cache_read_input_tokens", 1500)
    span.set_attribute.assert_any_call("gen_ai.response.finish_reasons", ["stop"])


def test_semconv_error_type():
    span = MagicMock()
    span.is_recording.return_value = True

    set_llm_attributes(
        span,
        model="gpt-4o",
        error_type="upstream_rate_limit",
    )
    span.set_attribute.assert_any_call("error.type", "upstream_rate_limit")
