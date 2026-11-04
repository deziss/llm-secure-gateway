"""Tests for client setup snippets service and API endpoint."""
import json
import pytest
from httpx import AsyncClient, ASGITransport
from types import SimpleNamespace

from llm_gateway.services.snippet_service import generate_client_snippets
from llm_gateway.auth.models import Role, User
from llm_gateway.auth.users import current_active_user
from llm_gateway.main import app


def test_generate_client_snippets_structure_and_substitution():
    """Verify generate_client_snippets returns all required snippets with substituted values."""
    base_url = "https://gateway.corp.net:8443/"
    api_key = "sec-prod-key-xyz987"
    model = "claude-3-7-sonnet"

    snippets = generate_client_snippets(base_url, api_key, model)

    expected_keys = {
        "claude_code",
        "cursor",
        "continue_dev",
        "python_openai",
        "python_anthropic",
        "curl_openai",
        "curl_anthropic",
    }
    assert set(snippets.keys()) == expected_keys

    expected_base = "https://gateway.corp.net:8443"

    # Claude Code CLI
    claude = snippets["claude_code"]
    assert f'export ANTHROPIC_BASE_URL="{expected_base}"' in claude
    assert f'export ANTHROPIC_API_KEY="{api_key}"' in claude
    assert not expected_base.endswith("/")

    # Cursor
    cursor = snippets["cursor"]
    assert f"Base URL: {expected_base}/v1" in cursor
    assert f"Model: {model}" in cursor
    assert f"API Key: {api_key}" in cursor

    # Continue.dev
    continue_dev = snippets["continue_dev"]
    parsed_continue = json.loads(continue_dev)
    assert "models" in parsed_continue
    assert len(parsed_continue["models"]) >= 1
    model_entry = parsed_continue["models"][0]
    assert model_entry["model"] == model
    assert model_entry["apiBase"] == f"{expected_base}/v1"
    assert model_entry["apiKey"] == api_key
    assert model_entry["provider"] == "openai"

    # Python OpenAI SDK
    py_openai = snippets["python_openai"]
    assert "from openai import OpenAI" in py_openai
    assert f'base_url="{expected_base}/v1"' in py_openai
    assert f'api_key="{api_key}"' in py_openai
    assert f'model="{model}"' in py_openai

    # Python Anthropic SDK
    py_anthropic = snippets["python_anthropic"]
    assert "import anthropic" in py_anthropic
    assert f'base_url="{expected_base}"' in py_anthropic
    assert f'api_key="{api_key}"' in py_anthropic
    assert f'model="{model}"' in py_anthropic

    # cURL OpenAI
    curl_oai = snippets["curl_openai"]
    assert f'curl -X POST "{expected_base}/v1/chat/completions"' in curl_oai
    assert f"Authorization: Bearer {api_key}" in curl_oai
    assert f'"model": "{model}"' in curl_oai

    # cURL Anthropic
    curl_ant = snippets["curl_anthropic"]
    assert f'curl -X POST "{expected_base}/v1/messages"' in curl_ant
    assert f"x-api-key: {api_key}" in curl_ant
    assert f'"model": "{model}"' in curl_ant


def test_generate_client_snippets_defaults():
    """Verify fallback defaults when parameters are empty or None."""
    snippets = generate_client_snippets("", "", "")

    assert "http://localhost:8000" in snippets["claude_code"]
    assert "YOUR_API_KEY" in snippets["claude_code"]
    assert "default" in snippets["cursor"]
    assert "YOUR_API_KEY" in snippets["cursor"]

    parsed = json.loads(snippets["continue_dev"])
    assert parsed["models"][0]["apiKey"] == "YOUR_API_KEY"
    assert parsed["models"][0]["model"] == "default"


@pytest.mark.asyncio
async def test_get_client_snippets_unauthenticated():
    """Verify unauthenticated access to /api/client-snippets is rejected."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/client-snippets")
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_get_client_snippets_authenticated(client):
    """Verify authenticated request to /api/client-snippets returns populated snippets."""
    res = await client.get("/api/client-snippets?key=sec-my-secret-key&model=gpt-4o")
    assert res.status_code == 200
    data = res.json()

    assert "claude_code" in data
    assert "cursor" in data
    assert "continue_dev" in data
    assert "python_openai" in data
    assert "python_anthropic" in data
    assert "curl_openai" in data
    assert "curl_anthropic" in data

    # Verify key and model were passed through and base_url is resolved
    assert "sec-my-secret-key" in data["claude_code"]
    assert "gpt-4o" in data["cursor"]
    assert "sec-my-secret-key" in data["python_openai"]
    assert "http://test/v1" in data["python_openai"]
    assert "http://test" in data["python_anthropic"]


@pytest.mark.asyncio
async def test_get_client_snippets_admin_prefix(client):
    """Verify /admin/api/client-snippets also works for consistency."""
    res = await client.get("/admin/api/client-snippets?key=sec-admin-test&model=qwen-2.5")
    assert res.status_code == 200
    data = res.json()
    assert "sec-admin-test" in data["claude_code"]
    assert "qwen-2.5" in data["cursor"]


@pytest.mark.asyncio
async def test_get_client_snippets_x_forwarded_headers(client):
    """Verify reverse-proxy headers (X-Forwarded-Proto, X-Forwarded-Host) are honored."""
    headers = {
        "x-forwarded-proto": "https",
        "x-forwarded-host": "gateway.acme.corp",
    }
    res = await client.get("/api/client-snippets?key=sec-proxy-key", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert 'export ANTHROPIC_BASE_URL="https://gateway.acme.corp"' in data["claude_code"]
    assert 'base_url="https://gateway.acme.corp/v1"' in data["python_openai"]
