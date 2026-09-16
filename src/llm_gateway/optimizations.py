import json
import logging
from typing import Optional
from fastapi import Response
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

def is_title_generation(messages: list) -> bool:
    """Detect if the message stream is trying to generate a thread title."""
    for msg in messages:
        content = str(msg.get("content", ""))
        if any(term in content.lower() for term in ["generate a title", "generate a short title", "summarize the conversation in a few words"]):
            return True
    return False

def is_quota_probe(messages: list) -> bool:
    """Detect if this is a minimal model quota/connection probe request."""
    # Common probes: "hello", "hi", "test", ".", "respond with hello"
    if len(messages) == 1:
        content = str(messages[0].get("content", "")).strip().lower()
        if content in ["hello", "hi", "test", ".", "respond with hello", "ping"]:
            return True
    return False

def is_prefix_detection(messages: list) -> bool:
    """Detect if this is a Claude Code command prefix/tool environment check."""
    for msg in messages:
        content = str(msg.get("content", ""))
        if "Command:" in content and "Output:" in content:
            return True
    return False

async def try_fast_path(body: dict, path: str) -> Optional[Response]:
    """Intercept trivial/boilerplate requests and return a synthetic response locally."""
    if not isinstance(body, dict):
        return None
        
    messages = body.get("messages", [])
    if not messages and "prompt" in body:
        messages = [{"role": "user", "content": body.get("prompt", "")}]
        
    if not messages:
        return None
        
    is_stream = body.get("stream", False)
    model = body.get("model", "gpt-4o")
    
    # Determine the response style based on the endpoint path
    is_openai_style = "v1/chat/completions" in path or "chat/completions" in path
    is_anthropic_style = "v1/messages" in path or "messages" in path
    
    if not (is_openai_style or is_anthropic_style):
        return None
        
    synthetic_text = None
    
    # 1. Quota Probe Handler
    if is_quota_probe(messages):
        logger.info("Fast-path: Quota probe detected. Returning local mock response.")
        synthetic_text = "Hello! Gateway is fully connected and operational."
        
    # 2. Title Generation Handler
    elif is_title_generation(messages):
        logger.info("Fast-path: Title generation request detected. Returning local title.")
        synthetic_text = "LLM Secure Session"
        
    # 3. Prefix Detection Handler
    elif is_prefix_detection(messages):
        logger.info("Fast-path: Command prefix detection detected. Returning empty response.")
        synthetic_text = ""
        
    if synthetic_text is None:
        return None
        
    # Format and return the response based on streaming setting and style
    if is_stream:
        if is_openai_style:
            async def openai_generator():
                chat_id = "chatcmpl-fast-path"
                # Yield text chunk
                chunk = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": 1677652288,
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": synthetic_text},
                        "finish_reason": None
                    }]
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                # Yield stop chunk
                stop_chunk = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": 1677652288,
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop"
                    }]
                }
                yield f"data: {json.dumps(stop_chunk)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                
            return StreamingResponse(openai_generator(), media_type="text/event-stream")
            
        elif is_anthropic_style:
            async def anthropic_generator():
                msg_id = "msg-fast-path"
                # message_start
                yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'content': [], 'model': model, 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}}})}\n\n".encode("utf-8")
                # content_block_start
                yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n".encode("utf-8")
                # content_block_delta
                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': synthetic_text}})}\n\n".encode("utf-8")
                # content_block_stop
                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n".encode("utf-8")
                # message_delta
                yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 1}})}\n\n".encode("utf-8")
                # message_stop
                yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n".encode("utf-8")
                
            return StreamingResponse(anthropic_generator(), media_type="text/event-stream")
    else:
        # Non-streaming Response
        if is_openai_style:
            resp_body = {
                "id": "chatcmpl-fast-path",
                "object": "chat.completion",
                "created": 1677652288,
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": synthetic_text
                    },
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2
                }
            }
            return JSONResponse(content=resp_body)
        elif is_anthropic_style:
            resp_body = {
                "id": "msg-fast-path",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": synthetic_text}],
                "model": model,
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1
                }
            }
            return JSONResponse(content=resp_body)
            
    return None
