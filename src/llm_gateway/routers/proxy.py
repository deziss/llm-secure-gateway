import httpx
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from opentelemetry import trace
from ..services import ConfigService, get_config_service
from ..database import get_session
from ..telemetry import record_request_metrics, stream_with_telemetry, flatten_attributes, PhoenixTraceManager
from ..proxy_helpers import (
    get_owner_id_from_request,
    apply_rate_limit,
    check_owner_permissions,
    build_backend_auth_headers,
    get_target_backend,
    try_backend_with_fallback,
    try_with_cross_provider_fallback,
    _get_all_urls,
    clean_target_url,
)
import time
import json
import logging
from ..auth.users import set_ui_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["proxy"], dependencies=[Depends(set_ui_user)])

@router.get("/api/tags")
async def aggregate_ollama_tags(
    request: Request,
    config_service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session)
) -> dict:
    """
    Federated Aggregation: Intercept GET /api/tags and return a unified list of all models
    available across all registered backend servers to simulate a single massive provider.
    """
    # Check if Federation is enabled globally
    is_enabled = await config_service.get_setting(session, "ENABLE_MODEL_FEDERATION", "false")
    if str(is_enabled).lower() != "true":
         raise HTTPException(status_code=404, detail="Federation disabled. Query /direct/{backend}/api/tags instead.")

    apply_rate_limit(request)

    backends = await config_service.list_backends(session)
    models_set = set()
    models_list = []

    for backend in backends:
        for model in backend.models:
            if model not in models_set:
                models_set.add(model)
                # Create a fake Ollama-compatible model detail block
                models_list.append({
                    "name": model,
                    "model": model,
                    "modified_at": backend.updated_at.isoformat() if backend.updated_at else backend.created_at.isoformat(),
                    "size": 0,
                    "digest": "federated",
                    "details": {
                        "parent_model": "",
                        "format": "gguf",
                        "family": "federated",
                        "families": ["federated"],
                        "parameter_size": "unknown",
                        "quantization_level": "unknown"
                    }
                })
    return {"models": models_list}

@router.get("/api/version")
async def ollama_version(request: Request) -> dict:
    """Return an Ollama-compatible version payload for CLI and client handshakes."""
    apply_rate_limit(request)
    return {"version": "0.10.0"}

@router.get("/v1/models")
async def aggregate_openai_models(
    request: Request,
    config_service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session)
) -> dict:
    """
    Federated Aggregation: Intercept GET /v1/models and return a unified list.
    """
    # Check if Federation is enabled globally
    is_enabled = await config_service.get_setting(session, "ENABLE_MODEL_FEDERATION", "false")
    if str(is_enabled).lower() != "true":
         raise HTTPException(status_code=404, detail="Federation disabled. Query /direct/{backend}/v1/models instead.")
    apply_rate_limit(request)

    backends = await config_service.list_backends(session)
    models_set = set()
    models_list = []

    for backend in backends:
        for model in backend.models:
            if model not in models_set:
                models_set.add(model)
                models_list.append({
                    "id": model,
                    "object": "model",
                    "created": int(backend.created_at.timestamp()),
                    "owned_by": backend.name
                })
    return {"object": "list", "data": models_list}


@router.post("/v1/messages")
@router.post("/messages")
async def anthropic_messages_endpoint(
    request: Request,
    config_service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session)
):
    """Native Anthropic Messages API endpoint (/v1/messages)."""
    apply_rate_limit(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
        
    raw_model = body.get("model", "unknown")
    if raw_model == "unknown":
        raise HTTPException(status_code=400, detail="Model field is required")
        
    query_prov = request.query_params.get("provider")
    from ..proxy_helpers import resolve_provider_and_model
    prov_hint, clean_model = resolve_provider_and_model(raw_model, query_prov)
    provider = prov_hint or query_prov
    model = clean_model

    owner_id, is_admin_ui = get_owner_id_from_request(request)
    
    enable_budget = await config_service.get_setting(session, "ENABLE_BUDGET_ENFORCEMENT", "true")
    if str(enable_budget).lower() == "true":
        from ..services.spend_service import check_budget
        api_key_hash = getattr(request.state.user, "key_hash", None) if hasattr(request.state, "user") else None
        await check_budget(session, owner_id, api_key_hash)

    backend = await get_target_backend(model, config_service, session, provider=provider, owner_id=owner_id if not is_admin_ui else None)
    is_native_anthropic = (
        str(getattr(backend.backend_type, "value", backend.backend_type)).lower() == "anthropic"
    )

    if not is_admin_ui:
        target_perm_ep = "/v1/messages" if is_native_anthropic else "/v1/chat/completions"
        await check_owner_permissions(session, owner_id, backend, target_perm_ep, model)
    
    is_stream = bool(body.get("stream", False))
    from ..proxy_helpers import _get_client, build_backend_auth_headers, _get_all_urls

    headers = await build_backend_auth_headers(request, backend, session, owner_id, backend.name)
    headers["Content-Type"] = "application/json"

    if is_native_anthropic:
        forward_body = dict(body)
        forward_body["model"] = model
        target_path = "/v1/messages"
    else:
        from ..translation.anthropic_to_openai import translate_request
        forward_body = translate_request(body)
        forward_body["model"] = model
        target_path = "/v1/chat/completions"

    client = _get_client(backend.base_url)
    all_urls = _get_all_urls(backend)
    
    last_err = None
    for u in all_urls:
        dest_url = f"{u.rstrip('/')}{target_path}"
        try:
            if is_stream:
                req_obj = client.build_request("POST", dest_url, json=forward_body, headers=headers)
                raw_resp = await client.send(req_obj, stream=True)
                if raw_resp.status_code >= 400:
                    err_text = await raw_resp.aread()
                    await raw_resp.aclose()
                    raise HTTPException(status_code=raw_resp.status_code, detail=err_text.decode("utf-8", errors="ignore"))
                
                if is_native_anthropic:
                    async def stream_raw():
                        async for chunk in raw_resp.aiter_raw():
                            yield chunk
                    return StreamingResponse(stream_raw(), media_type="text/event-stream")
                else:
                    from ..translation.anthropic_to_openai import translate_stream
                    return StreamingResponse(translate_stream(raw_resp), media_type="text/event-stream")
            else:
                resp = await client.post(dest_url, json=forward_body, headers=headers)
                if resp.status_code >= 400:
                    raise HTTPException(status_code=resp.status_code, detail=resp.text)
                
                if is_native_anthropic:
                    return JSONResponse(content=resp.json(), status_code=resp.status_code)
                else:
                    from ..translation.anthropic_to_openai import translate_response
                    translated = translate_response(resp.json())
                    return JSONResponse(content=translated, status_code=200)
        except HTTPException:
            raise
        except Exception as e:
            last_err = e
            continue

    raise HTTPException(status_code=502, detail=f"Backend request failed: {last_err}")


@router.post("/v1/audio/speech")
@router.post("/audio/speech")
async def audio_speech_endpoint(
    request: Request,
    config_service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session)
):
    """OpenAI-compatible Text-to-Speech (TTS) endpoint."""
    apply_rate_limit(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
        
    model = body.get("model", "tts-1")
    text_input = body.get("input", "")
    if not text_input:
        raise HTTPException(status_code=400, detail="Input text is required")
        
    owner_id, is_admin_ui = get_owner_id_from_request(request)
    query_prov = request.query_params.get("provider")
    from ..proxy_helpers import resolve_provider_and_model, _get_client, build_backend_auth_headers
    prov_hint, clean_model = resolve_provider_and_model(model, query_prov)
    provider = prov_hint or query_prov
    model = clean_model

    backend = await get_target_backend(model, config_service, session, provider=provider, owner_id=owner_id if not is_admin_ui else None)
    client = _get_client(backend.base_url)
    headers = await build_backend_auth_headers(request, backend, session, owner_id, backend.name)
    headers["Content-Type"] = "application/json"

    dest_url = f"{backend.base_url.rstrip('/')}/v1/audio/speech"
    try:
        req_obj = client.build_request("POST", dest_url, json=body, headers=headers)
        resp = await client.send(req_obj, stream=True)
        if resp.status_code >= 400:
            err = await resp.aread()
            await resp.aclose()
            raise HTTPException(status_code=resp.status_code, detail=err.decode("utf-8", errors="ignore"))
        
        fmt = body.get("response_format", "mp3")
        media_type = f"audio/{fmt}" if fmt in ("mp3", "wav", "ogg") else "audio/mpeg"
        async def stream_audio():
            async for chunk in resp.aiter_raw():
                yield chunk
        return StreamingResponse(stream_audio(), media_type=media_type)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"TTS backend unreachable: {e}")


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
async def proxy_request(
    path: str,
    request: Request,
    config_service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session)
) -> StreamingResponse:
    """
    Proxy request to the selected LLM backend with dynamic routing + fallback.
    Includes:
    - Rate limiting
    - Model selection (for applicable methods)
    - Dynamic path forwarding
    - Header forwarding
    - Streaming response
    - OpenTelemetry spans with multi-tenant tracing
    - Automatic fallback to secondary URLs on connection failure
    """

    apply_rate_limit(request)

    # Extract model and provider from body only for methods that support it
    model = "unknown"
    provider = None
    body = None

    if request.method in ["POST", "PUT", "PATCH"]:
        try:
            body = await request.json()
            if isinstance(body, dict):
                model = body.get("model", "unknown")
                provider = body.get("provider")
        except (json.JSONDecodeError, ValueError):
            pass  # Body might not be JSON or empty

    # For chat completions, model is conventionally required
    if "chat/completions" in path and model == "unknown" and request.method == "POST":
        if not body or not body.get("model"):
            raise HTTPException(status_code=400, detail="Model field is required")

    # 0. Alias resolution
    fallback_chain_id = None
    if model and model != "unknown":
        try:
            from ..services.fallback_service import resolve_alias
            alias_b, real_m, fallback_chain_id = await resolve_alias(session, model)
            if alias_b or real_m != model:
                model = real_m
                if isinstance(body, dict):
                    body["model"] = real_m
        except Exception as e:
            logger.debug("Alias resolution error: %s", e)

    # Provider resolution & prefix stripping (e.g. 'vllm-42/llama-3.1-8b' or ?provider=vllm-42)
    query_prov = request.query_params.get("provider")
    if query_prov and not provider:
        provider = query_prov

    from ..proxy_helpers import resolve_provider_and_model
    prov_hint, clean_model = resolve_provider_and_model(model, provider)
    if prov_hint and not provider:
        provider = prov_hint
    if clean_model != model:
        model = clean_model
        if isinstance(body, dict):
            body["model"] = clean_model

    owner_id, is_admin_ui = get_owner_id_from_request(request)

    # 0a. Budget enforcement
    enable_budget = await config_service.get_setting(session, "ENABLE_BUDGET_ENFORCEMENT", "true")
    if str(enable_budget).lower() == "true":
        from ..services.spend_service import check_budget
        api_key_hash = getattr(request.state.user, "key_hash", None) if hasattr(request.state, "user") else None
        await check_budget(session, owner_id, api_key_hash)

    # 0. Backpressure & Multimodal inlining
    from ..services.backpressure_service import backpressure_guard
    from ..services.image_inliner import inline_remote_images
    if isinstance(body, dict) and "messages" in body:
        try:
            body["messages"] = await inline_remote_images(body["messages"])
        except Exception as e:
            logger.debug("Image inlining skipped: %s", e)

    # Modality filter (strip image content if model is text-only when VISION_ENABLED)
    if isinstance(body, dict) and "messages" in body:
        vision_enabled = await config_service.get_setting(session, "VISION_ENABLED", "true")
        if str(vision_enabled).lower() == "true":
            from ..services.model_metadata_service import strip_images_if_unsupported
            body["messages"], _ = strip_images_if_unsupported(body["messages"], model, vision_enabled=True)

    # 0b. Guardrails & PII redaction
    if isinstance(body, dict) and "messages" in body:
        enable_guardrails = await config_service.get_setting(session, "ENABLE_GUARDRAILS", "false")
        if str(enable_guardrails).lower() == "true":
            from ..services.guardrails_service import check_prompt_injection
            sensitivity = await config_service.get_setting(session, "GUARDRAIL_SENSITIVITY", "medium")
            is_safe, _, matches = check_prompt_injection(body["messages"], sensitivity=sensitivity)
            if not is_safe:
                logger.warning("Prompt injection blocked: %s", matches)
                raise HTTPException(status_code=400, detail="Request blocked by safety guardrails: prompt injection detected")

        enable_pii = await config_service.get_setting(session, "ENABLE_PII_MASKING", "false")
        if str(enable_pii).lower() == "true":
            from ..services.pii_service import get_pii_scanner
            scanner = get_pii_scanner()
            redacted, pii_matches = scanner.scan_messages(body["messages"])
            if pii_matches:
                body["messages"] = redacted

    # 0c. Cache lookup (Exact + Semantic)
    enable_cache = await config_service.get_setting(session, "ENABLE_EXACT_CACHE", "false")
    enable_semantic_cache = await config_service.get_setting(session, "ENABLE_SEMANTIC_CACHE", "false")
    cache_key = None
    if (str(enable_cache).lower() == "true" or str(enable_semantic_cache).lower() == "true") and isinstance(body, dict):
        from ..services.cache_service import is_cacheable, compute_cache_key, get_cached, get_semantic_cached, extract_prompt
        if is_cacheable(body):
            if str(enable_cache).lower() == "true":
                cache_key = compute_cache_key(
                    model or "unknown",
                    body.get("messages", []),
                    body.get("temperature", 1.0),
                    body.get("max_tokens"),
                )
                cached_resp = await get_cached(cache_key, session)
                if cached_resp:
                    return JSONResponse(content=cached_resp, headers={"X-Cache": "HIT"})

            if str(enable_semantic_cache).lower() == "true":
                prompt_text = extract_prompt(body)
                if prompt_text:
                    try:
                        threshold = float(await config_service.get_setting(session, "SEMANTIC_CACHE_THRESHOLD", "0.85"))
                    except (ValueError, TypeError):
                        threshold = 0.85
                    sem_res = await get_semantic_cached(model or "unknown", prompt_text, threshold=threshold, session=session)
                    if sem_res:
                        sem_resp, sim_score = sem_res
                        return JSONResponse(
                            content=sem_resp,
                            headers={"X-Cache": "SEMANTIC-HIT", "X-Cache-Similarity": str(sim_score)},
                        )

    # Resolve backend
    backend = await get_target_backend(model, config_service, session, provider, owner_id=owner_id if not is_admin_ui else None)

    if not is_admin_ui:
        await check_owner_permissions(session, owner_id, backend, path, model)

    # Danger block filter
    from sqlalchemy import select
    from ..models import Owner
    normalized_path = "/" + path.lstrip("/")
    owner_obj = None
    if owner_id != "anonymous":
        owner_res = await session.execute(select(Owner).where(Owner.id == owner_id))
        owner_obj = owner_res.scalars().first()
    if owner_obj and getattr(owner_obj, "block_endpoints", True):
        dangerous = ["/api/pull", "/api/delete", "/api/push", "/api/create"]
        if any(normalized_path.startswith(d) for d in dangerous):
            raise HTTPException(status_code=403, detail=f"Dangerous endpoint '{normalized_path}' is blocked for Owner '{owner_id}'")

    provider_id = provider if provider else backend.backend_type.value

    headers = await build_backend_auth_headers(request, backend, session, owner_id, provider_id)

    # ---------------------------------------------------------
    # Manual Span Management for Streaming & Detailed Telemetry
    # ---------------------------------------------------------
    
    # Get tenant-specific tracer (by owner and provider)
    tenant_tracer = PhoenixTraceManager.get_tracer(owner_id, provider_id)

    # Manual Span Management - llm_request as root
    span = tenant_tracer.start_span("llm_request")
    
    try:
        # OpenInference attributes
        span.set_attribute("openinference.span.kind", "LLM")
        span.set_attribute("llm.model_name", model)
        
        # Standard attributes
        span.set_attribute("http.method", request.method)
        from ..services.agent_detector import detect_coding_agent
        agent_name, agent_version = detect_coding_agent(
            request.headers.get("user-agent"),
            request.headers.get("x-client-name") or request.headers.get("x-source")
        )
        span.set_attribute("agent.client", agent_name)
        span.set_attribute("agent.version", agent_version)
        span.set_attribute("http.url", str(request.url))
        span.set_attribute("owner.id", owner_id)
        span.set_attribute("provider", provider_id)
        span.set_attribute("model", model or "unknown")
        span.set_attribute("backend.name", backend.name)
        span.set_attribute("backend.url", backend.base_url)
        span.set_attribute("backend.fallback_urls", json.dumps(getattr(backend, "fallback_urls", []) or []))
        span.set_attribute("backend.type", str(backend.backend_type.value) if hasattr(backend.backend_type, 'value') else str(backend.backend_type))
        
        # Extract and flatten input messages and parameters if available
        if isinstance(body, dict):
            messages = body.get("messages", [])
            if messages:
                flat_attrs = flatten_attributes(messages, prefix="llm.input_messages")
                for k, v in flat_attrs.items():
                    span.set_attribute(k, v)
                    
            params = {k: v for k, v in body.items() if k not in ("messages", "model")}
            if params:
                 span.set_attribute("llm.invocation_parameters", json.dumps(params))

        start_time = time.time()
        raw_body = await request.body() if not body else None
        
        is_retry = await config_service.get_setting(session, "ENABLE_RETRY_BACKOFF", "false")
        enable_retry = str(is_retry).lower() == "true"
        
        try:
            r, used_url, backend, client = await try_with_cross_provider_fallback(
                backend, path, request.method, headers, body, raw_body, session, config_service,
                fallback_chain_id=fallback_chain_id, enable_retry=enable_retry
            )
            target_url = clean_target_url(used_url, path)
            span.set_attribute("http.url", target_url)
            span.set_attribute("backend.used_url", used_url)

            api_key_hash = getattr(request.state.user, "key_hash", None) if hasattr(request.state, "user") else None
            enable_spend = await config_service.get_setting(session, "ENABLE_SPEND_TRACKING", "true")
            is_streaming = isinstance(body, dict) and body.get("stream", False)

            async def on_stream_complete(in_tokens: int, out_tokens: int, chunks: list[bytes]):
                if str(enable_spend).lower() == "true":
                    try:
                        from ..database import get_session_context
                        from ..services.spend_service import record_spend
                        async with get_session_context() as db_sess:
                            await record_spend(
                                db_sess,
                                owner_id=owner_id,
                                api_key_hash=api_key_hash,
                                model=model or "unknown",
                                provider=provider_id or "unknown",
                                input_tokens=in_tokens,
                                output_tokens=out_tokens,
                            )
                            await db_sess.commit()
                    except Exception as e:
                        logger.warning("Failed to record spend: %s", e)

                if not is_streaming and (cache_key or str(enable_semantic_cache).lower() == "true"):
                    try:
                        from ..services.cache_service import store_cached, extract_prompt, compute_cache_key
                        full_body_str = b"".join(chunks).decode("utf-8", errors="ignore")
                        from ..services.json_healer import heal_json
                        ok, parsed_json, _ = heal_json(full_body_str)
                        resp_json = parsed_json if (ok and isinstance(parsed_json, dict)) else json.loads(full_body_str)
                        prompt_text = extract_prompt(body) if isinstance(body, dict) else None
                        eff_key = cache_key or compute_cache_key(
                            model or "unknown",
                            body.get("messages", []) if isinstance(body, dict) else [],
                            body.get("temperature", 1.0) if isinstance(body, dict) else 1.0,
                            body.get("max_tokens") if isinstance(body, dict) else None,
                        )
                        await store_cached(
                            cache_key=eff_key,
                            response=resp_json,
                            model=model or "unknown",
                            input_tokens=in_tokens,
                            output_tokens=out_tokens,
                            prompt=prompt_text,
                        )
                    except Exception as e:
                        logger.debug("Failed to store cache: %s", e)

            # 3. Pass span to streaming generator which handles ending it
            return StreamingResponse(
                stream_with_telemetry(
                    r, start_time, used_url, model, path, owner_id, span, client,
                    on_complete=on_stream_complete,
                ),
                status_code=r.status_code,
                headers=dict(r.headers)
            )

        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RequestError) as exc:
            # All URLs failed
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            
            duration = time.time() - start_time
            record_request_metrics(
                 backend_url=backend.base_url, model=model, endpoint_uri=path, user_name=owner_id,
                 success=False, request_duration=duration
            )
            span.end()
            all_urls = _get_all_urls(backend)
            logger.error("All backend URLs failed: %s. Last error: %s", all_urls, exc)
            from ..services.error_sanitizer import sanitize_error_message
            safe_err = sanitize_error_message(str(exc), status_code=502)
            raise HTTPException(
                status_code=502,
                detail=safe_err
            )
                
    except Exception as e:
        # Catch-all for other errors (e.g. during span setup)
        if span.is_recording():
             span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
             span.record_exception(e)
             span.end()
        raise e


