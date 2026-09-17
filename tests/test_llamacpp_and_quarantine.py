import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from sqlmodel import select
from llm_gateway.models import BackendType, LLMBackend
from llm_gateway.services.federation_service import fetch_models_from_backend
from llm_gateway.services.model_metadata_service import fetch_llamacpp_props
from llm_gateway.proxy_helpers import (
    quarantine_model,
    is_model_quarantined,
    get_active_quarantines,
    clear_model_quarantine,
    try_with_cross_provider_fallback,
    _model_quarantines,
)

def test_backend_type_llamacpp():
    assert BackendType.LLAMACPP == "llamacpp"
    # Test normalization of aliases
    assert BackendType("llama.cpp") == BackendType.LLAMACPP
    assert BackendType("llama_cpp") == BackendType.LLAMACPP
    assert BackendType("llamacpp") == BackendType.LLAMACPP

@pytest.mark.asyncio
async def test_fetch_models_llamacpp_v1_models():
    client = httpx.AsyncClient()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {"id": "mistral-7b-instruct-v0.3.gguf"},
            {"id": "llama-3-8b-instruct.gguf"}
        ]
    }

    with patch.object(client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        models = await fetch_models_from_backend(client, "http://localhost:8080/v1", BackendType.LLAMACPP)
        assert models == ["mistral-7b-instruct-v0.3.gguf", "llama-3-8b-instruct.gguf"]
        mock_get.assert_called_once()
        assert "/models" in mock_get.call_args[0][0]

@pytest.mark.asyncio
async def test_fetch_models_llamacpp_props_fallback():
    client = httpx.AsyncClient()
    # First call to /v1/models fails or returns empty data
    resp_empty = MagicMock()
    resp_empty.status_code = 200
    resp_empty.json.return_value = {"data": []}

    # Second call to /props returns generation settings with model path
    resp_props = MagicMock()
    resp_props.status_code = 200
    resp_props.json.return_value = {
        "default_generation_settings": {
            "n_ctx": 8192,
            "model": "/models/qwen2.5-coder-7b-instruct.gguf"
        }
    }

    with patch.object(client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [resp_empty, resp_props]
        models = await fetch_models_from_backend(client, "http://localhost:8080", BackendType.LLAMACPP)
        assert models == ["qwen2.5-coder-7b-instruct"]
        assert mock_get.call_count == 2

@pytest.mark.asyncio
async def test_fetch_llamacpp_props():
    client = httpx.AsyncClient()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "default_generation_settings": {"n_ctx": 16384, "model": "phi-4.gguf"},
        "total_slots": 2
    }

    with patch.object(client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        props = await fetch_llamacpp_props("http://localhost:8080", client)
        assert props["n_ctx"] == 16384
        assert props["model"] == "phi-4.gguf"
        assert props["total_slots"] == 2

def test_granular_model_quarantine():
    _model_quarantines.clear()
    assert not is_model_quarantined("node-1", "llama-3-8b")
    
    # Quarantine for 100 seconds
    quarantine_model("node-1", "llama-3-8b", 100, "Test 429")
    assert is_model_quarantined("node-1", "llama-3-8b")
    # Other models on the same backend are unaffected
    assert not is_model_quarantined("node-1", "qwen-7b")
    # Same model on another backend is unaffected
    assert not is_model_quarantined("node-2", "llama-3-8b")

    active = get_active_quarantines()
    assert len(active) == 1
    assert active[0]["backend_name"] == "node-1"
    assert active[0]["model_name"] == "llama-3-8b"
    assert active[0]["remaining_seconds"] > 0

    clear_model_quarantine("node-1", "llama-3-8b")
    assert not is_model_quarantined("node-1", "llama-3-8b")
    _model_quarantines.clear()
