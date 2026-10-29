"""Request lifecycle hooks used by the proxy routes and the streaming wrapper.

Usage from a route::

    tracker = track_dispatch(backend.name, backend.backend_type, model)   # before dispatch
    r, used_url, backend, client = await try_with_cross_provider_fallback(...)
    tracker.retarget(backend.name, backend.backend_type, served_model)    # post-fallback
    ...
    stream_with_telemetry(..., tracker=tracker)                            # first byte / done

Every tracker is finished exactly once; ``finish`` is idempotent so error paths and the
streaming wrapper can both call it safely.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..store import LiveStats, get_live_stats

logger = logging.getLogger(__name__)


def _type_str(server_type) -> str:
    if server_type is None:
        return "unknown"
    value = getattr(server_type, "value", server_type)
    return str(value)


class RequestTracker:
    """Tracks one dispatched request in the live store."""

    __slots__ = ("_store", "key", "start", "first_token_at", "finished", "_inflight")

    def __init__(self, store: LiveStats, key: tuple[str, str, str], start: float) -> None:
        self._store = store
        self.key = key
        self.start = start
        self.first_token_at: Optional[float] = None
        self.finished = False
        self._inflight = 0

    def retarget(self, server: str, server_type, model: str) -> None:
        """Re-key a still-waiting request after fallback chose a different server/model."""
        if self.finished or self.first_token_at is not None:
            return
        new_key = (server or "unknown", _type_str(server_type), model or "unknown")
        new_key = self._store.resolve(new_key)
        if new_key != self.key:
            self._store.retarget(self.key, new_key)
            self.key = new_key

    def first_token(self) -> None:
        if self.finished or self.first_token_at is not None:
            return
        self.first_token_at = self._store.now()
        self._store.first_token(self.key)

    def set_inflight_bytes(self, total: int) -> None:
        if self.finished:
            return
        delta = total - self._inflight
        if delta:
            self._inflight = total
            self._store.add_inflight_bytes(self.key, delta)

    def finish(
        self,
        *,
        success: bool = True,
        cancelled: bool = False,
        output_tokens: int = 0,
        estimated: bool = False,
        decode_seconds: Optional[float] = None,
    ) -> Optional[dict]:
        """Close the request. Returns timing figures on the first call, None afterwards.

        ``decode_seconds`` overrides the measured generation time (Ollama reports its own).
        """
        if self.finished:
            return None
        self.finished = True
        now = self._store.now()
        ttft = None
        decode_s = None
        if self.first_token_at is not None:
            ttft = self.first_token_at - self.start
            decode_s = now - self.first_token_at
        if decode_seconds is not None and decode_seconds > 0:
            decode_s = decode_seconds
        self._store.finish(
            self.key,
            was_generating=self.first_token_at is not None,
            success=success,
            cancelled=cancelled,
            output_tokens=output_tokens,
            decode_seconds=decode_s,
            ttft=ttft,
            estimated=estimated,
            now=now,
        )
        if self._inflight:
            self._store.add_inflight_bytes(self.key, -self._inflight)
            self._inflight = 0
        return {"ttft": ttft, "decode_seconds": decode_s, "duration": now - self.start}


def track_dispatch(server: str, server_type, model: str, store: Optional[LiveStats] = None) -> RequestTracker:
    store = store or get_live_stats()
    key = store.resolve((server or "unknown", _type_str(server_type), model or "unknown"))
    now = store.now()
    store.dispatch(key, now)
    return RequestTracker(store, key, now)


def finish_open_tracker(tracker: Optional[RequestTracker]) -> None:
    """Safety net (response background task): close a tracker the stream never finished,
    e.g. when the response generator never started. A no-op for an already finished tracker."""
    if tracker is not None and not tracker.finished:
        track_done(tracker, success=False, cancelled=True)


def track_first_token(tracker: Optional[RequestTracker]) -> None:
    if tracker is not None:
        tracker.first_token()


def track_done(
    tracker: Optional[RequestTracker],
    *,
    success: bool = True,
    cancelled: bool = False,
    output_tokens: int = 0,
    estimated: bool = False,
    decode_seconds: Optional[float] = None,
) -> Optional[dict]:
    if tracker is None:
        return None
    try:
        return tracker.finish(
            success=success, cancelled=cancelled, output_tokens=output_tokens,
            estimated=estimated, decode_seconds=decode_seconds,
        )
    except Exception:  # never let stats break a request
        logger.debug("live stats finish failed", exc_info=True)
        return None
