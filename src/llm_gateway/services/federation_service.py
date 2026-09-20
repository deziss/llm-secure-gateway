import asyncio
import httpx
import logging
from typing import List, Optional
from ..database import engine
from sqlalchemy.ext.asyncio import AsyncSession
from ..services import get_config_service
from ..models import BackendType

logger = logging.getLogger(__name__)

async def fetch_models_from_backend(client: httpx.AsyncClient, base_url: str, backend_type: BackendType, api_key: Optional[str] = None) -> List[str]:
    """Fetch the list of available models from a specific backend."""
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Determine the correct endpoint and parsing logic based on backend type
    if backend_type == BackendType.OLLAMA:
        url = f"{base_url.rstrip('/')}/api/tags"
        try:
            response = await client.get(url, headers=headers, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
        except Exception as e:
            logger.error(f"Failed to fetch models from Ollama backend at {base_url}: {e}")
            return []
            
    elif backend_type in [BackendType.VLLM, BackendType.OPENAI, BackendType.GROQ, BackendType.CUSTOM]:
        url = f"{base_url.rstrip('/')}/v1/models"
        try:
            response = await client.get(url, headers=headers, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            return [model["id"] for model in data.get("data", [])]
        except Exception as e:
            logger.error(f"Failed to fetch models from standard backend at {base_url}: {e}")
            return []

    elif backend_type == BackendType.LLAMACPP:
        base = base_url.rstrip("/")
        models_url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
        try:
            response = await client.get(models_url, headers=headers, timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                models = [model["id"] for model in data.get("data", []) if "id" in model]
                if models:
                    return models
        except Exception as e:
            logger.debug(f"llama.cpp /v1/models check failed: {e}")

        # Fallback to llama.cpp native /props endpoint
        clean_base = base[:-3] if base.endswith("/v1") else base
        props_url = f"{clean_base}/props"
        try:
            response = await client.get(props_url, headers=headers, timeout=5.0)
            if response.status_code == 200:
                p_data = response.json()
                raw_model = (
                    p_data.get("default_generation_settings", {}).get("model")
                    or p_data.get("model")
                    or ""
                )
                if raw_model:
                    import os
                    model_id = os.path.basename(raw_model)
                    if model_id.endswith(".gguf"):
                        model_id = model_id[:-5]
                    return [model_id]
                return ["default"]
        except Exception as e:
            logger.warning(f"Failed to fetch models from llama.cpp backend at {base_url}: {e}")
            return []
        return []
            
    elif backend_type == BackendType.ANTHROPIC:
        # Anthropic has a /v1/models endpoint as of recently, but if not, we can default to empty or static
        url = f"{base_url.rstrip('/')}/v1/models"
        headers["x-api-key"] = api_key or ""
        headers["anthropic-version"] = "2023-06-01"
        try:
            response = await client.get(url, headers=headers, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            return [model["id"] for model in data.get("data", [])]
        except Exception as e:
            logger.warning(f"Failed to fetch models from Anthropic backend at {base_url}: {e}")
            return []
            
    else:
        logger.warning(f"Unsupported backend type {backend_type} for model federation.")
        return []

async def poll_all_backends() -> None:
    """Polls all registered backends and updates their 'models' list in the DB."""
    config_service = get_config_service()
    
    # We create a new DB session for the background task
    async with AsyncSession(engine) as session:
        # Check if Federation is enabled globally
        is_enabled = await config_service.get_setting(session, "ENABLE_MODEL_FEDERATION", "false")
        if str(is_enabled).lower() != "true":
            return

        backends = await config_service.list_backends(session)
        if not backends:
            return

        # Explicit timeout: unreachable backends must not stretch a polling
        # cycle (and the DB session it holds) out to httpx's defaults.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0)
        ) as client:
            tasks = []
            for backend in backends:
                # Resolve the API key if encrypted
                api_key = backend.api_key
                if api_key and hasattr(api_key, "get_secret_value"):
                    api_key = api_key.get_secret_value()

                tasks.append(fetch_models_from_backend(client, backend.base_url, backend.backend_type, api_key))
            
            # Run all fetch tasks concurrently
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for backend, fetched_models in zip(backends, results):
                if isinstance(fetched_models, list) and fetched_models:
                    # Check if models have changed
                    current_models = set(backend.models)
                    new_models = set(fetched_models)
                    
                    if current_models != new_models:
                        logger.info(f"Updating models for backend '{backend.name}': {fetched_models}")
                        backend.models = fetched_models
                        session.add(backend)
        
        await session.commit()

async def federation_loop() -> None:
    """Continuous loop to poll backends every 30 seconds."""
    logger.info("Starting background Model Federation Service...")
    while True:
        try:
            await poll_all_backends()
        except Exception as e:
            logger.exception(f"Error in federation polling loop: {e}")
        
        await asyncio.sleep(30)
