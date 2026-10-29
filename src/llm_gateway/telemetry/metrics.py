"""Compatibility layer: the instruments live in observability/export/instruments.py.

``setup_metrics()`` and ``record_request_metrics()`` keep their names so existing callers
(routes, streaming wrapper, tests) do not change.
"""
from ..observability.export.instruments import setup_instruments, record_request


def setup_metrics(meter=None) -> None:
    setup_instruments(meter)


def record_request_metrics(
    backend_url: str,
    model: str,
    endpoint_uri: str,
    user_name: str,
    success: bool = True,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    request_duration: float = 0.0,
    backend_latency: float = 0.0,
    *,
    server: str = "",
    server_type: str = "",
    time_to_first_token: float = 0.0,
    generation_tps: float = 0.0,
) -> None:
    record_request(
        backend_url=backend_url, model=model, endpoint_uri=endpoint_uri, user_name=user_name,
        success=success, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        total_tokens=total_tokens, request_duration=request_duration, backend_latency=backend_latency,
        server=server, server_type=server_type,
        time_to_first_token=time_to_first_token, generation_tps=generation_tps,
    )
