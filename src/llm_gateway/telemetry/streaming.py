import time
import json
import logging
from typing import AsyncGenerator, Dict, Optional

from opentelemetry import trace
from .metrics import record_request_metrics
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
) -> AsyncGenerator[bytes, None]:
    """
    Async generator to wrap a streaming response, translate format if configured,
    optionally normalize/strip reasoning tags, record telemetry metrics, and end the span cleanly.
    """
    accumulated_chunks = []
    response_content_parts = []  # For standard content
    response_thinking_parts = [] # For thinking/reasoning content
    token_usage = {}  # To capture token counts
    time_to_first_token = None
    has_error = False

    # Initialize thinking parser if enabled
    parser = ThinkTagParser() if normalize_thinking else None

    # Determine whether the client expects OpenAI or Anthropic format
    # If translation_mode is anthropic_to_openai, final format is Anthropic (client sent Anthropic)
    # If translation_mode is openai_to_anthropic, final format is OpenAI (client sent OpenAI)
    is_client_anthropic = "messages" in endpoint_uri or translation_mode == "anthropic_to_openai"
    is_client_openai = "chat/completions" in endpoint_uri or translation_mode == "openai_to_anthropic"

    # Translate stream if mode is set
    response_stream = translate_response_stream(translation_mode, response)

    try:
        async for chunk in response_stream:
            if time_to_first_token is None:
                time_to_first_token = time.time() - start_time
                if span.is_recording():
                    span.set_attribute("llm.time_to_first_token", time_to_first_token)

            decoded = chunk.decode('utf-8', errors='ignore')

            # If normalize_thinking is active, we parse and intercept standard/thinking blocks
            if parser:
                parsed_chunks = []
                for line in decoded.split('\n'):
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
                                        response_thinking_parts.append(reasoning)
                                        parsed_chunks.append(line.encode('utf-8') + b'\n')
                                        continue
                                        
                                    if content:
                                        think_delta, text_delta = parser.feed(content)
                                        if think_delta:
                                            response_thinking_parts.append(think_delta)
                                        if text_delta:
                                            response_content_parts.append(text_delta)
                                            
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
                                            response_thinking_parts.append(think_delta)
                                        if text_delta:
                                            response_content_parts.append(text_delta)
                                            
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

            # Try to parse SSE data to extract telemetry content and metadata
            try:
                for line in decoded.split('\n'):
                    if line.startswith('data: '):
                        json_str = line[6:].strip()

                        if json_str == '[DONE]':
                            continue

                        if json_str:
                            try:
                                chunk_data = json.loads(json_str)

                                # OpenAI format telemetry extraction
                                if 'choices' in chunk_data and chunk_data['choices']:
                                    choice = chunk_data['choices'][0]
                                    if 'delta' in choice:
                                        delta = choice['delta']
                                        content = delta.get('content', '')
                                        reasoning = delta.get('reasoning_content', '')
                                        if content:
                                            response_content_parts.append(content)
                                        if reasoning:
                                            response_thinking_parts.append(reasoning)

                                # Anthropic format telemetry extraction
                                elif 'delta' in chunk_data:
                                    delta = chunk_data['delta']
                                    if delta.get('type') == 'text_delta':
                                        content = delta.get('text', '')
                                        if content:
                                            response_content_parts.append(content)

                                if 'usage' in chunk_data:
                                    token_usage = chunk_data['usage']

                            except json.JSONDecodeError:
                                pass
            except Exception:
                pass

            yield chunk

        # Flush any remaining buffer from thinking tag parser
        if parser and parser.buffer:
            think_delta, text_delta = parser.feed("")
            if think_delta:
                response_thinking_parts.append(think_delta)
            if text_delta:
                response_content_parts.append(text_delta)

        # Stream finished successfully
        duration = time.time() - start_time

        record_request_metrics(
             backend_url=backend_url, model=model, endpoint_uri=endpoint_uri, user_name=user_name,
             success=True, request_duration=duration,
             prompt_tokens=token_usage.get('prompt_tokens', 0),
             completion_tokens=token_usage.get('completion_tokens', 0),
             total_tokens=token_usage.get('total_tokens', 0)
        )

        if on_complete:
            try:
                p_tok = token_usage.get('prompt_tokens', 0)
                c_tok = token_usage.get('completion_tokens', 0)
                if p_tok == 0 and c_tok == 0 and accumulated_chunks:
                    try:
                        raw_joined = b"".join(accumulated_chunks).decode('utf-8', errors='ignore')
                        body_j = json.loads(raw_joined)
                        u = body_j.get("usage", {})
                        p_tok = u.get("prompt_tokens", 0)
                        c_tok = u.get("completion_tokens", 0)
                    except Exception:
                        pass
                import asyncio
                if asyncio.iscoroutinefunction(on_complete):
                    await on_complete(p_tok, c_tok, accumulated_chunks)
                else:
                    on_complete(p_tok, c_tok, accumulated_chunks)
            except Exception as cb_err:
                logger.warning("on_complete callback error: %s", cb_err)

    except Exception as e:
        has_error = True
        duration = time.time() - start_time
        if span.is_recording():
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))

        record_request_metrics(
             backend_url=backend_url, model=model, endpoint_uri=endpoint_uri, user_name=user_name,
             success=False, request_duration=duration
        )
        raise e
    finally:
        # Set OpenInference output attributes regardless of completion status
        if span.is_recording():
            if not has_error:
                 span.set_status(trace.Status(trace.StatusCode.OK))

            # Set output messages as JSON array (OpenInference format)
            if response_content_parts:
                full_content = ''.join(response_content_parts)
                output_messages = [{"role": "assistant", "content": full_content[:10000]}]
                
                # Append thinking content if captured
                if response_thinking_parts:
                    full_thinking = ''.join(response_thinking_parts)
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

            # Set token usage attributes
            if token_usage:
                if 'prompt_tokens' in token_usage:
                    span.set_attribute("llm.token_count.prompt", token_usage['prompt_tokens'])
                if 'completion_tokens' in token_usage:
                    span.set_attribute("llm.token_count.completion", token_usage['completion_tokens'])
                if 'total_tokens' in token_usage:
                    span.set_attribute("llm.token_count.total", token_usage['total_tokens'])

        span.end()
        try:
            await response.aclose()
        except Exception:
            pass
        if client is not None:
            await client.aclose()
