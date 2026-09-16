import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock
import sys
import os

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

try:
    from llm_gateway.models import LLMBackend, BackendType
    from llm_gateway.routers.proxy import get_target_backend
    from llm_gateway.provider_registry import register_provider, register_model, BackendProvider, ProviderModel
    from llm_gateway.services import ConfigService
except ImportError as e:
    import pytest
    pytest.skip(f"Skipping tests due to missing dependencies: {e}", allow_module_level=True)

class TestProviderIntegration(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_get_target_backend_with_provider(self):
        async def run_test():
            # Setup registry
            provider = BackendProvider(id="openai", name="OpenAI", base_url="http://openai", supported_models=["gpt-4"], auth_type="api_key")
            register_provider(provider)
            model = ProviderModel(provider_id="openai", model_name="gpt-4", max_tokens=1000)
            # Ensure model registered for lookup
            # register_model call requires provider to be in _providers, which we just did.
            # But wait, register_model takes ProviderModel
            register_model(model)

            # Setup mocks
            service = MagicMock(spec=ConfigService)
            session = AsyncMock()
            
            # Mock Backends in DB
            backend_openai = LLMBackend(name="openai-main", base_url="http://openai", backend_type=BackendType.OPENAI, models=["gpt-4"])
            
            service.list_backends.return_value = [backend_openai]

            # Test explicit provider match
            found = await get_target_backend("gpt-4", service, session, provider="openai")
            self.assertEqual(found, backend_openai)
        
        self.loop.run_until_complete(run_test())

    def test_get_target_backend_fallback(self):
        async def run_test():
            # Setup mocks
            service = MagicMock(spec=ConfigService)
            session = AsyncMock()
            backend = LLMBackend(name="default", base_url="http://def", backend_type=BackendType.OLLAMA, models=["llama2"])
            service.list_backends.return_value = [backend]

            # Test fallback (no provider specified)
            found = await get_target_backend("llama2", service, session)
            # Fallback logic returns first backend if model matches
            self.assertEqual(found, backend)
        
        self.loop.run_until_complete(run_test())

if __name__ == "__main__":
    unittest.main()
