# Provider Registry for llm-gateway-v2
"""In‑memory registry for backend providers and their supported models.
This module defines data structures and helper functions used by the gateway
to resolve which backend to route a request to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BackendProvider:
    """Definition of a backend provider.

    Attributes
    ----------
    id: str
        Unique identifier for the provider (e.g., "openai").
    name: str
        Human‑readable name.
    base_url: str
        Base URL for the provider's API.
    supported_models: List[str]
        List of model names the provider supports.
    auth_type: str
        One of "api_key", "oauth", or "none".
    """

    id: str
    name: str
    base_url: str
    fallback_urls: List[str] = field(default_factory=list)
    supported_models: List[str] = field(default_factory=list)
    auth_type: str = "api_key"


@dataclass(frozen=True)
class ProviderModel:
    """Model metadata scoped to a provider.

    Attributes
    ----------
    provider_id: str
        Identifier of the provider this model belongs to.
    model_name: str
        Name of the model (e.g., "gpt-4.1").
    max_tokens: int
        Maximum token limit for the model.
    pricing: Optional[Dict[str, float]]
        Optional pricing information (input/output cost).
    """

    provider_id: str
    model_name: str
    max_tokens: int
    pricing: Optional[Dict[str, float]] = None

# ---------------------------------------------------------------------------
# Registry implementation (simple in‑memory singleton)
# ---------------------------------------------------------------------------

_providers: Dict[str, BackendProvider] = {}
_models: Dict[str, Dict[str, ProviderModel]] = {}


def register_provider(provider: BackendProvider) -> None:
    """Add a provider to the registry.

    If a provider with the same ``id`` already exists it will be overwritten.
    """
    _providers[provider.id] = provider
    _models.setdefault(provider.id, {})


def get_provider(provider_id: str) -> Optional[BackendProvider]:
    """Retrieve a provider by its identifier."""
    return _providers.get(provider_id)


def list_providers() -> List[BackendProvider]:
    """Return a list of all registered providers."""
    return list(_providers.values())


def register_model(model: ProviderModel) -> None:
    """Register a model under its provider.

    Raises
    ------
    KeyError
        If the provider does not exist in the registry.
    """
    if model.provider_id not in _providers:
        raise KeyError(f"Provider '{model.provider_id}' is not registered")
    _models[model.provider_id][model.model_name] = model


def get_model(provider_id: str, model_name: str) -> Optional[ProviderModel]:
    """Retrieve a model for a given provider."""
    return _models.get(provider_id, {}).get(model_name)


def list_models(provider_id: str) -> List[ProviderModel]:
    """List all models registered for a specific provider."""
    return list(_models.get(provider_id, {}).values())

# ---------------------------------------------------------------------------
# Example default registration (can be overridden by env/config at runtime)
# ---------------------------------------------------------------------------

def _register_defaults() -> None:
    """Register a few common providers and models.
    This runs at import time so the registry is ready for use.
    """
    openai = BackendProvider(
        id="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        supported_models=["gpt-4.1", "gpt-4", "gpt-3.5-turbo"],
        auth_type="api_key",
    )
    register_provider(openai)
    for m in openai.supported_models:
        register_model(ProviderModel(provider_id=openai.id, model_name=m, max_tokens=8192, pricing={"input": 0.03, "output": 0.06}))

    ollama = BackendProvider(
        id="ollama",
        name="Local Ollama",
        base_url="http://localhost:11434",
        fallback_urls=["http://10.120.130.55:11434"],
        supported_models=["llama3.2:latest"],
        auth_type="none",
    )
    register_provider(ollama)
    for m in ollama.supported_models:
        register_model(ProviderModel(provider_id=ollama.id, model_name=m, max_tokens=4096))

    vllm = BackendProvider(
        id="vllm",
        name="Local vLLM",
        base_url="http://localhost:8000/v1",
        supported_models=["Qwen/Qwen2.5-7B-Instruct"],
        auth_type="none",
    )
    register_provider(vllm)
    for m in vllm.supported_models:
        register_model(ProviderModel(provider_id=vllm.id, model_name=m, max_tokens=8192))


_register_defaults()
