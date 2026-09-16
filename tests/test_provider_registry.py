"""
Unit tests for the provider registry module.
"""
import pytest
import llm_gateway.provider_registry as reg


class TestBackendProvider:
    def test_create_provider(self):
        provider = reg.BackendProvider(
            id="test-openai",
            name="Test OpenAI",
            base_url="https://api.openai.com/v1",
        )
        assert provider.id == "test-openai"
        assert provider.name == "Test OpenAI"
        assert provider.base_url == "https://api.openai.com/v1"

    def test_default_fields(self):
        provider = reg.BackendProvider(
            id="p1", name="P1", base_url="http://localhost"
        )
        assert provider.fallback_urls == []
        assert provider.supported_models == []
        assert provider.auth_type == "api_key"

    def test_frozen_dataclass(self):
        provider = reg.BackendProvider(
            id="p1", name="P1", base_url="http://localhost"
        )
        with pytest.raises(AttributeError):
            provider.id = "changed"


class TestProviderModel:
    def test_create_model(self):
        model = reg.ProviderModel(
            provider_id="openai",
            model_name="gpt-4",
            max_tokens=8192,
        )
        assert model.provider_id == "openai"
        assert model.model_name == "gpt-4"
        assert model.max_tokens == 8192

    def test_default_pricing_is_none(self):
        model = reg.ProviderModel(
            provider_id="openai", model_name="gpt-4", max_tokens=8192
        )
        assert model.pricing is None

    def test_with_pricing(self):
        model = reg.ProviderModel(
            provider_id="openai",
            model_name="gpt-4",
            max_tokens=8192,
            pricing={"input": 0.03, "output": 0.06},
        )
        assert model.pricing["input"] == 0.03
        assert model.pricing["output"] == 0.06


class TestProviderRegistry:
    def setup_method(self):
        reg._providers.clear()
        reg._models.clear()

    def test_register_and_get_provider(self):
        provider = reg.BackendProvider(
            id="test-p", name="Test", base_url="http://localhost"
        )
        reg.register_provider(provider)
        result = reg.get_provider("test-p")
        assert result is provider

    def test_get_nonexistent_provider_returns_none(self):
        assert reg.get_provider("nonexistent") is None

    def test_list_providers_empty(self):
        assert reg.list_providers() == []

    def test_list_providers(self):
        p1 = reg.BackendProvider(id="p1", name="P1", base_url="http://a")
        p2 = reg.BackendProvider(id="p2", name="P2", base_url="http://b")
        reg.register_provider(p1)
        reg.register_provider(p2)
        result = reg.list_providers()
        assert len(result) == 2
        ids = {p.id for p in result}
        assert ids == {"p1", "p2"}

    def test_register_provider_overwrites(self):
        p1 = reg.BackendProvider(id="dup", name="V1", base_url="http://a")
        p2 = reg.BackendProvider(id="dup", name="V2", base_url="http://b")
        reg.register_provider(p1)
        reg.register_provider(p2)
        result = reg.get_provider("dup")
        assert result.name == "V2"

    def test_register_model(self):
        provider = reg.BackendProvider(id="p1", name="P1", base_url="http://a")
        reg.register_provider(provider)
        model = reg.ProviderModel(provider_id="p1", model_name="m1", max_tokens=4096)
        reg.register_model(model)
        result = reg.get_model("p1", "m1")
        assert result is model

    def test_register_model_unknown_provider_raises(self):
        model = reg.ProviderModel(provider_id="unknown", model_name="m1", max_tokens=4096)
        with pytest.raises(KeyError, match="unknown"):
            reg.register_model(model)

    def test_get_model_nonexistent_returns_none(self):
        assert reg.get_model("nonexistent", "model") is None

    def test_list_models_empty(self):
        assert reg.list_models("nonexistent") == []

    def test_list_models(self):
        provider = reg.BackendProvider(id="p1", name="P1", base_url="http://a")
        reg.register_provider(provider)
        m1 = reg.ProviderModel(provider_id="p1", model_name="m1", max_tokens=4096)
        m2 = reg.ProviderModel(provider_id="p1", model_name="m2", max_tokens=8192)
        reg.register_model(m1)
        reg.register_model(m2)
        result = reg.list_models("p1")
        assert len(result) == 2
        names = {m.model_name for m in result}
        assert names == {"m1", "m2"}

    def test_models_isolated_between_providers(self):
        p1 = reg.BackendProvider(id="p1", name="P1", base_url="http://a")
        p2 = reg.BackendProvider(id="p2", name="P2", base_url="http://b")
        reg.register_provider(p1)
        reg.register_provider(p2)
        reg.register_model(reg.ProviderModel(provider_id="p1", model_name="m1", max_tokens=4096))
        reg.register_model(reg.ProviderModel(provider_id="p2", model_name="m2", max_tokens=8192))
        assert len(reg.list_models("p1")) == 1
        assert len(reg.list_models("p2")) == 1
        assert reg.get_model("p1", "m2") is None
        assert reg.get_model("p2", "m1") is None


class TestDefaultRegistration:
    def test_defaults_registered_at_import(self):
        """The module registers openai and ollama defaults on import."""
        # Re-register defaults since setup_method clears state
        reg._register_defaults()
        openai = reg.get_provider("openai")
        assert openai is not None
        assert openai.name == "OpenAI"

        ollama = reg.get_provider("ollama")
        assert ollama is not None
        assert ollama.name == "Local Ollama"

    def test_default_openai_models(self):
        reg._register_defaults()
        models = reg.list_models("openai")
        names = {m.model_name for m in models}
        assert "gpt-4.1" in names
        assert "gpt-4" in names

    def test_default_ollama_models(self):
        reg._register_defaults()
        models = reg.list_models("ollama")
        names = {m.model_name for m in models}
        assert "llama3.2:latest" in names
