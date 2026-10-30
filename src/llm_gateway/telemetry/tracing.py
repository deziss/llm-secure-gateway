import os
import logging
from typing import Dict, Optional

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from phoenix.otel import register

from .config import telemetry_enabled

# Global tracer (default, non-tenant specific)
tracer = trace.get_tracer(__name__)


class PhoenixTraceManager:
    """
    Manages per-tenant Phoenix tracer providers for multi-tenant trace isolation.
    Creates separate projects in Phoenix for each owner/backend combination.
    """

    _tracers: Dict[str, TracerProvider] = {}
    _managed: Dict[str, TracerProvider] = {}  # per-tenant providers when Phoenix is managed in the UI
    _phoenix_endpoint: str = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006")
    _phoenix_api_key: Optional[str] = os.getenv("PHOENIX_API_KEY")
    if telemetry_enabled() and not _phoenix_api_key:
        logging.info("PhoenixTraceManager: no PHOENIX_API_KEY set; exporting unauthenticated.")

    @classmethod
    def _resolve_target(cls) -> tuple[str, Optional[Dict[str, str]]]:
        """Endpoint and headers from the phoenix preset (self-hosted, Phoenix Cloud or Arize AX)."""
        from ..observability.export.presets import resolve, PresetError
        try:
            target = resolve("phoenix").traces
            if target is not None:
                return target.endpoint, dict(target.headers) or None
        except PresetError as exc:
            logging.warning("PhoenixTraceManager: %s; using PHOENIX_COLLECTOR_ENDPOINT", exc)
        endpoint = cls._phoenix_endpoint
        if not endpoint.endswith("/v1/traces"):
            endpoint = f"{endpoint}/v1/traces"
        return endpoint, cls._get_headers()

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
        # Phoenix configured in the admin UI takes precedence over the environment setup.
        from ..observability.export import manager
        mode = manager.phoenix_mode()
        if mode == "per_tenant":
            return cls._managed_tracer(owner_id, backend_name)
        if mode == "single" or not telemetry_enabled():
            # Global provider: UI-managed integrations (and env sinks, when on) receive the spans.
            return trace.get_tracer(__name__)

        project_name = cls.get_project_name(owner_id, backend_name)

        if project_name not in cls._tracers:
            try:
                endpoint, headers = cls._resolve_target()
                tracer_provider = register(
                    project_name=project_name,
                    endpoint=endpoint,
                    headers=headers,
                    batch=True,
                    auto_instrument=False,
                    set_global_tracer_provider=False
                )
                # Non-Phoenix sinks (OTLP, Langfuse, ...) also see tenant spans.
                from ..observability.export.sinks import shared_span_processors
                for proc in shared_span_processors():
                    tracer_provider.add_span_processor(proc)
                cls._tracers[project_name] = tracer_provider
                logging.info(f"Created Phoenix tracer for project: {project_name}")
            except Exception as e:
                logging.warning(f"Failed to create Phoenix tracer for {project_name}: {e}")
                # Fallback to global tracer
                return trace.get_tracer(__name__)

        return cls._tracers[project_name].get_tracer(__name__)

    @classmethod
    def _managed_tracer(cls, owner_id: str, backend_name: Optional[str]) -> trace.Tracer:
        """A provider per tenant whose resource names the Phoenix project. Spans still go through
        the one managed processor, so every enabled integration sees them; only Phoenix uses the
        project attribute."""
        project_name = cls.get_project_name(owner_id, backend_name)
        provider = cls._managed.get(project_name)
        if provider is None:
            from .setup import build_resource
            from ..observability.export.manager import managed_span_processor
            provider = TracerProvider(resource=build_resource({"openinference.project.name": project_name}))
            provider.add_span_processor(managed_span_processor())
            cls._managed[project_name] = provider
        return provider.get_tracer(__name__)

    @classmethod
    def get_tracer_count(cls) -> int:
        """Return the number of cached tracer providers"""
        return len(cls._tracers)
