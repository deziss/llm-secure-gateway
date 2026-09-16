from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import httpx
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from opentelemetry import trace
import time
import json

logger = logging.getLogger(__name__)

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
from ..auth.users import set_ui_user
from ..optimizations import try_fast_path
from ..translation import translate_request_body

router = APIRouter(tags=["v2-proxy"], dependencies=[Depends(set_ui_user)])


@router.get("/v1/models")
async def get_federated_models(
    request: Request,
    config_service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Federated discoverable model catalog in standard OpenAI format."""
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
                    "owned_by": backend.name,
                })
    return {"object": "list", "data": models_list}


@router.api_route("/direct/{backend_name}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def direct_proxy(
    backend_name: str,
    path: str,
    request: Request,
    config_service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
):
    """Direct Proxy: routes /direct/{backend_name}/{path} to the named backend."""
    apply_rate_limit(request)

    body = None
    model = None
    if request.method in ["POST", "PUT", "PATCH"]:
        try:
            body = await request.json()
            model = body.get("model") if isinstance(body, dict) else None
        except (json.JSONDecodeError, ValueError):
            pass

    owner_id, is_admin_ui = get_owner_id_from_request(request)

    backend = await config_service.get_backend(session, backend_name)
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend '{backend_name}' not found")

    # 1. Fast-Path Optimization check
    enable_fast_path = await config_service.get_setting(session, "ENABLE_FAST_PATH_OPTIMIZATIONS", "false")
    if str(enable_fast_path).lower() == "true":
        fast_response = await try_fast_path(body, path)
        if fast_response:
            return fast_response

    # 2. Protocol Translation request mapping
    enable_translation = await config_service.get_setting(session, "ENABLE_PROTOCOL_TRANSLATION", "false")
    translation_mode = "none"
    if str(enable_translation).lower() == "true" and hasattr(backend, "translation_mode"):
        translation_mode = backend.translation_mode
        if translation_mode != "none":
            body = translate_request_body(translation_mode, body)
            if isinstance(body, dict):
                model = body.get("model", model)

    if not is_admin_ui:
        await check_owner_permissions(session, owner_id, backend, path, model)

    provider = backend.backend_type.value if hasattr(backend.backend_type, "value") else str(backend.backend_type)
    tenant_tracer = PhoenixTraceManager.get_tracer(owner_id, provider)
    headers = await build_backend_auth_headers(request, backend, session, owner_id, provider)

    span = tenant_tracer.start_span("llm_request_direct")
    try:
        span.set_attribute("openinference.span.kind", "LLM")
        if model:
            span.set_attribute("llm.model_name", model)
        span.set_attribute("http.method", request.method)
        from ..services.agent_detector import detect_coding_agent
        agent_name, agent_version = detect_coding_agent(
            request.headers.get("user-agent"),
            request.headers.get("x-client-name") or request.headers.get("x-source")
        )
        span.set_attribute("agent.client", agent_name)
        span.set_attribute("agent.version", agent_version)
        span.set_attribute("owner.id", owner_id)
        span.set_attribute("backend.name", backend.name)
        span.set_attribute("backend.url", backend.base_url)
        span.set_attribute("routing_mode", "direct")
        span.set_attribute("model", model or "unknown")
        span.set_attribute("provider", provider)
        span.set_attribute("translation_mode", translation_mode)

        if isinstance(body, dict):
            messages = body.get("messages", [])
            if messages:
                span.set_attribute("llm.input_messages", json.dumps(messages))
            params = {k: v for k, v in body.items() if k not in ("messages", "model")}
            if params:
                span.set_attribute("llm.invocation_parameters", json.dumps(params))

        start_time = time.time()
        raw_body = None
        if not body and request.method in ["POST", "PUT", "PATCH"]:
            raw_body = await request.body()

        is_retry = await config_service.get_setting(session, "ENABLE_RETRY_BACKOFF", "false")
        enable_retry = str(is_retry).lower() == "true"

        # Determine thinking block normalization setting
        enable_thinking_global = await config_service.get_setting(session, "ENABLE_THINKING_NORMALIZATION", "false")
        normalize_thinking = (str(enable_thinking_global).lower() == "true")
        if hasattr(backend, "normalize_thinking") and backend.normalize_thinking:
            normalize_thinking = True

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
                                provider=provider or "unknown",
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

            return StreamingResponse(
                stream_with_telemetry(
                    r, start_time, used_url, model or "unknown", target_url, owner_id, span, client,
                    translation_mode=translation_mode, normalize_thinking=normalize_thinking,
                    on_complete=on_stream_complete,
                ),
                status_code=r.status_code,
                headers=dict(r.headers),
            )

        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RequestError) as exc:
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            duration = time.time() - start_time
            record_request_metrics(
                backend_url=backend.base_url, model=model or "unknown",
                endpoint_uri=path, user_name=owner_id, success=False, request_duration=duration,
            )
            span.end()
            logger.error("All backend URLs failed: %s. Last error: %s", _get_all_urls(backend), exc)
            raise HTTPException(
                status_code=502,
                detail="All backend URLs failed",
            )

    except Exception as e:
        if span.is_recording():
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            span.end()
        raise


@router.get("/provider/{provider}/api/tags")
async def aggregate_v2_ollama_tags(
    provider: str,
    request: Request,
    config_service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Federated Aggregation: provider-scoped /api/tags."""
    is_enabled = await config_service.get_setting(session, "ENABLE_MODEL_FEDERATION", "false")
    if str(is_enabled).lower() != "true":
        raise HTTPException(status_code=404, detail="Federation disabled. Query /direct/{backend}/api/tags instead.")

    apply_rate_limit(request)

    backends = await config_service.list_backends(session)
    models_set: set = set()
    models_list = []

    for backend in backends:
        if backend.backend_type.value == provider or backend.backend_type == provider:
            for model in backend.models:
                if model not in models_set:
                    models_set.add(model)
                    models_list.append({
                        "name": model,
                        "model": model,
                        "modified_at": backend.updated_at.isoformat() if backend.updated_at else backend.created_at.isoformat(),
                        "size": 0,
                        "digest": f"federated-{provider}",
                        "details": {
                            "parent_model": "",
                            "format": "gguf",
                            "family": "federated",
                            "families": ["federated"],
                            "parameter_size": "unknown",
                            "quantization_level": "unknown",
                        },
                    })
    return {"models": models_list}


@router.get("/provider/{provider}/v1/models")
async def aggregate_v2_openai_models(
    provider: str,
    request: Request,
    config_service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Federated Aggregation: provider-scoped /v1/models."""
    is_enabled = await config_service.get_setting(session, "ENABLE_MODEL_FEDERATION", "false")
    if str(is_enabled).lower() != "true":
        raise HTTPException(status_code=404, detail="Federation disabled. Query /direct/{backend}/v1/models instead.")

    apply_rate_limit(request)

    backends = await config_service.list_backends(session)
    models_set: set = set()
    models_list = []

    for backend in backends:
        if backend.backend_type.value == provider or backend.backend_type == provider:
            for model in backend.models:
                if model not in models_set:
                    models_set.add(model)
                    models_list.append({
                        "id": model,
                        "object": "model",
                        "created": int(backend.created_at.timestamp()),
                        "owned_by": backend.name,
                    })
    return {"object": "list", "data": models_list}


@router.api_route("/provider/{provider}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def dynamic_proxy(
    provider: str,
    path: str,
    request: Request,
    config_service: ConfigService = Depends(get_config_service),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Flexible Proxy (v2): routes /provider/{provider}/{path} to the appropriate backend."""
    apply_rate_limit(request)

    body = None
    model = None
    if request.method in ["POST", "PUT", "PATCH"]:
        try:
            body = await request.json()
            model = body.get("model") if isinstance(body, dict) else None
        except (json.JSONDecodeError, ValueError):
            pass

    if not model and "chat/completions" in path and request.method == "POST":
        raise HTTPException(status_code=400, detail="Model field is required in body")

    # 0. Alias resolution
    fallback_chain_id = None
    if model:
        try:
            from ..services.fallback_service import resolve_alias
            alias_b, real_m, fallback_chain_id = await resolve_alias(session, model)
            if alias_b or real_m != model:
                model = real_m
                if isinstance(body, dict):
                    body["model"] = real_m
        except Exception as e:
            logger.debug("Alias resolution error: %s", e)

    owner_id, is_admin_ui = get_owner_id_from_request(request)

    # 0a. Budget enforcement
    enable_budget = await config_service.get_setting(session, "ENABLE_BUDGET_ENFORCEMENT", "true")
    if str(enable_budget).lower() == "true":
        from ..services.spend_service import check_budget
        api_key_hash = getattr(request.state.user, "key_hash", None) if hasattr(request.state, "user") else None
        await check_budget(session, owner_id, api_key_hash)

    # 0. Multimodal inlining
    from ..services.image_inliner import inline_remote_images
    if isinstance(body, dict) and "messages" in body:
        try:
            body["messages"] = await inline_remote_images(body["messages"])
        except Exception as e:
            logger.debug("Image inlining skipped: %s", e)

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

    # 1. Fast-Path Optimization check
    enable_fast_path = await config_service.get_setting(session, "ENABLE_FAST_PATH_OPTIMIZATIONS", "false")
    if str(enable_fast_path).lower() == "true":
        fast_response = await try_fast_path(body, path)
        if fast_response:
            return fast_response

    backend = await get_target_backend(model or "unknown", config_service, session, provider=provider, owner_id=owner_id if not is_admin_ui else None)

    # 2. Protocol Translation request mapping
    enable_translation = await config_service.get_setting(session, "ENABLE_PROTOCOL_TRANSLATION", "false")
    translation_mode = "none"
    if str(enable_translation).lower() == "true" and hasattr(backend, "translation_mode"):
        translation_mode = backend.translation_mode
        if translation_mode != "none":
            body = translate_request_body(translation_mode, body)
            if isinstance(body, dict):
                model = body.get("model", model)

    tenant_tracer = PhoenixTraceManager.get_tracer(owner_id, provider)
    span = tenant_tracer.start_span("llm_request")

    try:
        span.set_attribute("openinference.span.kind", "LLM")
        if model:
            span.set_attribute("llm.model_name", model)
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
        span.set_attribute("provider", provider)
        span.set_attribute("model", model or "unknown")
        span.set_attribute("endpoint", path)
        span.set_attribute("backend.name", backend.name)
        span.set_attribute("backend.url", backend.base_url)
        span.set_attribute("backend.type", str(backend.backend_type.value) if hasattr(backend.backend_type, "value") else str(backend.backend_type))
        span.set_attribute("translation_mode", translation_mode)

        if not is_admin_ui:
            await check_owner_permissions(session, owner_id, backend, path, model)

        headers = await build_backend_auth_headers(request, backend, session, owner_id, provider)

        if isinstance(body, dict):
            messages = body.get("messages", [])
            prompt = body.get("prompt", "")
            if messages:
                span.set_attribute("llm.input_messages", json.dumps(messages))
                span.set_attribute("input.value", json.dumps(messages))
            elif prompt:
                span.set_attribute("input.value", prompt)
            params = {k: v for k, v in body.items() if k not in ("messages", "model")}
            if params:
                span.set_attribute("llm.invocation_parameters", json.dumps(params))

        start_time = time.time()
        raw_body = None
        if not body and request.method in ["POST", "PUT", "PATCH"]:
            raw_body = await request.body()

        is_retry = await config_service.get_setting(session, "ENABLE_RETRY_BACKOFF", "false")
        enable_retry = str(is_retry).lower() == "true"

        # Determine thinking block normalization setting
        enable_thinking_global = await config_service.get_setting(session, "ENABLE_THINKING_NORMALIZATION", "false")
        normalize_thinking = (str(enable_thinking_global).lower() == "true")
        if hasattr(backend, "normalize_thinking") and backend.normalize_thinking:
            normalize_thinking = True

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
                                provider=provider or "unknown",
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

            return StreamingResponse(
                stream_with_telemetry(
                    r, start_time, used_url, model or "unknown", target_url, owner_id, span, client,
                    translation_mode=translation_mode, normalize_thinking=normalize_thinking,
                    on_complete=on_stream_complete,
                ),
                status_code=r.status_code,
                headers=dict(r.headers),
            )

        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RequestError) as exc:
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            duration = time.time() - start_time
            record_request_metrics(
                backend_url=backend.base_url, model=model or "unknown",
                endpoint_uri=path, user_name=owner_id, success=False, request_duration=duration,
            )
            span.end()
            logger.error("All backend URLs failed: %s. Last error: %s", _get_all_urls(backend), exc)
            raise HTTPException(
                status_code=502,
                detail="All backend URLs failed",
            )

    except Exception as e:
        if span.is_recording():
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            span.end()
        raise
