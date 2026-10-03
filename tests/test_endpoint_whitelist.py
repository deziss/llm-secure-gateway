"""
allowed_endpoints matching (proxy_helpers._canonical_endpoint/_endpoint_matches).

Backends store allowed_endpoints both relative to a /v1 base URL
("/chat/completions") and with the prefix ("/v1/chat/completions"); requests
arrive as "v1/chat/completions". A literal prefix match rejected every request
to backends stored the first way with HTTP 403 "Endpoint ... is not allowed".
"""
import pytest

from llm_gateway.proxy_helpers import _canonical_endpoint as canon, _endpoint_matches as match


@pytest.mark.parametrize("entry", ["/chat/completions", "/v1/chat/completions", "chat/completions", "v1/chat/completions"])
def test_both_storage_styles_allow_the_request(entry):
    assert match(canon("v1/chat/completions"), canon(entry))
    assert match(canon("/v1/chat/completions"), canon(entry))


def test_unlisted_endpoint_is_still_denied():
    allowed = [canon(e) for e in ["/chat/completions", "/models"]]
    assert not any(match(canon("v1/embeddings"), e) for e in allowed)


def test_prefix_must_end_on_a_segment_boundary():
    # "/completions" must not allow "/completions-evil" or "/completionsfoo"
    assert match(canon("v1/completions"), canon("/completions"))
    assert not match(canon("v1/completionsfoo"), canon("/completions"))
    # nested paths under an allowed prefix are fine
    assert match(canon("v1/models/llama-3"), canon("/v1/models"))


def test_ollama_style_paths_unaffected():
    assert match(canon("api/chat"), canon("/api/chat"))
    assert not match(canon("api/chat"), canon("/chat/completions"))
