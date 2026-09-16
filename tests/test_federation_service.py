"""
Unit tests for the federation service module.
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import httpx


class TestFetchModelsFromBackend:
    async def test_ollama_backend_returns_models(self):
        from llm_gateway.services.federation_service import fetch_models_from_backend
        from llm_gateway.models import BackendType

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "models": [
                {"name": "llama3.2:latest"},
                {"name": "mistral:latest"},
            ]
        }

        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_response)

        result = await fetch_models_from_backend(
            client, "http://ollama:11434", BackendType.OLLAMA
        )
        assert result == ["llama3.2:latest", "mistral:latest"]
        client.get.assert_awaited_once()
        assert "/api/tags" in client.get.call_args[0][0]

    async def test_openai_backend_returns_models(self):
        from llm_gateway.services.federation_service import fetch_models_from_backend
        from llm_gateway.models import BackendType

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "gpt-4"},
                {"id": "gpt-3.5-turbo"},
            ]
        }

        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_response)

        result = await fetch_models_from_backend(
            client, "https://api.openai.com", BackendType.OPENAI, api_key="sk-test"
        )
        assert result == ["gpt-4", "gpt-3.5-turbo"]
        assert "/v1/models" in client.get.call_args[0][0]

    async def test_vllm_backend_uses_openai_format(self):
        from llm_gateway.services.federation_service import fetch_models_from_backend
        from llm_gateway.models import BackendType

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [{"id": "mistral-7b"}]
        }

        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_response)

        result = await fetch_models_from_backend(
            client, "http://vllm:8000", BackendType.VLLM
        )
        assert result == ["mistral-7b"]

    async def test_anthropic_backend_sets_headers(self):
        from llm_gateway.services.federation_service import fetch_models_from_backend
        from llm_gateway.models import BackendType

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [{"id": "claude-3-opus"}]
        }

        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_response)

        result = await fetch_models_from_backend(
            client, "https://api.anthropic.com", BackendType.ANTHROPIC, api_key="sk-ant-test"
        )
        assert result == ["claude-3-opus"]
        call_headers = client.get.call_args[1]["headers"]
        assert call_headers["x-api-key"] == "sk-ant-test"
        assert "anthropic-version" in call_headers

    async def test_ollama_backend_handles_connection_error(self):
        from llm_gateway.services.federation_service import fetch_models_from_backend
        from llm_gateway.models import BackendType

        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        result = await fetch_models_from_backend(
            client, "http://dead-host:11434", BackendType.OLLAMA
        )
        assert result == []

    async def test_openai_backend_handles_http_error(self):
        from llm_gateway.services.federation_service import fetch_models_from_backend
        from llm_gateway.models import BackendType

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("401", request=MagicMock(), response=mock_response)
        )

        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_response)

        result = await fetch_models_from_backend(
            client, "https://api.openai.com", BackendType.OPENAI
        )
        assert result == []

    async def test_google_backend_returns_empty(self):
        from llm_gateway.services.federation_service import fetch_models_from_backend
        from llm_gateway.models import BackendType

        client = AsyncMock()
        result = await fetch_models_from_backend(
            client, "https://generativelanguage.googleapis.com", BackendType.GOOGLE
        )
        assert result == []

    async def test_groq_backend_uses_openai_format(self):
        from llm_gateway.services.federation_service import fetch_models_from_backend
        from llm_gateway.models import BackendType

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [{"id": "llama-3.2-70b"}]
        }

        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_response)

        result = await fetch_models_from_backend(
            client, "https://api.groq.com", BackendType.GROQ, api_key="gsk-test"
        )
        assert result == ["llama-3.2-70b"]

    async def test_custom_backend_uses_openai_format(self):
        from llm_gateway.services.federation_service import fetch_models_from_backend
        from llm_gateway.models import BackendType

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [{"id": "custom-model-v1"}]
        }

        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_response)

        result = await fetch_models_from_backend(
            client, "http://custom:8080", BackendType.CUSTOM
        )
        assert result == ["custom-model-v1"]

    async def test_ollama_strips_trailing_slash(self):
        from llm_gateway.services.federation_service import fetch_models_from_backend
        from llm_gateway.models import BackendType

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"models": []}

        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_response)

        await fetch_models_from_backend(
            client, "http://ollama:11434/", BackendType.OLLAMA
        )
        url = client.get.call_args[0][0]
        assert "//" not in url.replace("http://", "")

    async def test_api_key_sent_as_bearer_header(self):
        from llm_gateway.services.federation_service import fetch_models_from_backend
        from llm_gateway.models import BackendType

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"data": []}

        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_response)

        await fetch_models_from_backend(
            client, "https://api.openai.com", BackendType.OPENAI, api_key="sk-test"
        )
        call_headers = client.get.call_args[1]["headers"]
        assert call_headers["Authorization"] == "Bearer sk-test"

    async def test_no_api_key_sends_no_auth_header(self):
        from llm_gateway.services.federation_service import fetch_models_from_backend
        from llm_gateway.models import BackendType

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"models": []}

        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_response)

        await fetch_models_from_backend(
            client, "http://ollama:11434", BackendType.OLLAMA
        )
        call_headers = client.get.call_args[1]["headers"]
        assert "Authorization" not in call_headers
