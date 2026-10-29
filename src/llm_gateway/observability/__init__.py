"""Observability core: in-process live stats, usage parsing, process stats and exporters.

Data path: collect (request hooks, usage parsing, process stats) -> store (in-process
rings) -> export (OpenTelemetry instruments, OTLP presets, Prometheus /metrics).

Keep this module light: it is imported by telemetry/ and routers, so it must not import
OpenTelemetry or anything that would create an import cycle with main.py.
"""
import os
import socket

#: Gateway version. main.py re-exports it as ``_VERSION``; kept here to avoid a
#: circular import (main -> telemetry -> observability -> main).
VERSION = "0.14.1"

_INSTANCE_ID: str | None = None


def instance_id() -> str:
    """Stable identifier for this process, used as ``service.instance.id``.

    Hostname based (container id inside docker), so two replicas never collide.
    ``OBS_INSTANCE_ID`` overrides it.
    """
    global _INSTANCE_ID
    if _INSTANCE_ID is None:
        _INSTANCE_ID = os.getenv("OBS_INSTANCE_ID", "").strip() or socket.gethostname()
    return _INSTANCE_ID


from .store import get_live_stats, LiveStats  # noqa: E402  (light, no OTel import)

__all__ = ["VERSION", "instance_id", "get_live_stats", "LiveStats"]
