import os
import logging
from typing import Dict, Optional

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from phoenix.otel import register

# Global tracer (default, non-tenant specific)
tracer = trace.get_tracer(__name__)


class PhoenixTraceManager:
    """
    Manages per-tenant Phoenix tracer providers for multi-tenant trace isolation.
    Creates separate projects in Phoenix for each owner/backend combination.
    """

    _tracers: Dict[str, TracerProvider] = {}
    _phoenix_endpoint: str = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006")
    _phoenix_api_key: Optional[str] = os.getenv("PHOENIX_API_KEY")
    if _phoenix_api_key:
        logging.info(f"PhoenixTraceManager loaded API Key: {_phoenix_api_key[:10]}...")
    else:
        logging.warning("PhoenixTraceManager: No API Key found in env!")

    @classmethod
    def _get_headers(cls) -> Optional[Dict[str, str]]:
        """Get authentication headers for Phoenix if API key is configured."""
        if cls._phoenix_api_key:
            return {"authorization": f"Bearer {cls._phoenix_api_key}"}
        return None

    @classmethod
    def get_project_name(cls, owner_id: str, backend_name: Optional[str] = None) -> str:
        # Sanitize owner_id (remove special chars, limit length)
        sanitized_owner = owner_id.replace(":", "-").replace("/", "-")[:50]

        if backend_name:
            sanitized_backend = backend_name.replace(":", "-").replace("/", "-")[:30]
            return f"gateway-{sanitized_owner}-{sanitized_backend}"
        return f"gateway-{sanitized_owner}"

    @classmethod
    def get_tracer(cls, owner_id: str, backend_name: Optional[str] = None) -> trace.Tracer:
        """
        Create & Cache tracer for the given owner/backend combination.
        OpenTelemetry Tracer instance for the project
        """
        project_name = cls.get_project_name(owner_id, backend_name)

        if project_name not in cls._tracers:
            try:
                # Use register() as requested, with corrected endpoint
                endpoint = cls._phoenix_endpoint
                if not endpoint.endswith("/v1/traces"):
                    endpoint = f"{endpoint}/v1/traces"

                tracer_provider = register(
                    project_name=project_name,
                    endpoint=endpoint,
                    headers=cls._get_headers(),
                    batch=True,
                    auto_instrument=False,
                    set_global_tracer_provider=False
                )
                cls._tracers[project_name] = tracer_provider
                logging.info(f"Created Phoenix tracer for project: {project_name}")
            except Exception as e:
                logging.warning(f"Failed to create Phoenix tracer for {project_name}: {e}")
                # Fallback to global tracer
                return trace.get_tracer(__name__)

        return cls._tracers[project_name].get_tracer(__name__)

    @classmethod
    def get_tracer_count(cls) -> int:
        """Return the number of cached tracer providers"""
        return len(cls._tracers)
