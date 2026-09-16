import hashlib
import json
import logging
import math
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..models import CachedResponse
from ..cache import TTLCache

logger = logging.getLogger(__name__)

# In-memory fallback cache for when Redis is unavailable
_mem_cache = TTLCache(ttl_seconds=3600.0, max_size=512)

# In-memory semantic similarity index:
# List of {"model": str, "prompt": str, "vector": dict, "response": dict, "expires_at": float}
_semantic_index: List[dict] = []

# Redis client (initialized lazily)
_redis_client = None
_redis_healthy = True


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _init_redis():
    """Lazily initialize Redis client if REDIS_URL is set."""
    global _redis_client, _redis_healthy
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return None
    try:
        import redis
        _redis_client = redis.from_url(redis_url, decode_responses=True)
        _redis_client.ping()
        logger.info("Cache service connected to Redis")
        return _redis_client
    except Exception as exc:
        logger.warning("Redis cache unavailable (%s), using in-memory fallback", exc)
        _redis_healthy = False
        return None


def extract_prompt(messages_or_body) -> str:
    """Extract user prompt text from chat messages or request body."""
    if isinstance(messages_or_body, dict):
        messages = messages_or_body.get("messages", [])
        if not messages and "prompt" in messages_or_body:
            return str(messages_or_body["prompt"]).strip()
    elif isinstance(messages_or_body, list):
        messages = messages_or_body
    elif isinstance(messages_or_body, str):
        return messages_or_body.strip()
    else:
        return ""

    texts = []
    for m in messages:
        if isinstance(m, dict):
            content = m.get("content", "")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        texts.append(part.get("text", ""))
        elif isinstance(m, str):
            texts.append(m)
    return " ".join(texts).strip()


def compute_sparse_embedding(text: str) -> Dict[str, float]:
    """Compute normalized L2 sparse n-gram term vector for cosine similarity matching."""
    if not text:
        return {}
    tokens = re.findall(r"\b\w+\b", text.lower())
    if not tokens:
        return {}

    counts: Counter = Counter(tokens)
    for token in tokens:
        if len(token) >= 3:
            for i in range(len(token) - 2):
                counts[f"_ng_{token[i:i+3]}"] += 0.5

    norm = math.sqrt(sum(v * v for v in counts.values()))
    if norm == 0:
        return {}
    return {k: round(v / norm, 5) for k, v in counts.items()}


def cosine_similarity(vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
    """Compute cosine similarity between two normalized sparse vectors (0.0 to 1.0)."""
    if not vec1 or not vec2:
        return 0.0
    common = set(vec1.keys()) & set(vec2.keys())
    return sum(vec1[k] * vec2[k] for k in common)


def compute_cache_key(model: str, messages: list, temperature: float = 1.0,
                       max_tokens: Optional[int] = None) -> str:
    """Compute a deterministic cache key from request parameters."""
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def is_cacheable(body: dict) -> bool:
    """Determine if a request is cacheable (non-streaming, deterministic)."""
    if not isinstance(body, dict):
        return False
    # Don't cache streaming requests
    if body.get("stream", False):
        return False
    # Only cache with temperature <= 0.1 (deterministic)
    temp = body.get("temperature", 1.0)
    if temp is not None and temp > 0.1:
        return False
    return True


async def get_cached(cache_key: str, session: Optional[AsyncSession] = None) -> Optional[dict]:
    """Try to retrieve a cached response by exact match key."""
    # 1. Try Redis
    global _redis_client, _redis_healthy
    if _redis_client is None and _redis_healthy:
        _init_redis()
    if _redis_client and _redis_healthy:
        try:
            data = _redis_client.get(f"llmcache:{cache_key}")
            if data:
                logger.info("Cache HIT (Redis): %s", cache_key[:16])
                return json.loads(data)
        except Exception:
            _redis_healthy = False

    # 2. Try in-memory
    hit, val = _mem_cache.get(cache_key)
    if hit:
        logger.info("Cache HIT (memory): %s", cache_key[:16])
        return val

    # 3. Try DB
    if session:
        try:
            result = await session.execute(
                select(CachedResponse).where(
                    CachedResponse.cache_key == cache_key,
                    CachedResponse.expires_at > _utcnow(),
                )
            )
            entry = result.scalars().first()
            if entry:
                logger.info("Cache HIT (DB): %s", cache_key[:16])
                entry.hit_count += 1
                session.add(entry)
                resp = json.loads(entry.response_json)
                _mem_cache.set(cache_key, resp)
                return resp
        except Exception as exc:
            logger.warning("DB cache lookup failed: %s", exc)

    return None


def store_semantic_cache(
    model: str, prompt: str, response: dict, ttl_seconds: int = 3600
) -> None:
    """Index prompt and response for semantic similarity lookups."""
    if not prompt or not response:
        return
    vec = compute_sparse_embedding(prompt)
    if not vec:
        return

    now = time.time()
    global _semantic_index
    _semantic_index = [e for e in _semantic_index if e["expires_at"] > now]

    _semantic_index.append({
        "model": model,
        "prompt": prompt,
        "vector": vec,
        "response": response,
        "expires_at": now + ttl_seconds,
    })
    if len(_semantic_index) > 500:
        _semantic_index.pop(0)


async def get_semantic_cached(
    model: str, prompt: str, threshold: float = 0.85, session: Optional[AsyncSession] = None
) -> Optional[Tuple[dict, float]]:
    """Semantic cache lookup using Cosine Similarity threshold against previously cached prompts.

    Returns (response, similarity_score) if similarity >= threshold, else None.
    """
    if not prompt:
        return None

    query_vec = compute_sparse_embedding(prompt)
    if not query_vec:
        return None

    now = time.time()
    best_match = None
    best_sim = 0.0

    for entry in _semantic_index:
        if entry["model"] != model:
            continue
        if entry["expires_at"] <= now:
            continue
        sim = cosine_similarity(query_vec, entry["vector"])
        if sim > best_sim:
            best_sim = sim
            best_match = entry["response"]

    if best_match and best_sim >= threshold:
        logger.info("Semantic Cache HIT: similarity=%.3f >= %.2f for model '%s'", best_sim, threshold, model)
        return best_match, round(best_sim, 3)

    return None


async def store_cached(
    cache_key: str, response: dict, model: str,
    input_tokens: int = 0, output_tokens: int = 0,
    ttl_seconds: int = 3600,
    session: Optional[AsyncSession] = None,
    prompt: Optional[str] = None,
) -> None:
    """Store a response in all available cache tiers (Exact + Semantic)."""
    response_json = json.dumps(response, separators=(",", ":"))

    # 1. Redis
    global _redis_client, _redis_healthy
    if _redis_client and _redis_healthy:
        try:
            _redis_client.setex(f"llmcache:{cache_key}", ttl_seconds, response_json)
        except Exception:
            _redis_healthy = False

    # 2. In-memory exact
    _mem_cache.set(cache_key, response, ttl=float(ttl_seconds))

    # 3. Semantic similarity index
    if prompt:
        store_semantic_cache(model, prompt, response, ttl_seconds)

    # 4. DB (for persistence across restarts)
    if session:
        try:
            entry = CachedResponse(
                cache_key=cache_key,
                model=model,
                response_json=response_json,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                expires_at=_utcnow() + timedelta(seconds=ttl_seconds),
            )
            await session.merge(entry)
        except Exception as exc:
            logger.warning("DB cache store failed: %s", exc)
