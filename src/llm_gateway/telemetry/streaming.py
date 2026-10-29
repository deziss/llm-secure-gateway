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
def flatten_attributes(data: Dict, prefix: str = "llm.input_messages") -> Dict[str, str]:
    """
    Flatten a list of message dictionaries into OpenTelemetry attributes.
    Follows OpenInference semantic conventions.
    """
    attributes = {}
    if not isinstance(data, list):
        return attributes

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            attr_key = f"{prefix}.{i}.{key}"
            if isinstance(value, (str, int, float, bool)):
                attributes[attr_key] = str(value)
            elif value is None:
                attributes[attr_key] = ""
            else:
                attributes[attr_key] = json.dumps(value)
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
            raw_thinking = delta.get("reasoning_content")
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
        upstream_ok = int(getattr(response, "status_code", 200)) < 400
    except (TypeError, ValueError):
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
        elif not scan.usage_seen:
            # Streaming without usage (e.g. OpenAI without include_usage): count content deltas.
            est = estimate_completion(scan.delta_count)
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
        track_done(
            tracker, success=success, cancelled=cancelled,
            output_tokens=usage.completion_tokens, estimated=estimated, decode_seconds=decode_s,
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
                if rewritten_chunk:
                    accumulated_chunks.append(rewritten_chunk)
                    yield rewritten_chunk
                continue

            # Standard path (no thinking tag normalization)
            accumulated_chunks.append(chunk)

            # Parse SSE / NDJSON events for usage and content (never fails the stream)
            try:
                lines = (tele_buf + decoded).split('\n')
                tele_buf = lines.pop()
                for line in lines:
                    scan.line(line, content=True)
            except Exception:
                pass

            yield chunk

        # Emit whatever the parser path still holds (a final line with no
        # trailing newline) unchanged.
        if parser:
            line_buf += decoder.decode(b"", final=True)
            if line_buf:
                tail = line_buf.encode("utf-8")
                scan.line(line_buf, content=False)
                accumulated_chunks.append(tail)
                yield tail
        else:
            # Last event may arrive without a trailing newline (Ollama NDJSON, bare JSON)
            tele_buf += decoder.decode(b"", final=True)
            if tele_buf.strip():
                try:
                    scan.line(tele_buf, content=True)
                except Exception:
                    pass

        # Flush any remaining buffer from thinking tag parser
        if parser and parser.buffer:
            think_delta, text_delta = parser.feed("")
            if think_delta:
                scan.thinking_parts.append(think_delta)
            if text_delta:
                scan.content_parts.append(text_delta)

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
            if not has_error:
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
                        elif 'choices' in response_json and response_json['choices']:
                            msg = response_json['choices'][0].get('message', {})
                            output_messages = [{"role": msg.get('role', 'assistant'), "content": msg.get('content', '')[:10000]}]
                            span.set_attribute("llm.output_messages", json.dumps(output_messages))
                            span.set_attribute("output.value", msg.get('content', '')[:10000])
                    except (json.JSONDecodeError, KeyError, TypeError):
                        span.set_attribute("output.value", full_body[:10000])
                        span.set_attribute("output.mime_type", "application/json")
                except Exception:
                    span.set_attribute("output.value", "<binary or decode error>")

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
