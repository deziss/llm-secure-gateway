"""Prometheus pull endpoint: ``GET /metrics``.

The OTel ``PrometheusMetricReader`` registers with prometheus_client's default registry when
it is constructed. ``/metrics`` renders that registry. Enabled by ``OBS_PROMETHEUS`` (default
true) and independent of ``ENABLE_TELEMETRY``. It is pull-only, so it opens no outbound
connection.

Bearer check: set ``OBS_METRICS_TOKEN``; requests must send ``Authorization: Bearer <token>``.
The route is public to AuthMiddleware (the token is its guard). With no token the endpoint
answers 403 unless ``OBS_METRICS_ALLOW_ANONYMOUS=true`` (then keep it on a private network).

If the optional packages are missing, the endpoint answers 503 and nothing else breaks.
"""
from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["observability"])

_TRUTHY = {"1", "true", "yes", "on", "enabled"}
_STATE: dict = {"reader": None, "scrapes": 0, "last_ok": None, "last_error": None}
_LOCK = threading.Lock()


def prometheus_enabled() -> bool:
    return os.getenv("OBS_PROMETHEUS", "true").strip().lower() in _TRUTHY


def anonymous_allowed() -> bool:
    return os.getenv("OBS_METRICS_ALLOW_ANONYMOUS", "false").strip().lower() in _TRUTHY


def build_prometheus_reader():
    """Create the OTel reader once. Returns None when the optional package is missing."""
    with _LOCK:
        if _STATE["reader"] is not None:
            return _STATE["reader"]
        if anonymous_allowed() and not os.getenv("OBS_METRICS_TOKEN", "").strip():
            logger.warning("/metrics is served WITHOUT authentication (OBS_METRICS_ALLOW_ANONYMOUS=true); "
                           "keep it on a private network or set OBS_METRICS_TOKEN")
        try:
            from opentelemetry.exporter.prometheus import PrometheusMetricReader
        except ImportError:
            logger.warning("opentelemetry-exporter-prometheus not installed; /metrics will return 503")
            return None
        try:
            _STATE["reader"] = PrometheusMetricReader()
        except Exception as exc:
            logger.warning("Prometheus reader setup failed: %s", exc)
            _STATE["last_error"] = str(exc)
            return None
        return _STATE["reader"]


def prometheus_status() -> Optional[dict]:
    """Pseudo-sink entry for the admin Integrations tab. None when /metrics is disabled."""
    if not prometheus_enabled():
        return None
    with _LOCK:
        last_ok = _STATE["last_ok"]
        last_error = _STATE["last_error"]
        scrapes = _STATE["scrapes"]
    if _STATE["reader"] is None and last_error is None:
        last_error = "reader not initialised (telemetry setup has not run or package missing)"
    return {
        "name": "prometheus",
        "signals": ["metrics"],
        "endpoint": "/metrics",
        "last_ok": last_ok,
        "last_error": last_error,
        "exported": scrapes,
        "failed": 0,
    }


def _check_token(request: Request) -> bool:
    token = os.getenv("OBS_METRICS_TOKEN", "").strip()
    if not token:
        return anonymous_allowed()
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[len("Bearer "):].encode(), token.encode())


@router.get("/metrics", include_in_schema=False)
async def metrics_endpoint(request: Request) -> Response:
    if not prometheus_enabled():
        return PlainTextResponse("metrics disabled (OBS_PROMETHEUS=false)", status_code=404)
    if not _check_token(request):
        if not os.getenv("OBS_METRICS_TOKEN", "").strip():
            return PlainTextResponse("forbidden: set OBS_METRICS_TOKEN or OBS_METRICS_ALLOW_ANONYMOUS=true",
                                     status_code=403)
        return PlainTextResponse("unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"})
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    except ImportError:
        return PlainTextResponse("prometheus_client not installed", status_code=503)
    try:
        body = generate_latest()
    except Exception as exc:  # a broken collector must not 500 the scrape
        with _LOCK:
            _STATE["last_error"] = str(exc)[:500]
        return PlainTextResponse(f"scrape failed: {exc}", status_code=503)
    with _LOCK:
        _STATE["scrapes"] += 1
        _STATE["last_ok"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _STATE["last_error"] = None
    return Response(content=body, media_type=CONTENT_TYPE_LATEST)
