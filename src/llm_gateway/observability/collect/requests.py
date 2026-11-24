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
import uuid
from datetime import datetime, timezone
from typing import Optional

from ..store import LiveStats, get_live_stats

logger = logging.getLogger(__name__)


def classify_error(status_code: int) -> str:
    """Classify HTTP status into standard error taxonomy."""
    if status_code == 429:
        return "upstream_rate_limit"
    if status_code == 503:
        return "upstream_overload"
    if status_code in (500, 502, 504):
        return "upstream_failure"
    if status_code in (400, 403):
        return "guardrail_blocked"
    if status_code == 401:
        return "auth_failure"
    if status_code == 499:
        return "client_disconnect"
    return f"http_{status_code}"


def _type_str(server_type) -> str:
    if server_type is None:
        return "unknown"
    value = getattr(server_type, "value", server_type)
    return str(value)


class RequestTracker:
    """Tracks one dispatched request in the live store."""

    __slots__ = (
        "_store",
        "key",
        "start",
        "first_token_at",
        "finished",
        "_inflight",
        "is_stream",
        "request_id",
        "owner_id",
        "provider",
        "_live_tokens",
        "_live_deltas",
    )

    def __init__(
        self,
        store: LiveStats,
        key: tuple[str, str, str],
        start: float,
        is_stream: bool = False,
        request_id: Optional[str] = None,
        owner_id: Optional[str] = "anonymous",
        provider: Optional[str] = None,
    ) -> None:
        self._store = store
        self.key = key
        self.start = start
        self.first_token_at: Optional[float] = None
        self.finished = False
        self._inflight = 0
        self.is_stream = is_stream
        self.request_id = request_id
        self.owner_id = owner_id
        self.provider = provider
        self._live_tokens = 0
        self._live_deltas = 0

    def retarget(self, server: str, server_type, model: str) -> None:
        """Re-key a still-waiting request after fallback chose a different server/model."""
        if self.finished or self.first_token_at is not None:
            return
        new_key = (server or "unknown", _type_str(server_type), model or "unknown")
        new_key = self._store.resolve(new_key)
        if new_key != self.key:
            self._store.retarget(self.key, new_key)
            self.key = new_key
            self.provider = _type_str(server_type)

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

    def progress(self, total_output_tokens: int, total_deltas: Optional[int] = None) -> None:
        """Report tokens (and SSE delta events) produced so far by this still-running stream.

        Credits only the increment to the live second-buckets so tok/s updates while the
        request is in flight. The total is remembered so ``finish`` can avoid double-counting.
        """
        if self.finished:
            return
        if total_output_tokens > self._live_tokens:
            delta = total_output_tokens - self._live_tokens
            self._live_tokens = total_output_tokens
        else:
            delta = 0
        ddelta = 0
        if total_deltas is not None and total_deltas > self._live_deltas:
            ddelta = total_deltas - self._live_deltas
            self._live_deltas = total_deltas
        if delta <= 0 and ddelta <= 0:
            return
        try:
            self._store.progress(self.key, delta, deltas=ddelta)
        except Exception:  # never let stats break a request
            logger.debug("live stats progress failed", exc_info=True)

    def finish(
        self,
        *,
        success: bool = True,
        cancelled: bool = False,
        output_tokens: int = 0,
        prompt_tokens: int = 0,
        total_tokens: int = 0,
        estimated: bool = False,
        decode_seconds: Optional[float] = None,
        duration_s: Optional[float] = None,
        itl_ms: Optional[float] = None,
        status_code: int = 200,
        request_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        cost_usd: float = 0.0,
        cache_hit: bool = False,
        error_type: Optional[str] = None,
        phases: Optional[dict[str, float]] = None,
        delta_events: int = 0,
        **kwargs,
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
        dur = (now - self.start) if duration_s is None else duration_s
        self._store.finish(
            self.key,
            was_generating=self.first_token_at is not None,
            success=success,
            cancelled=cancelled,
            output_tokens=output_tokens,
            prompt_tokens=prompt_tokens,
            total_tokens=total_tokens,
            decode_seconds=decode_s,
            duration_s=dur,
            ttft=ttft,
            itl_ms=itl_ms,
            estimated=estimated,
            already_counted=self._live_tokens,
            delta_events=delta_events,
            already_counted_deltas=self._live_deltas,
            now=now,
        )
        if self._inflight:
            self._store.add_inflight_bytes(self.key, -self._inflight)
            self._inflight = 0

        try:
            from ..traces import RequestTrace, get_trace_buffer

            trace_status = status_code
            if not success and trace_status == 200:
                trace_status = 499 if cancelled else 500
            elif cancelled and trace_status == 200:
                trace_status = 499

            trace_error = error_type
            if trace_error is None and trace_status >= 400:
                trace_error = classify_error(trace_status)

            trace_req_id = (
                request_id
                or getattr(self, "request_id", None)
                or f"req-{uuid.uuid4().hex[:12]}"
            )
            trace_owner = (
                owner_id
                or getattr(self, "owner_id", None)
                or "anonymous"
            )
            trace_model = model or (self.key[2] if len(self.key) > 2 else "unknown")
            trace_provider = (
                provider
                or getattr(self, "provider", None)
                or (self.key[1] if len(self.key) > 1 else "unknown")
            )
            tot_tok = total_tokens if total_tokens > 0 else (prompt_tokens + output_tokens)

            trace = RequestTrace(
                request_id=trace_req_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                owner_id=trace_owner,
                model=trace_model,
                provider=trace_provider,
                status_code=trace_status,
                duration_ms=round(dur * 1000.0, 2),
                ttft_ms=round(ttft * 1000.0, 2) if ttft is not None else None,
                input_tokens=prompt_tokens,
                output_tokens=output_tokens,
                total_tokens=tot_tok,
                cost_usd=float(cost_usd or 0.0),
                cache_hit=bool(cache_hit),
                error_type=trace_error,
                phases=phases or {},
            )
            get_trace_buffer().record(trace)
        except Exception:
            logger.debug("recording trace failed", exc_info=True)

        return {"ttft": ttft, "decode_seconds": decode_s, "duration": dur}


def track_dispatch(
    server: str,
    server_type,
    model: str,
    store: Optional[LiveStats] = None,
    is_stream: bool = False,
    request_id: Optional[str] = None,
    owner_id: Optional[str] = "anonymous",
    provider: Optional[str] = None,
    **kwargs,
) -> RequestTracker:
    store = store or get_live_stats()
    key = store.resolve((server or "unknown", _type_str(server_type), model or "unknown"))
    now = store.now()
    store.dispatch(key, now)
    return RequestTracker(
        store,
        key,
        now,
        is_stream=is_stream,
        request_id=request_id,
        owner_id=owner_id,
        provider=provider or _type_str(server_type),
    )


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
    prompt_tokens: int = 0,
    total_tokens: int = 0,
    estimated: bool = False,
    decode_seconds: Optional[float] = None,
    duration_s: Optional[float] = None,
    itl_ms: Optional[float] = None,
    **kwargs,
) -> Optional[dict]:
    if tracker is None:
        return None
    try:
        return tracker.finish(
            success=success,
            cancelled=cancelled,
            output_tokens=output_tokens,
            prompt_tokens=prompt_tokens,
            total_tokens=total_tokens,
            estimated=estimated,
            decode_seconds=decode_seconds,
            duration_s=duration_s,
            itl_ms=itl_ms,
            **kwargs,
        )
    except Exception:  # never let stats break a request
        logger.debug("live stats finish failed", exc_info=True)
        return None
