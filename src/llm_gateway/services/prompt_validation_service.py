"""Semantic prompt validation (Know Your Prompts, Know Your Vectors).

Risks grow when AI agents pull context from websites, docs, tickets or
retrieval corpora: poisoned content (hidden prompt injections) and prompt
injection payloads arrive inside otherwise legitimate-looking text. Regex
guardrails catch known phrasing; this comparator catches *paraphrases of known
bad prompts* — semantic variants of exemplars the security team stored, plus
optionally known good prompts whose absence raises the bar for anything far
from approved usage.

Design mirrors the semantic cache (see cache_service): cheap in-process
sparse n-gram embeddings + cosine similarity, no external vector store, no
network round-trip on the hot path. Exemplars live in the ``promptexemplar``
table and are cached in memory (refreshed by a slow background loop and on
CRUD via ``invalidate_exemplar_cache``).

Config (SystemSetting, admin-editable):
  PROMPT_VALIDATION_ENABLED   "true" to gate requests (default false)
  PROMPT_VALIDATION_THRESHOLD cosine similarity to flag (default "0.60")
  PROMPT_VALIDATION_ACTION    "block" (400) or "flag" (audit + span attr,
                              request proceeds) — default "flag"
  PROMPT_VALIDATION_MODE      "denylist" (flag near BAD exemplars, default)
                              "allowlist" (flag when NOT near a GOOD exemplar)
                              "both"
Exemplars are managed via /admin/prompt-exemplars (role-protected API).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from sqlalchemy import select

from .cache_service import compute_sparse_embedding, cosine_similarity, extract_prompt

logger = logging.getLogger(__name__)

_EXEMPLAR_TTL_SECONDS = 60.0

# (expires_at, exemplars) — exemplars are [(kind, text, vector, label)]
_cache: tuple[float, list] | None = None
_cache_lock = asyncio.Lock()


def invalidate_exemplar_cache() -> None:
    """Force the next validation to reload exemplars from the DB."""
    global _cache
    _cache = None


async def load_exemplars(session) -> list:
    """Load (and embed) active exemplars, cached briefly in-process."""
    global _cache
    now = time.monotonic()
    if _cache is not None and _cache[0] > now:
        return _cache[1]
    async with _cache_lock:
        now = time.monotonic()
        if _cache is not None and _cache[0] > now:
            return _cache[1]
        from ..models import PromptExemplar

        result = await session.execute(
            select(PromptExemplar).where(PromptExemplar.is_active == True)  # noqa: E712
        )
        items = []
        for ex in result.scalars().all():
            vec = compute_sparse_embedding(ex.text)
            if vec:
                items.append((ex.kind, vec, ex.label or ex.kind))
        _cache = (now + _EXEMPLAR_TTL_SECONDS, items)
        return items


def compare_prompt(prompt: str, exemplars: list, *, threshold: float,
                   mode: str) -> dict:
    """Pure comparator (no I/O): nearest exemplar per kind + verdict.

    Returns {"ok": bool, "max_bad": (sim, label)|None, "max_good": (sim,
    label)|None, "threshold": float, "mode": str}.
    """
    qvec = compute_sparse_embedding(prompt)
    max_bad = None
    max_good = None
    if qvec:
        for kind, vec, label in exemplars:
            sim = cosine_similarity(qvec, vec)
            if kind == "bad":
                if max_bad is None or sim > max_bad[0]:
                    max_bad = (sim, label)
            else:
                if max_good is None or sim > max_good[0]:
                    max_good = (sim, label)

    violation_reasons = []
    if mode in ("denylist", "both") and max_bad is not None and max_bad[0] >= threshold:
        violation_reasons.append(
            f"prompt is {max_bad[0]:.0%} similar to known-bad exemplar '{max_bad[1]}'")
    if mode in ("allowlist", "both"):
        good_exemplars_exist = any(k == "good" for k, _, _ in exemplars)
        if good_exemplars_exist and (max_good is None or max_good[0] < threshold):
            sim_txt = f"{max_good[0]:.0%}" if max_good else "0%"
            violation_reasons.append(
                f"prompt has no approved (allowlisted) match above threshold (closest {sim_txt})")

    return {
        "ok": not violation_reasons,
        "max_bad": max_bad,
        "max_good": max_good,
        "threshold": threshold,
        "mode": mode,
        "reasons": violation_reasons,
        "reason": "; ".join(violation_reasons) or "ok",
    }


async def validate_prompt(session, messages: list, *, owner_id: Optional[str] = None,
                          model: str = "") -> dict:
    """Gate a request's messages against the exemplar store.

    Errors fail OPEN (a broken exemplar store must not take the gateway down,
    consistent with the guardrail scanner). Returns the compare_prompt verdict
    (with an extra ``{"skipped": ...}`` dict when disabled/unavailable).
    """
    from . import config_service

    try:
        enabled = await config_service.get_setting(session, "PROMPT_VALIDATION_ENABLED", "false")
        if str(enabled).lower() != "true":
            return {"skipped": "disabled", "ok": True}
        raw_thr = await config_service.get_setting(session, "PROMPT_VALIDATION_THRESHOLD", "0.60")
        try:
            threshold = float(raw_thr)
        except (TypeError, ValueError):
            threshold = 0.60
        mode = str(await config_service.get_setting(session, "PROMPT_VALIDATION_MODE", "denylist")).lower()
        if mode not in ("denylist", "allowlist", "both"):
            mode = "denylist"

        exemplars = await load_exemplars(session)
        if not exemplars:
            return {"skipped": "no exemplars", "ok": True}

        prompt = extract_prompt(messages) if not isinstance(messages, str) else messages
        verdict = compare_prompt(prompt or "", exemplars, threshold=threshold, mode=mode)
        verdict["action"] = str(await config_service.get_setting(session, "PROMPT_VALIDATION_ACTION", "flag")).lower()

        if not verdict["ok"]:
            _audit(owner_id, model, verdict)
        return verdict
    except Exception as exc:  # pragma: no cover - defensive; fail open
        logger.warning("prompt validation unavailable, allowing request: %s", exc)
        return {"skipped": "error", "ok": True}


def _audit(owner_id: Optional[str], model: Optional[str], verdict: dict) -> None:
    try:
        from ..audit import get_audit_logger

        get_audit_logger().log("semantic_prompt_validation", owner_id or "unknown", model or "unknown",
                               "warn" if verdict.get("action") == "block" else "info",
                               {"reasons": verdict.get("reasons", []),
                                "threshold": verdict.get("threshold"),
                                "mode": verdict.get("mode"),
                                "action": verdict.get("action"),
                                "max_bad_sim": round(verdict["max_bad"][0], 3) if verdict.get("max_bad") else None,
                                "max_good_sim": round(verdict["max_good"][0], 3) if verdict.get("max_good") else None})
    except Exception:  # pragma: no cover
        logger.debug("prompt validation audit failed", exc_info=True)


async def exemplar_refresh_loop() -> None:
    """Keep the exemplar cache warm even without traffic (started from lifespan)."""
    logger.info("prompt exemplar refresh loop started")
    await asyncio.sleep(10)
    while True:
        try:
            from ..database import get_session_context

            async with get_session_context() as session:
                await load_exemplars(session)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("prompt exemplar refresh failed")
        await asyncio.sleep(_EXEMPLAR_TTL_SECONDS + 5)
