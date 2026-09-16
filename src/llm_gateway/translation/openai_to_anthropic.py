import json
import logging
import time
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

def translate_request(body: dict) -> dict:
    """Translate OpenAI Chat Completions request to Anthropic Messages request."""
    anthropic_body = {}
    
    # Map basic fields
    anthropic_body["model"] = body.get("model", "claude-3-5-sonnet-20241022")
    anthropic_body["stream"] = body.get("stream", False)
    
    # max_tokens is REQUIRED in Anthropic, default to 4096 if not provided
    anthropic_body["max_tokens"] = body.get("max_tokens") or body.get("max_completion_tokens") or 4096
    
    if "temperature" in body:
        anthropic_body["temperature"] = body["temperature"]
        
    # Extract system prompt and separate messages
    messages = []
    system_parts = []
    
    for msg in body.get("messages", []):
        role = msg.get("role")
        content = msg.get("content")
        
        if role == "system":
            system_parts.append(str(content))
        else:
            # Map user/assistant content.
            # Anthropic messages role must be user or assistant only.
            mapped_role = "user" if role not in ("assistant", "user") else role
            
            # Map OpenAI content blocks if user sent multi-modal content
            if isinstance(content, list):
                parts = []
                for item in content:
                    item_type = item.get("type")
                    if item_type == "text":
                        parts.append({"type": "text", "text": item.get("text", "")})
                    elif item_type == "image_url":
                        url = item.get("image_url", {}).get("url", "")
                        if url.startswith("data:image/"):
                            try:
                                header, base64_data = url.split(";base64,")
                                media_type = header.replace("data:", "")
                                parts.append({
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": media_type,
                                        "data": base64_data
                                    }
                                })
                            except Exception:
                                pass # Skip invalid image urls
                messages.append({"role": mapped_role, "content": parts if parts else ""})
            else:
                messages.append({"role": mapped_role, "content": content})
                
    if system_parts:
        anthropic_body["system"] = "\n".join(system_parts)
        
    anthropic_body["messages"] = messages
    
    # Map tools if specified
    if "tools" in body:
        tools = []
        for t in body["tools"]:
            if t.get("type") == "function":
                func = t.get("function", {})
                tools.append({
                    "name": func.get("name"),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}})
                })
        if tools:
            anthropic_body["tools"] = tools
            
        # Tool choice
        tool_choice = body.get("tool_choice")
        if tool_choice:
            if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                anthropic_body["tool_choice"] = {
                    "type": "tool",
                    "name": tool_choice.get("function", {}).get("name")
                }
            elif tool_choice == "required":
                anthropic_body["tool_choice"] = {"type": "any"}
            elif tool_choice == "auto":
                anthropic_body["tool_choice"] = {"type": "auto"}
                
    return anthropic_body


async def translate_stream(raw_response) -> AsyncGenerator[bytes, None]:
    """Translate Anthropic stream events to OpenAI chunk format."""
    chat_id = "chatcmpl-trans-" + json.dumps(hash(raw_response))[:8]
    created_time = int(time.time())
    model_name = "translated-claude"
    
    async for chunk in raw_response.aiter_raw():
        decoded = chunk.decode("utf-8", errors="ignore")
        
        # Anthropic streams are formatted as SSE:
        # event: event_type
        # data: {...}
        #
        # Let's accumulate and parse them line-by-line
        current_event = None
        for line in decoded.split("\n"):
            line = line.strip()
            if line.startswith("event: "):
                current_event = line[7:].strip()
            elif line.startswith("data: ") and current_event:
                data_str = line[6:].strip()
                try:
                    data = json.loads(data_str)
                    
                    if current_event == "message_start":
                        msg = data.get("message", {})
                        model_name = msg.get("model", model_name)
                        
                    elif current_event == "content_block_delta":
                        delta = data.get("delta", {})
                        delta_type = delta.get("type")
                        
                        # Standard text delta
                        if delta_type == "text_delta":
                            text = delta.get("text", "")
                            openai_chunk = {
                                "id": chat_id,
                                "object": "chat.completion.chunk",
                                "created": created_time,
                                "model": model_name,
                                "choices": [{
                                    "index": 0,
                                    "delta": {"content": text},
                                    "finish_reason": None
                                }]
                            }
                            yield f"data: {json.dumps(openai_chunk)}\n\n".encode("utf-8")
                            
                        # Tool call delta
                        elif delta_type == "input_json_delta":
                            partial_json = delta.get("partial_json", "")
                            openai_chunk = {
                                "id": chat_id,
                                "object": "chat.completion.chunk",
                                "created": created_time,
                                "model": model_name,
                                "choices": [{
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [{
                                            "index": 0,
                                            "function": {"arguments": partial_json}
                                        }]
                                    },
                                    "finish_reason": None
                                }]
                            }
                            yield f"data: {json.dumps(openai_chunk)}\n\n".encode("utf-8")
                            
                    elif current_event == "content_block_start":
                        block = data.get("content_block", {})
                        if block.get("type") == "tool_use":
                            openai_chunk = {
                                "id": chat_id,
                                "object": "chat.completion.chunk",
                                "created": created_time,
                                "model": model_name,
                                "choices": [{
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [{
                                            "index": 0,
                                            "id": block.get("id"),
                                            "type": "function",
                                            "function": {
                                                "name": block.get("name"),
                                                "arguments": ""
                                            }
                                        }]
                                    },
                                    "finish_reason": None
                                }]
                            }
                            yield f"data: {json.dumps(openai_chunk)}\n\n".encode("utf-8")
                            
                    elif current_event == "message_delta":
                        # End of message, yield finish reason
                        delta = data.get("delta", {})
                        stop_reason = delta.get("stop_reason")
                        finish_reason = "stop"
                        if stop_reason == "tool_use":
                            finish_reason = "tool_calls"
                            
                        usage = data.get("usage", {})
                        prompt_tokens = usage.get("input_tokens", 0)
                        completion_tokens = usage.get("output_tokens", 0)
                        
                        openai_chunk = {
                            "id": chat_id,
                            "object": "chat.completion.chunk",
                            "created": created_time,
                            "model": model_name,
                            "choices": [{
                                "index": 0,
                                "delta": {},
                                "finish_reason": finish_reason
                            }],
                            "usage": {
                                "prompt_tokens": prompt_tokens,
                                "completion_tokens": completion_tokens,
                                "total_tokens": prompt_tokens + completion_tokens
                            }
                        }
                        yield f"data: {json.dumps(openai_chunk)}\n\n".encode("utf-8")
                        
                except json.JSONDecodeError:
                    pass
                
    yield b"data: [DONE]\n\n"
