import time
import json
import logging
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

def translate_request(body: dict) -> dict:
    """Translate Anthropic Messages request to OpenAI Chat Completions request."""
    openai_body = {}
    
    # Map basic fields
    openai_body["model"] = body.get("model", "gpt-4o")
    openai_body["stream"] = body.get("stream", False)
    
    if "temperature" in body:
        openai_body["temperature"] = body["temperature"]
    if "max_tokens" in body:
        openai_body["max_tokens"] = body["max_tokens"]
        
    # Map messages and prepended system prompt
    messages = []
    system_prompt = body.get("system")
    if system_prompt:
        if isinstance(system_prompt, list):
            # Handle list of content blocks for system prompt
            text_parts = [part.get("text", "") for part in system_prompt if part.get("type") == "text"]
            system_text = "\n".join(text_parts)
            if system_text:
                messages.append({"role": "system", "content": system_text})
        else:
            messages.append({"role": "system", "content": str(system_prompt)})
            
    # Copy messages list and map structures
    for msg in body.get("messages", []):
        role = msg.get("role")
        content = msg.get("content")
        
        # Anthropic content can be a string or a list of blocks
        if isinstance(content, list):
            parts = []
            for block in content:
                if block.get("type") == "text":
                    parts.append({"type": "text", "text": block.get("text", "")})
                elif block.get("type") == "image":
                    # Translate image block if present
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        media_type = source.get("media_type", "image/jpeg")
                        data = source.get("data", "")
                        parts.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{data}"
                            }
                        })
            messages.append({"role": role, "content": parts if parts else ""})
        else:
            messages.append({"role": role, "content": content})
            
    openai_body["messages"] = messages
    
    # Map tools if specified
    if "tools" in body:
        tools = []
        for tool in body["tools"]:
            tools.append({
                "type": "function",
                "function": {
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}})
                }
            })
        if tools:
            openai_body["tools"] = tools
            
        # Tool choice
        tool_choice = body.get("tool_choice")
        if tool_choice:
            choice_type = tool_choice.get("type")
            if choice_type == "auto":
                openai_body["tool_choice"] = "auto"
            elif choice_type == "any":
                openai_body["tool_choice"] = "required"
            elif choice_type == "tool":
                openai_body["tool_choice"] = {
                    "type": "function",
                    "function": {"name": tool_choice.get("name")}
                }
                
    return openai_body


async def translate_stream(raw_response) -> AsyncGenerator[bytes, None]:
    """Translate an OpenAI streaming response to Anthropic Message stream format."""
    message_id = "msg_trans_" + json.dumps(hash(raw_response))[:8]
    model_name = "translated-model"
    
    # Send message start event first
    start_event = {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model_name,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
    }
    yield f"event: message_start\ndata: {json.dumps(start_event)}\n\n".encode("utf-8")
    
    # Send content block start
    block_start_event = {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""}
    }
    yield f"event: content_block_start\ndata: {json.dumps(block_start_event)}\n\n".encode("utf-8")
    
    prompt_tokens = 0
    completion_tokens = 0
    
    async for chunk in raw_response.aiter_raw():
        decoded = chunk.decode("utf-8", errors="ignore")
        for line in decoded.split("\n"):
            line = line.strip()
            if not line.startswith("data: "):
                continue
            
            json_str = line[6:].strip()
            if json_str == "[DONE]":
                continue
            
            try:
                data = json.loads(json_str)
                if "model" in data:
                    model_name = data["model"]
                    
                # Capture usage
                if "usage" in data and data["usage"]:
                    prompt_tokens = data["usage"].get("prompt_tokens", prompt_tokens)
                    completion_tokens = data["usage"].get("completion_tokens", completion_tokens)
                
                choices = data.get("choices", [])
                if not choices:
                    continue
                
                choice = choices[0]
                delta = choice.get("delta", {})
                
                # Check for standard content stream
                if "content" in delta and delta["content"]:
                    content_delta = {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": delta["content"]}
                    }
                    yield f"event: content_block_delta\ndata: {json.dumps(content_delta)}\n\n".encode("utf-8")
                
                # Check for tool call stream
                elif "tool_calls" in delta and delta["tool_calls"]:
                    tool_call = delta["tool_calls"][0]
                    # We can normalize tool use stream chunks
                    func = tool_call.get("function", {})
                    name = func.get("name")
                    args = func.get("arguments", "")
                    
                    if name:
                        # Start of a tool use block
                        tool_start = {
                            "type": "content_block_start",
                            "index": 1,
                            "content_block": {
                                "type": "tool_use",
                                "id": tool_call.get("id", "tool_call_0"),
                                "name": name,
                                "input": {}
                            }
                        }
                        yield f"event: content_block_start\ndata: {json.dumps(tool_start)}\n\n".encode("utf-8")
                        
                    if args:
                        tool_delta = {
                            "type": "content_block_delta",
                            "index": 1,
                            "delta": {"type": "input_json_delta", "partial_json": args}
                        }
                        yield f"event: content_block_delta\ndata: {json.dumps(tool_delta)}\n\n".encode("utf-8")
                        
            except json.JSONDecodeError:
                pass
                
    # Send content block stop
    block_stop_event = {
        "type": "content_block_stop",
        "index": 0
    }
    yield f"event: content_block_stop\ndata: {json.dumps(block_stop_event)}\n\n".encode("utf-8")
    
    # Send message delta
    msg_delta_event = {
        "type": "message_delta",
        "delta": {
            "stop_reason": "end_turn",
            "stop_sequence": None
        },
        "usage": {
            "output_tokens": completion_tokens
        }
    }
    yield f"event: message_delta\ndata: {json.dumps(msg_delta_event)}\n\n".encode("utf-8")
    
    # Send message stop
    stop_event = {
        "type": "message_stop"
    }
    yield f"event: message_stop\ndata: {json.dumps(stop_event)}\n\n".encode("utf-8")


def translate_response(openai_resp: dict) -> dict:
    """Translate OpenAI Chat Completion JSON response to Anthropic Message response."""
    choice = openai_resp.get("choices", [{}])[0]
    msg = choice.get("message", {})
    finish_reason = choice.get("finish_reason", "stop")
    
    stop_reason_map = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "function_call": "tool_use",
    }
    stop_reason = stop_reason_map.get(finish_reason, "end_turn")
    
    content = []
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
        
    for tc in msg.get("tool_calls", []):
        func = tc.get("function", {})
        try:
            args = json.loads(func.get("arguments", "{}"))
        except Exception:
            args = {"raw": func.get("arguments", "")}
        content.append({
            "type": "tool_use",
            "id": tc.get("id", f"tool_{len(content)}"),
            "name": func.get("name", ""),
            "input": args
        })
        
    usage = openai_resp.get("usage", {})
    return {
        "id": openai_resp.get("id", f"msg_{int(time.time())}"),
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": openai_resp.get("model", "unknown"),
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        }
    }
