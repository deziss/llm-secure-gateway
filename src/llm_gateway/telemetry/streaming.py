import asyncio
import codecs
import inspect
import json
import logging
import time
from typing import AsyncGenerator, Dict, Optional

from opentelemetry import trace

from .metrics import record_request_metrics
from ..observability.collect.requests import track_done, track_first_token
from ..observability.collect.usage import (
    Usage,
    estimate_completion,
    parse_sse_data_line,
    usage_from_body,
    usage_from_event,
)
from ..observability.export.semconv import set_llm_attributes
from ..translation import translate_response_stream
from ..translation.think_tag_parser import ThinkTagParser

logger = logging.getLogger(__name__)

# Telemetry Helpers
def flatten_attributes(data: Dict, prefix: str = "llm.input_messages", max_attrs: int = 64) -> Dict[str, str]:
    """
    Flatten a list of message dictionaries into OpenTelemetry attributes.
    Follows OpenInference semantic conventions.
    Capped at max_attrs to avoid filling the span attribute table and dropping status codes.
    """
    attributes = {}
    if not isinstance(data, list):
        return attributes

    count = 0
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if count >= max_attrs:
                return attributes
            attr_key = f"{prefix}.{i}.{key}"
            if isinstance(value, (str, int, float, bool)):
                attributes[attr_key] = str(value)
            elif value is None:
                attributes[attr_key] = ""
            else:
                attributes[attr_key] = json.dumps(value)
            count += 1
    return attributes


def _extract_deltas(obj: dict) -> tuple[str, str, bool]:
    """(content text, thinking text, has_delta) for one parsed stream event.

    Covers OpenAI chat chunks, Anthropic content_block_delta events and Ollama
    chat/generate lines.
    """
    text = thinking = ""
    has_delta = False
    choices = obj.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        delta = choices[0].get("delta")
        if isinstance(delta, dict):
            raw_text = delta.get("content")
            # vLLM reasoning-stream deltas use "reasoning"; OpenAI-compatible
            # clients/proxies relabel it "reasoning_content". Count both — with
            # neither, a pure-reasoning stream's tokens are invisible to live tok/s.
            raw_thinking = delta.get("reasoning_content") or delta.get("reasoning")
            text = raw_text if isinstance(raw_text, str) else ""
            thinking = raw_thinking if isinstance(raw_thinking, str) else ""
            has_delta = bool(text or thinking)
        return text, thinking, has_delta
    delta = obj.get("delta")
    if isinstance(delta, dict):
        dtype = delta.get("type")
        if dtype == "text_delta":
            text = delta.get("text") or ""
        elif dtype == "thinking_delta":
            thinking = delta.get("thinking") or ""
        has_delta = bool(text or thinking)
        return text, thinking, has_delta
    msg = obj.get("message")
    if isinstance(msg, dict):
        raw_text = msg.get("content")
        raw_thinking = msg.get("thinking")
        text = raw_text if isinstance(raw_text, str) else ""
        thinking = raw_thinking if isinstance(raw_thinking, str) else ""
        has_delta = bool(text or thinking)
        return text, thinking, has_delta
    if isinstance(obj.get("response"), str) and obj.get("response"):
        return obj["response"], "", True
    return "", "", False


def _text_of_body(raw: bytes) -> str:
    """Assistant text of a complete non-streaming body, for the no-usage estimate."""
    try:
        obj = json.loads(raw.decode("utf-8", errors="ignore"))
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(obj, dict):
        return ""
    choices = obj.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        msg = choices[0].get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), str):
            return msg["content"]
    if isinstance(obj.get("message"), dict) and isinstance(obj["message"].get("content"), str):
        return obj["message"]["content"]
    content = obj.get("content")
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    if isinstance(obj.get("response"), str):
        return obj["response"]
    return ""


class _StreamScan:
    """Accumulates usage and assistant text from complete stream lines."""

    def __init__(self) -> None:
        self.usage = Usage()
        self.usage_seen = False
        self.delta_count = 0
        self.content_parts: list[str] = []
        self.thinking_parts: list[str] = []

    def line(self, line: str, content: bool = True) -> None:
        obj = parse_sse_data_line(line)
        if obj is None:
            return
        u = usage_from_event(obj)
        if u is not None:
            self.usage.merge(u)
            self.usage_seen = True
        text, thinking, has_delta = _extract_deltas(obj)
        if has_delta:
            self.delta_count += 1
        if content:
            if text:
                self.content_parts.append(text)
            if thinking:
                self.thinking_parts.append(thinking)


async def stream_with_telemetry(
    response,
    start_time: float,
    backend_url: str,
    model: str,
    endpoint_uri: str,
    user_name: str,
    span: trace.Span,
    client=None,
    translation_mode: str = "none",
    normalize_thinking: bool = False,
    on_complete=None,
    *,
    server_name: Optional[str] = None,
    server_type: Optional[str] = None,
    tracker=None,
    output_guardrail=None,
    output_guardrail_action: str = "block",
    on_output_violation=None,
    pii_scrubber=None,
    on_pii_mask=None,
) -> AsyncGenerator[bytes, None]:
    """
    Async generator to wrap a streaming response, translate format if configured,
    optionally normalize/strip reasoning tags, record telemetry metrics, and end the span cleanly.

    Metrics and ``on_complete`` run exactly once per request, in every outcome: normal end,
    upstream error (recorded as failed), and client disconnect (``CancelledError`` or
    ``GeneratorExit``: recorded as cancelled, then re-raised).

    ``tracker`` is a RequestTracker from observability.collect.requests; it receives the first
    byte, in-flight buffer size and the final figures for the live stats.
    """
    server = server_name or backend_url
    accumulated_chunks = []
    accumulated_bytes = 0
    time_to_first_token = None
    first_token_wall = None
    has_error = False
    finalized = False
    # An upstream 4xx/5xx that streams cleanly is still a failed request.
    try:
        resp_status = int(getattr(response, "status_code", 200))
        upstream_ok = resp_status < 400
    except (TypeError, ValueError):
        resp_status = 200
        upstream_ok = True

    # Thinking normalization rewrites SSE lines, so it only applies to an
    # event stream. A plain JSON body (embeddings, non-streaming chat) is
    # passed through byte for byte: rewriting it line by line used to insert
    # newlines mid-document and overrun the upstream Content-Length.
    content_type = ""
    try:
        content_type = (response.headers.get("content-type") or "").lower()
    except Exception:
        pass
    is_sse = "text/event-stream" in content_type
    parser = ThinkTagParser() if (normalize_thinking and is_sse) else None

    # Network chunks split lines (and multi-byte UTF-8 characters) at
    # arbitrary points. Decode incrementally and only act on complete lines;
    # the unfinished tail waits in a buffer for the next chunk.
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    line_buf = ""   # parser path: text not yet emitted
    tele_buf = ""   # passthrough path: text not yet scanned for telemetry

    scan = _StreamScan()

    # Determine whether the client expects OpenAI or Anthropic format
    # If translation_mode is anthropic_to_openai, final format is Anthropic (client sent Anthropic)
    # If translation_mode is openai_to_anthropic, final format is OpenAI (client sent OpenAI)
    is_client_anthropic = "messages" in endpoint_uri or translation_mode == "anthropic_to_openai"
    is_client_openai = "chat/completions" in endpoint_uri or translation_mode == "openai_to_anthropic"

    # ── Output (streaming) guardrail state ──────────────────────────────────
    # ``output_guardrail`` is a sync callable(text) -> decision with .is_allowed.
    # On a block decision the gateway stops forwarding upstream text and emits
    # a client-format-aware replacement notice instead.
    from ..services.guardrails_service import OUTPUT_SCAN_CARRY as _GR_CARRY_LEN, GUARDRAIL_NOTICE
    gr_enabled = output_guardrail is not None
    # Line-oriented formats can be truncated safely mid-stream; a plain JSON
    # body cannot, so it is buffered whole and checked before any bytes go out.
    gr_stream_like = is_sse or ("ndjson" in content_type)
    gr_buffer_mode = gr_enabled and output_guardrail_action == "block" and not gr_stream_like
    gr_blocked = False
    gr_violation_seen = False
    _gr_frag_idx = 0
    _gr_think_idx = 0
    _gr_carry = ""
    gr_buffer: list[bytes] = []

    # ── Response (streaming) PII masking state ──────────────────────────────
    # ``pii_scrubber`` is a PIIScrubber (services.pii_service): feed(text) ->
    # safe-to-emit prefix, flush() -> final redacted tail. JSON-framed streams
    # are masked field-by-field (delta.content / delta.text etc.); the carry
    # window lives in the scrubber so PII split across deltas is still caught
    # (held text is prepended to the next field the client concatenates).
    pii_enabled = pii_scrubber is not None
    pii_stream_like = is_sse or ("ndjson" in content_type)
    # A whole-body block check (guardrail) already buffers the body; let it
    # own the hold and mask at release time instead of buffering twice.
    pii_inline = pii_enabled and pii_stream_like and not gr_buffer_mode
    pii_body_mode = pii_enabled and not pii_stream_like and not gr_buffer_mode
    pii_buffer: list[bytes] = []
    _pii_hold = bytearray()      # incomplete line bytes (inline mode)
    _pii_flushed = False         # flush delta already injected
    pii_notified = False

    def _mask_str(text: str, full: bool = False) -> str:
        if not text:
            return text
        try:
            if full:
                return pii_scrubber.scrub_full(text)
            return pii_scrubber.feed(text)
        except Exception:  # pragma: no cover - masking must not kill the stream
            return text

    def _mask_obj(obj, *, full: bool = False) -> bool:
        """Redact text-bearing fields of a decoded JSON event in place."""
        changed = False
        if not isinstance(obj, dict):
            return False
        for choice in (obj.get("choices") or []):
            if not isinstance(choice, dict):
                continue
            for holder_key in ("delta", "message"):
                holder = choice.get(holder_key)
                if not isinstance(holder, dict):
                    continue
                for field in ("content", "reasoning_content", "reasoning"):
                    v = holder.get(field)
                    if isinstance(v, str) and v:
                        new_v = _mask_str(v, full)
                        holder[field] = new_v
                        changed = changed or new_v != v
        # Anthropic text deltas
        delta = obj.get("delta")
        if isinstance(delta, dict):
            v = delta.get("text")
            if isinstance(v, str) and v:
                new_v = _mask_str(v, full)
                delta["text"] = new_v
                changed = changed or new_v != v
        # Anthropic content blocks (whole-body JSON / message_start)
        content = obj.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str) and block["text"]:
                    new_v = _mask_str(block["text"], full)
                    block["text"] = new_v
                    changed = changed or new_v != v
        return changed

    def _pii_flush_lines(is_anthropic: bool) -> bytes:
        """Emit held scrubber tail as one final delta before the stream closes."""
        nonlocal _pii_flushed
        if _pii_flushed:
            return b""
        try:
            tail = pii_scrubber.flush()
        except Exception:  # pragma: no cover
            return b""
        if not tail:
            return b""
        _pii_flushed = True
        if is_anthropic:
            evt = {"type": "content_block_delta", "index": 0,
                   "delta": {"type": "text_delta", "text": tail}}
            return f"data: {json.dumps(evt)}\n\n".encode("utf-8")
        evt = {"id": "pii-flush", "object": "chat.completion.chunk", "created": 0,
               "model": model,
               "choices": [{"index": 0, "delta": {"content": tail}, "finish_reason": None}]}
        return f"data: {json.dumps(evt)}\n\n".encode("utf-8")

    def _pii_mask_chunk(chunk_bytes: bytes) -> bytes:
        """Mask complete JSON lines inside a network chunk; hold the incomplete
        tail in _pii_hold so byte order and line integrity are preserved."""
        nonlocal _pii_hold
        _pii_hold += chunk_bytes
        out = bytearray()
        data = bytes(_pii_hold)
        while True:
            nl = data.find(b"\n")
            if nl < 0:
                break
            line, data = data[:nl], data[nl + 1:]
            out += _pii_mask_line(line)
        _pii_hold = bytearray(data)
        return bytes(out)

    def _pii_extract_text(obj) -> str:
        try:
            if isinstance(obj, dict):
                if obj.get("object") == "chat.completion" or "choices" in obj:
                    ch = (obj.get("choices") or [{}])[0]
                    msg = ch.get("message") or ch.get("delta") or {}
                    return msg.get("content") or ""
                blocks = obj.get("content")
                if isinstance(blocks, list):
                    return "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        except Exception:  # pragma: no cover
            pass
        return ""

    def _pii_write_text(obj, text: str) -> None:
        try:
            if isinstance(obj, dict):
                if obj.get("object") == "chat.completion" or "choices" in obj:
                    ch = (obj.get("choices") or [{}])[0]
                    msg = ch.get("message") or ch.get("delta")
                    if isinstance(msg, dict):
                        msg["content"] = text
                elif isinstance(obj.get("content"), list) and obj["content"]:
                    first = obj["content"][0]
                    if isinstance(first, dict):
                        first["text"] = text
        except Exception:  # pragma: no cover
            pass

    def _pii_is_terminal_line(obj) -> bool:
        """True for events that end the assistant text (safe flush points)."""
        if not isinstance(obj, dict):
            return False
        t = obj.get("type")
        if t in ("content_block_stop", "message_stop"):
            return True
        for ch in (obj.get("choices") or []):
            if isinstance(ch, dict) and ch.get("finish_reason"):
                return True
        return False

    def _pii_terminal_flush(obj) -> bytes:
        """Flush held scrubber text immediately before a terminal event so the
        client's concatenated message keeps the masked tail (the flush delta
        rides in the same text block / message the terminal event closes)."""
        nonlocal _pii_flushed
        if _pii_flushed:
            return b""
        try:
            tail = pii_scrubber.flush()
        except Exception:  # pragma: no cover
            return b""
        if not tail:
            return b""
        _pii_flushed = True
        if is_client_anthropic:
            evt = {"type": "content_block_delta", "index": 0,
                   "delta": {"type": "text_delta", "text": tail}}
        else:
            evt = {"id": "pii-flush", "object": "chat.completion.chunk", "created": 0,
                   "model": model,
                   "choices": [{"index": 0, "delta": {"content": tail}, "finish_reason": None}]}
        return f"data: {json.dumps(evt)}\n\n".encode("utf-8")

    def _pii_mask_line(line: bytes) -> bytes:
        """Mask one complete SSE/NDJSON line (JSON payload lines only)."""
        stripped = line.strip()
        if not stripped:
            return line + b"\n"
        payload = stripped
        prefix = b""
        if payload.lower().startswith(b"data:"):
            rest = payload[5:]
            lead = len(rest) - len(rest.lstrip(b" "))
            prefix, payload = payload[:5] + lead * b" ", rest.lstrip(b" ")
        if payload.upper() == b"[DONE]":
            return line + b"\n"
        if payload.startswith(b"{"):
            try:
                obj = json.loads(payload.decode("utf-8"))
            except Exception:
                return line + b"\n"
            terminal = _pii_is_terminal_line(obj)
            # For terminal whole-object events (Anthropic stops carry no text),
            # flush+mask via the full-text helpers on the *previous* content.
            changed = _mask_obj(obj)
            body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
            if terminal:
                return _pii_terminal_flush(obj) + prefix + body + b"\n"
            if changed:
                return prefix + body + b"\n"
            return line + b"\n"
        return line + b"\n"

    def _pii_mask_body(body_bytes: bytes) -> bytes:
        """Whole JSON body masking (non-stream responses routed through the
        streaming pipeline): mask text fields with full-text scrubbing."""
        try:
            obj = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            try:
                return pii_scrubber.scrub_full(
                    body_bytes.decode("utf-8", errors="replace")).encode("utf-8")
            except Exception:  # pragma: no cover
                return body_bytes
        text = _pii_extract_text(obj)
        if text:
            masked_text = _mask_str(text, full=True) + _flush_now()
            if masked_text != text:
                _pii_write_text(obj, masked_text)
        else:
            _mask_obj(obj, full=True)
        try:
            return json.dumps(obj).encode("utf-8")
        except Exception:  # pragma: no cover
            return body_bytes

    def _flush_now() -> str:
        nonlocal _pii_flushed
        if _pii_flushed:
            return ""
        _pii_flushed = True
        try:
            return pii_scrubber.flush()
        except Exception:  # pragma: no cover
            return ""

    async def _notify_pii() -> None:
        nonlocal pii_notified
        if pii_notified or not pii_enabled:
            return
        pii_notified = True
        total = getattr(pii_scrubber, "total_masked", 0) or 0
        if not total:
            return
        if span.is_recording():
            span.set_attribute("llm.pii_response_masked", total)
        if on_pii_mask is None:
            return
        try:
            cats = set(getattr(pii_scrubber, "categories_seen", set()) or set())
            if inspect.iscoroutinefunction(on_pii_mask):
                await on_pii_mask(total, cats)
            else:
                on_pii_mask(total, cats)
        except Exception as cb_err:  # pragma: no cover
            logger.warning("pii mask callback error: %s", cb_err)

    async def _notify_violation(decision) -> None:
        nonlocal gr_violation_seen
        if gr_violation_seen:
            return
        gr_violation_seen = True
        if span.is_recording():
            span.set_attribute("llm.guardrail_output_block", output_guardrail_action == "block")
        if on_output_violation is None:
            return
        try:
            if inspect.iscoroutinefunction(on_output_violation):
                await on_output_violation(decision)
            else:
                on_output_violation(decision)
        except Exception as cb_err:  # pragma: no cover - callback must not kill the stream
            logger.warning("output guardrail callback error: %s", cb_err)

    def _scrub_scanned_content() -> None:
        """On block, drop scanned text so it can't leak into captured traces."""
        scan.content_parts[:] = [GUARDRAIL_NOTICE]
        scan.thinking_parts.clear()

    def _guardrail_check() -> object | None:
        """Scan text appended since the last call. Returns a blocking decision or None.

        A 256-char carry window between scans catches secrets that arrive split
        across two network chunks. Scanner errors fail open (a broken scanner
        must not take down healthy streams).
        """
        nonlocal _gr_frag_idx, _gr_think_idx, _gr_carry
        if output_guardrail is None:
            return None
        try:
            if len(scan.content_parts) <= _gr_frag_idx and len(scan.thinking_parts) <= _gr_think_idx:
                return None
            new_text = ("".join(scan.content_parts[_gr_frag_idx:])
                        + "".join(scan.thinking_parts[_gr_think_idx:]))
            _gr_frag_idx = len(scan.content_parts)
            _gr_think_idx = len(scan.thinking_parts)
            text = _gr_carry + new_text
            if len(text) > _GR_CARRY_LEN:
                _gr_carry = text[-_GR_CARRY_LEN:]
            else:
                _gr_carry = text
            decision = output_guardrail(text)
            if decision is not None and not decision.is_allowed:
                return decision
        except Exception as gr_err:  # pragma: no cover - defensive
            logger.warning("output guardrail scan error: %s", gr_err)
        return None

    def _replacement_chunks() -> list[bytes]:
        """Client-format-aware replacement body emitted in place of blocked text."""
        out: list[bytes] = []
        if not gr_stream_like:
            # Whole buffered body is replaced by a single valid JSON response.
            if is_client_anthropic:
                body = {
                    "id": "gateway-guardrail", "type": "message", "role": "assistant",
                    "model": model, "content": [{"type": "text", "text": GUARDRAIL_NOTICE}],
                    "stop_reason": "stop", "usage": {"input_tokens": 0, "output_tokens": 0},
                }
            else:
                body = {
                    "id": "gateway-guardrail", "object": "chat.completion", "created": 0,
                    "model": model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": GUARDRAIL_NOTICE},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
            out.append(json.dumps(body).encode("utf-8"))
            return out
        if is_client_anthropic:
            events = [
                ("message_start", {"type": "message_start", "message": {
                    "id": "gateway_guardrail", "type": "message", "role": "assistant",
                    "model": model, "content": [], "usage": {"input_tokens": 0, "output_tokens": 0}}}),
                ("content_block_start", {"type": "content_block_start", "index": 0,
                                         "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": GUARDRAIL_NOTICE}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "stop"},
                                   "usage": {"output_tokens": 0}}),
                ("message_stop", {"type": "message_stop"}),
            ]
            out.append("".join(f"event: {t}\ndata: {json.dumps(d)}\n\n" for t, d in events).encode("utf-8"))
        else:
            first = {"id": "gateway-guardrail", "object": "chat.completion.chunk", "created": 0,
                     "model": model,
                     "choices": [{"index": 0, "delta": {"content": GUARDRAIL_NOTICE}, "finish_reason": None}]}
            last = {"id": "gateway-guardrail", "object": "chat.completion.chunk", "created": 0,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            out.append(f"data: {json.dumps(first)}\n\ndata: {json.dumps(last)}\n\ndata: [DONE]\n\n".encode("utf-8"))
        return out

    _last_live_report = 0.0

    def _report_live_tokens() -> None:
        """Feed tokens-so-far to the live store so tok/s updates mid-stream (throttled)."""
        nonlocal _last_live_report
        if tracker is None or tracker.finished:
            return
        now = time.monotonic()
        if now - _last_live_report < 0.25:
            return
        # Only stamp the throttle when there is something to report, so the first
        # real token isn't made to wait an extra 250 ms behind empty early chunks.
        if scan.usage_seen and scan.usage.completion_tokens > 0:
            _last_live_report = now
            tracker.progress(scan.usage.completion_tokens, total_deltas=scan.delta_count)
        elif scan.delta_count > 0:
            # No usage chunk yet: each content delta is roughly one token.
            _last_live_report = now
            tracker.progress(scan.delta_count, total_deltas=scan.delta_count)

    # Translate stream if mode is set
    response_stream = translate_response_stream(translation_mode, response)

    def _figures(success: bool, cancelled: bool):
        """Final usage and timing for metrics, the store and on_complete."""
        end_wall = time.time()
        usage = scan.usage
        estimated = False
        if not scan.usage_seen and is_sse is False and accumulated_chunks:
            raw = b"".join(accumulated_chunks)
            parsed = usage_from_body(raw)
            if parsed is not None and parsed.has_counts():
                usage = parsed
                scan.usage_seen = True
            else:
                # No usage in the body: estimate from the text, roughly 4 characters per token.
                text = _text_of_body(raw)
                n = max(1, len(text) // 4) if text else 0
                usage = Usage(completion_tokens=n, total_tokens=n, estimated=bool(text))
                estimated = bool(text)
        elif not scan.usage_seen or (usage.completion_tokens == 0 and scan.delta_count > 0):
            # Streaming without usage (e.g. OpenAI without include_usage or Anthropic without message_delta usage): count content deltas.
            est = estimate_completion(scan.delta_count)
            if usage.has_counts():
                usage.completion_tokens = est.completion_tokens
                usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
                usage.estimated = est.estimated
            else:
                usage = est
            estimated = est.estimated and est.completion_tokens > 0
        usage.finalize_total()
        if usage.estimated:
            estimated = True

        decode_s = None
        if usage.eval_duration_s:
            decode_s = usage.eval_duration_s
        elif first_token_wall is not None:
            decode_s = max(end_wall - first_token_wall, 0.0)
        tps = 0.0
        if usage.completion_tokens > 0 and decode_s and decode_s > 0:
            tps = usage.completion_tokens / decode_s
        return usage, estimated, decode_s, tps, end_wall

    def _record(success: bool, cancelled: bool, usage: Usage, estimated: bool,
                decode_s, tps: float, end_wall: float) -> None:
        duration = end_wall - start_time
        record_request_metrics(
            backend_url=backend_url, model=model, endpoint_uri=endpoint_uri, user_name=user_name,
            success=success, request_duration=duration,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            server=server, server_type=server_type or "",
            time_to_first_token=time_to_first_token or 0.0,
            generation_tps=tps,
        )
        err_type = None
        if not success:
            if resp_status == 400:
                err_type = "bad_request"
            elif resp_status == 429:
                err_type = "upstream_rate_limit"
            elif resp_status == 503:
                err_type = "upstream_overload"
            else:
                err_type = "upstream_failure"

        out_text = None
        try:
            from ..observability.capture import captures_content

            if captures_content():
                if scan.content_parts:
                    out_text = "".join(scan.content_parts)[:65536]
                elif not is_sse and accumulated_chunks:
                    out_text = _text_of_body(b"".join(accumulated_chunks))[:65536] or None
        except Exception:  # pragma: no cover - capture must never break streaming
            out_text = None
        track_done(
            tracker, success=success, cancelled=cancelled,
            output_tokens=usage.completion_tokens,
            prompt_tokens=usage.prompt_tokens,
            total_tokens=usage.total_tokens,
            estimated=estimated, decode_seconds=decode_s,
            status_code=resp_status,
            error_type=err_type,
            delta_events=scan.delta_count,
            output_text=out_text,
        )
        set_llm_attributes(
            span,
            model=model,
            response_model=model,
            server_name=server,
            server_type=server_type,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        )

    try:
        _takes_cancelled = bool(on_complete) and "cancelled" in inspect.signature(on_complete).parameters
    except (TypeError, ValueError):
        _takes_cancelled = False

    async def _run_on_complete(usage: Usage, cancelled: bool = False) -> None:
        if not on_complete:
            return
        try:
            p_tok = usage.prompt_tokens
            c_tok = usage.completion_tokens
            # ``cancelled`` is passed only to callbacks that declare it, so older ones keep working.
            extra = {"cancelled": cancelled} if _takes_cancelled else {}
            if inspect.iscoroutinefunction(on_complete):
                await on_complete(p_tok, c_tok, accumulated_chunks, **extra)
            else:
                on_complete(p_tok, c_tok, accumulated_chunks, **extra)
        except Exception as cb_err:
            logger.warning("on_complete callback error: %s", cb_err)

    try:
        async for chunk in response_stream:
            if time_to_first_token is None:
                first_token_wall = time.time()
                time_to_first_token = first_token_wall - start_time
                track_first_token(tracker)
                if span.is_recording():
                    span.set_attribute("llm.time_to_first_token", time_to_first_token)

            accumulated_bytes += len(chunk)
            if tracker is not None:
                tracker.set_inflight_bytes(accumulated_bytes)

            decoded = decoder.decode(chunk)
            _report_live_tokens()

            # If normalize_thinking is active, we parse and intercept standard/thinking blocks
            if parser:
                parsed_chunks = []
                lines = (line_buf + decoded).split('\n')
                line_buf = lines.pop()
                for line in lines:
                    # Usage and delta counting only; the parser below owns the text.
                    scan.line(line, content=False)
                    if line.startswith('data: '):
                        json_str = line[6:].strip()
                        if json_str == '[DONE]':
                            parsed_chunks.append(line.encode('utf-8') + b'\n')
                            continue

                        try:
                            chunk_data = json.loads(json_str)

                            # OpenAI format tag normalization
                            if is_client_openai and 'choices' in chunk_data and chunk_data['choices']:
                                choice = chunk_data['choices'][0]
                                if 'delta' in choice:
                                    delta = choice['delta']
                                    content = delta.get('content', '')
                                    reasoning = delta.get('reasoning_content', '')

                                    # If reasoning is already structured by upstream
                                    if reasoning:
                                        scan.thinking_parts.append(reasoning)
                                        parsed_chunks.append(line.encode('utf-8') + b'\n')
                                        continue

                                    if content:
                                        think_delta, text_delta = parser.feed(content)
                                        if think_delta:
                                            scan.thinking_parts.append(think_delta)
                                        if text_delta:
                                            scan.content_parts.append(text_delta)

                                        # Yield rewritten chunk with separate fields
                                        delta_rewritten = {}
                                        if think_delta:
                                            delta_rewritten["reasoning_content"] = think_delta
                                        if text_delta:
                                            delta_rewritten["content"] = text_delta

                                        choice['delta'] = delta_rewritten
                                        rewritten_line = f"data: {json.dumps(chunk_data)}\n"
                                        parsed_chunks.append(rewritten_line.encode('utf-8'))
                                    else:
                                        parsed_chunks.append(line.encode('utf-8') + b'\n')
                                else:
                                    parsed_chunks.append(line.encode('utf-8') + b'\n')

                            # Anthropic format tag normalization
                            elif is_client_anthropic:
                                # Look for content block delta
                                if 'delta' in chunk_data:
                                    delta = chunk_data['delta']
                                    delta_type = delta.get('type')

                                    if delta_type == 'text_delta':
                                        text = delta.get('text', '')
                                        think_delta, text_delta = parser.feed(text)
                                        if think_delta:
                                            scan.thinking_parts.append(think_delta)
                                        if text_delta:
                                            scan.content_parts.append(text_delta)

                                        if text_delta:
                                            delta['text'] = text_delta
                                            rewritten_line = f"data: {json.dumps(chunk_data)}\n"
                                            parsed_chunks.append(rewritten_line.encode('utf-8'))
                                        else:
                                            # Skip emitting standard content chunk if it was entirely thinking
                                            pass
                                    else:
                                        parsed_chunks.append(line.encode('utf-8') + b'\n')
                                else:
                                    parsed_chunks.append(line.encode('utf-8') + b'\n')
                            else:
                                parsed_chunks.append(line.encode('utf-8') + b'\n')
                        except json.JSONDecodeError:
                            parsed_chunks.append(line.encode('utf-8') + b'\n')
                    else:
                        parsed_chunks.append(line.encode('utf-8') + b'\n')

                # Yield rewritten or filtered chunks
                rewritten_chunk = b"".join(parsed_chunks)
                if rewritten_chunk and pii_inline:
                    rewritten_chunk = _pii_mask_chunk(rewritten_chunk)
                if rewritten_chunk:
                    if gr_enabled:
                        _gr_decision = _guardrail_check()
                        if _gr_decision is not None:
                            await _notify_violation(_gr_decision)
                            if output_guardrail_action == "block":
                                gr_blocked = True
                                _scrub_scanned_content()
                                accumulated_chunks.clear()
                                _gr_repl = _replacement_chunks()
                                accumulated_chunks.extend(_gr_repl)
                                for _c in _gr_repl:
                                    yield _c
                                break
                    accumulated_chunks.append(rewritten_chunk)
                    yield rewritten_chunk
                continue

            # Standard path (no thinking tag normalization)
            if not gr_buffer_mode and not pii_body_mode and not pii_inline:
                accumulated_chunks.append(chunk)

            # Parse SSE / NDJSON events for usage and content (never fails the stream)
            try:
                lines = (tele_buf + decoded).split('\n')
                tele_buf = lines.pop()
                for line in lines:
                    scan.line(line, content=True)
            except Exception:
                pass

            if gr_enabled and not gr_blocked:
                _gr_decision = _guardrail_check()
                if _gr_decision is not None:
                    await _notify_violation(_gr_decision)
                    if output_guardrail_action == "block":
                        gr_blocked = True
                        _scrub_scanned_content()
                        accumulated_chunks.clear()
                        gr_buffer.clear()
                        _gr_repl = _replacement_chunks()
                        accumulated_chunks.extend(_gr_repl)
                        for _c in _gr_repl:
                            yield _c
                        break

            if gr_buffer_mode:
                # Whole-body mode: hold bytes until the complete body has been
                # scanned (a truncated JSON body would be unparseable for the client).
                gr_buffer.append(chunk)
            elif pii_body_mode:
                pii_buffer.append(chunk)
            elif pii_inline:
                # Masked bytes are what the client sees *and* what downstream
                # consumers (cache store, on_complete) receive — raw PII must
                # never enter the response cache.
                masked = _pii_mask_chunk(chunk)
                if masked:
                    accumulated_chunks.append(masked)
                    yield masked
            else:
                yield chunk

        # Emit whatever the parser path still holds (a final line with no
        # trailing newline) unchanged.
        if parser and not gr_blocked:
            line_buf += decoder.decode(b"", final=True)
            if line_buf:
                scan.line(line_buf, content=False)
                tail = line_buf.encode("utf-8")
                if pii_inline:
                    tail = _pii_mask_line(tail.rstrip(b"\n"))
                accumulated_chunks.append(tail)
                yield tail
        elif not gr_blocked:
            # Last event may arrive without a trailing newline (Ollama NDJSON, bare JSON)
            tele_buf += decoder.decode(b"", final=True)
            if tele_buf.strip():
                try:
                    scan.line(tele_buf, content=True)
                except Exception:
                    pass

        # Flush any remaining buffer from thinking tag parser
        if parser and parser.buffer and not gr_blocked:
            think_delta, text_delta = parser.feed("")
            if think_delta:
                scan.thinking_parts.append(think_delta)
            if text_delta:
                scan.content_parts.append(text_delta)

        if gr_enabled and not gr_blocked:
            if gr_buffer_mode:
                # Whole body collected: scan the decoded bytes directly (a bare
                # JSON body is not parsed line-by-line) and release or redact.
                try:
                    _gr_text = b"".join(gr_buffer).decode("utf-8", errors="replace")
                    _gr_decision = output_guardrail(_gr_text)
                except Exception as gr_err:  # pragma: no cover - defensive
                    logger.warning("output guardrail body scan error: %s", gr_err)
                    _gr_decision = None
                if _gr_decision is not None and not _gr_decision.is_allowed:
                    await _notify_violation(_gr_decision)
                    gr_blocked = True
                    _scrub_scanned_content()
                    accumulated_chunks.clear()
                    gr_buffer.clear()
                    _gr_repl = _replacement_chunks()
                    accumulated_chunks.extend(_gr_repl)
                    for _c in _gr_repl:
                        yield _c
                elif gr_buffer and pii_enabled:
                    _body = b"".join(gr_buffer)
                    _body = _pii_mask_body(_body)
                    gr_buffer.clear()
                    accumulated_chunks.append(_body)
                    yield _body
                else:
                    accumulated_chunks.extend(gr_buffer)
                    for _c in gr_buffer:
                        yield _c
                    gr_buffer.clear()
            else:
                # Final scan of any tail the incremental scans haven't evaluated.
                _gr_decision = _guardrail_check()
                if _gr_decision is not None:
                    await _notify_violation(_gr_decision)

        # Release anything the PII scrubber still holds (stream ended without
        # a terminal [DONE]/message_stop event, or a trailing partial line).
        if pii_enabled and not gr_blocked:
            if pii_inline and (_pii_hold or not _pii_flushed):
                _tail_bytes = (_pii_mask_line(bytes(_pii_hold)) if _pii_hold else b"") \
                    + _pii_flush_lines(is_client_anthropic)
                if _tail_bytes:
                    accumulated_chunks.append(_tail_bytes)
                    yield _tail_bytes
            elif pii_body_mode and pii_buffer:
                _body = _pii_mask_body(b"".join(pii_buffer))
                pii_buffer.clear()
                accumulated_chunks.append(_body)
                yield _body
            await _notify_pii()

        # Stream finished successfully
        usage, estimated, decode_s, tps, end_wall = _figures(True, False)
        finalized = True
        _record(upstream_ok, False, usage, estimated, decode_s, tps, end_wall)
        await _run_on_complete(usage)

    except (asyncio.CancelledError, GeneratorExit):
        # Client disconnected or the task was cancelled: still account for the work done.
        if not finalized:
            finalized = True
            usage, estimated, decode_s, tps, end_wall = _figures(False, True)
            _record(False, True, usage, estimated, decode_s, tps, end_wall)
            try:
                await _run_on_complete(usage, cancelled=True)
            except BaseException:
                pass
        raise

    except Exception as e:
        has_error = True
        if not finalized:
            finalized = True
            usage, estimated, decode_s, tps, end_wall = _figures(False, False)
            _record(False, False, usage, estimated, decode_s, tps, end_wall)
        if span.is_recording():
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
        raise e
    finally:
        # Set OpenInference output attributes regardless of completion status
        if span.is_recording():
            if not upstream_ok:
                span.set_status(trace.Status(trace.StatusCode.ERROR, f"HTTP {resp_status}"))
            elif not has_error:
                span.set_status(trace.Status(trace.StatusCode.OK))

            # Set output messages as JSON array (OpenInference format)
            if scan.content_parts:
                full_content = ''.join(scan.content_parts)
                output_messages = [{"role": "assistant", "content": full_content[:10000]}]

                # Append thinking content if captured
                if scan.thinking_parts:
                    full_thinking = ''.join(scan.thinking_parts)
                    span.set_attribute("llm.reasoning_content", full_thinking[:10000])
                    output_messages[0]["reasoning_content"] = full_thinking[:10000]

                span.set_attribute("llm.output_messages", json.dumps(output_messages))
                span.set_attribute("output.value", full_content[:10000])
                span.set_attribute("output.mime_type", "text/plain")
            else:
                # Fallback: try to parse from raw response
                try:
                    full_body = b"".join(accumulated_chunks).decode('utf-8', errors='replace')
                    try:
                        response_json = json.loads(full_body)
                        if 'message' in response_json:
                            msg = response_json['message']
                            output_messages = [{"role": msg.get('role', 'assistant'), "content": msg.get('content', '')[:10000]}]
                            span.set_attribute("llm.output_messages", json.dumps(output_messages))
                            span.set_attribute("output.value", msg.get('content', '')[:10000])
                            span.set_attribute("output.mime_type", "text/plain")
                        elif 'choices' in response_json and response_json['choices']:
                            msg = response_json['choices'][0].get('message', {})
                            output_messages = [{"role": msg.get('role', 'assistant'), "content": msg.get('content', '')[:10000]}]
                            span.set_attribute("llm.output_messages", json.dumps(output_messages))
                            span.set_attribute("output.value", msg.get('content', '')[:10000])
                            span.set_attribute("output.mime_type", "text/plain")
                    except (json.JSONDecodeError, KeyError, TypeError):
                        span.set_attribute("output.value", full_body[:10000])
                        span.set_attribute("output.mime_type", "application/json")
                except Exception:
                    span.set_attribute("output.value", "<binary or decode error>")
                    span.set_attribute("output.mime_type", "text/plain")

        span.end()
        try:
            await response.aclose()
        except Exception:
            pass
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
