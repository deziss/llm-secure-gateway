import pytest
from fastapi import HTTPException
from llm_gateway.services.backpressure_service import (
    backpressure_guard,
    get_inflight_count,
    set_max_inflight,
    is_inference_path,
)
from llm_gateway.services.error_sanitizer import sanitize_error_message
from llm_gateway.services.json_healer import (
    heal_json,
    extract_json_from_markdown,
    remove_trailing_commas,
    repair_truncated_json,
)
from llm_gateway.services.agent_detector import detect_coding_agent
from llm_gateway.services.image_inliner import is_ssrf_safe
from llm_gateway.services.failed_key_tracker import (
    record_failed_key,
    is_key_healthy,
    select_healthy_key,
)


@pytest.mark.asyncio
async def test_backpressure_guard():
    assert is_inference_path("/v1/chat/completions") is True
    assert is_inference_path("/v1/embeddings") is True
    assert is_inference_path("/health") is False
    assert is_inference_path("/admin/dashboard") is False

    set_max_inflight(2)

    async with backpressure_guard():
        assert get_inflight_count() == 1
        async with backpressure_guard():
            assert get_inflight_count() == 2
            # 3rd request exceeds limit of 2 -> raises 529
            with pytest.raises(HTTPException) as exc_info:
                async with backpressure_guard():
                    pass
            assert exc_info.value.status_code == 529
            assert exc_info.value.headers.get("Retry-After") == "1"

    assert get_inflight_count() == 0
    set_max_inflight(500)  # reset


def test_error_sanitizer():
    # Mask private IP and internal port
    raw = "ConnectError to http://10.10.110.42:13313/v1/chat/completions: connection refused"
    sanitized = sanitize_error_message(raw, status_code=502)
    assert "10.10.110.42" not in sanitized
    assert "13313" not in sanitized
    assert "502" in sanitized

    # Mask Bearer token
    raw_token = "Failed with token Bearer sk-live-secretkey1234567890"
    sanitized_token = sanitize_error_message(raw_token)
    assert "sk-live-secretkey1234567890" not in sanitized_token
    assert "[REDACTED_CREDENTIAL]" in sanitized_token


def test_json_healer_markdown():
    wrapped = "```json\n{\"name\": \"Alice\", \"role\": \"admin\"}\n```"
    extracted = extract_json_from_markdown(wrapped)
    assert extracted == '{"name": "Alice", "role": "admin"}'

    ok, parsed, _ = heal_json(wrapped)
    assert ok is True
    assert parsed == {"name": "Alice", "role": "admin"}


def test_json_healer_trailing_commas():
    bad_commas = '{"items": [1, 2, 3, ], "status": "ok", }'
    ok, parsed, _ = heal_json(bad_commas)
    assert ok is True
    assert parsed["status"] == "ok"
    assert parsed["items"] == [1, 2, 3]


def test_json_healer_truncated():
    # Truncated midway through output
    truncated = '{"user": "John Doe", "permissions": ["read", "write"'
    ok, parsed, _ = heal_json(truncated)
    assert ok is True
    assert parsed["user"] == "John Doe"
    assert "read" in parsed["permissions"]


def test_json_healer_mixed_conversation():
    mixed = "Sure! Here is the response you requested: {\"result\": 42, \"valid\": true} Hope this helps!"
    ok, parsed, _ = heal_json(mixed)
    assert ok is True
    assert parsed["result"] == 42
    assert parsed["valid"] is True


def test_agent_detector():
    # Claude Code
    name, ver = detect_coding_agent("claude-code/1.0.5")
    assert name == "claude-code"
    assert ver == "1.0.5"

    # Cursor
    name, _ = detect_coding_agent("Mozilla/5.0 (Windows) Cursor/0.45.1")
    assert name == "cursor"

    # Cline
    name, _ = detect_coding_agent("roo-cline/3.2.0")
    assert name == "cline"

    # Aider
    name, _ = detect_coding_agent("aider/0.72.0")
    assert name == "aider"

    # Copilot
    name, _ = detect_coding_agent("GitHubCopilot/1.2.3")
    assert name == "copilot"

    # Generic fallback
    name, ver = detect_coding_agent("Python-urllib/3.12")
    assert name == "generic"


def test_image_inliner_ssrf():
    assert is_ssrf_safe("https://example.com/images/cat.jpg") is True
    assert is_ssrf_safe("http://127.0.0.1:8080/secret") is False
    assert is_ssrf_safe("http://localhost:8000/test") is False
    assert is_ssrf_safe("http://169.254.169.254/latest/meta-data") is False
    assert is_ssrf_safe("ftp://example.com/image.png") is False


def test_failed_key_tracker():
    k1 = "key_alpha"
    k2 = "key_beta"

    assert is_key_healthy(k1) is True
    record_failed_key(k1, cooldown_seconds=10.0)
    assert is_key_healthy(k1) is False
    assert is_key_healthy(k2) is True

    selected = select_healthy_key([k1, k2])
    assert selected == k2
