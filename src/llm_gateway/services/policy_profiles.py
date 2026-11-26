"""Per-tenant (per-owner) policy profiles service.

PolicyProfile rows let an owner override global governance defaults:
model allow/deny lists, guardrail/PII switches, a concurrency hint, and an
informational daily budget (surfaced to alerts later -- NOT enforced here).

Model-access semantics deliberately mirror the existing global check in
``proxy_helpers.check_owner_permissions``:

* No model supplied                   -> allow (nothing to match).
* "*" in the allow list               -> allow everything not denied.
* Exact membership in the allow list  -> allow.
* Empty allow list                    -> deny everything not denied.

``denied_models`` additionally supports prefix wildcards (``gpt-4*``) and a
"*" deny-all entry. Deny always wins over allow.

``get_profile`` uses a small in-process TTL cache (60s) with negative
caching, so the proxy hot path only hits the DB once per owner per minute.
Writers MUST call ``invalidate_profile_cache`` after committing changes.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..cache import TTLCache

logger = logging.getLogger("llm_gateway.policy_profiles")

PROFILE_CACHE_TTL_SECONDS = 60.0

# Error codes returned by evaluate_model_access (also used as the proxy
# structured-error code and as PolicyRequest context["policy_reason"]).
CODE_MODEL_DENIED = "model_denied_by_policy_profile"
CODE_MODEL_NOT_ALLOWED = "model_not_allowed_by_policy_profile"

# owner_id -> PolicyProfile dict snapshot | None (None = negative cache entry).
_profile_cache = TTLCache(ttl_seconds=PROFILE_CACHE_TTL_SECONDS, max_size=4096)


def _matches_pattern(entry: str, model: str) -> bool:
    """Wildcard match for a policy-profile model pattern.

    Supports exact names, "*" (matches everything) and prefix wildcards like
    ``gpt-4*``. Comparison is case-insensitive.
    """
    entry = (entry or "").strip().lower()
    if not entry:
        return False
    if entry == "*":
        return True
    if entry.endswith("*"):
        return model.startswith(entry[:-1])
    return entry == model


def _entry_allows(entry: str, model: str) -> bool:
    # Mirrors the global allowed_models semantics (exact or literal '*');
    # prefix wildcards are intentionally NOT honoured on the allow side.
    entry = (entry or "").strip().lower()
    return entry == "*" or entry == model


async def get_profile(session, owner_id: str) -> Optional[Any]:
    """Load the PolicyProfile for an owner (cached 60s, negatives cached too).

    Returns None when the owner has no profile (inherit all global defaults).
    Never raises: on DB failure the caller proceeds with global defaults.
    """
    if not owner_id or session is None:
        return None

    hit, cached = _profile_cache.get(owner_id)
    if hit:
        return cached

    from sqlalchemy import select

    from ..models import PolicyProfile

    profile: Optional[Any] = None
    try:
        result = await session.execute(
            select(PolicyProfile).where(PolicyProfile.owner_id == owner_id)
        )
        row = result.scalars().first()
        if isinstance(row, PolicyProfile):
            # Store a detached snapshot so the cached value stays consistent
            # after the requesting session closes.
            profile = row.model_dump()
    except Exception:  # pragma: no cover - defensive: never break callers
        logger.warning(
            "Failed to load policy profile for owner %s", owner_id, exc_info=True
        )
        return None

    _profile_cache.set(owner_id, profile, ttl=PROFILE_CACHE_TTL_SECONDS)
    return profile


def evaluate_model_access(
    profile: Optional[Any], model: Optional[str]
) -> "tuple[bool, str]":
    """Decide whether ``model`` is usable under ``profile``.

    Returns ``(allowed, code)``; ``code`` is "" when allowed. ``profile`` may
    be a PolicyProfile instance or its dict snapshot (as cached).
    """
    if profile is None:
        return True, ""
    if not model:
        # Nothing to match -- mirror the global behaviour: allow.
        return True, ""

    model_l = model.strip().lower()

    denied: Optional[List[str]] = _get(profile, "denied_models")
    if denied:
        for entry in denied:
            if _matches_pattern(entry, model_l):
                return False, CODE_MODEL_DENIED

    allowed: Optional[List[str]] = _get(profile, "allowed_models")
    if allowed is None:
        # None = inherit global allowed_models -> profile imposes nothing.
        return True, ""
    if any(_entry_allows(entry, model_l) for entry in allowed):
        return True, ""
    return False, CODE_MODEL_NOT_ALLOWED


def resolve_overrides(
    profile: Optional[Any], global_settings: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Resolve effective governance booleans for an owner.

    ``global_settings`` maps keys ``enforce_guardrails`` / ``pii_masking`` to
    the current global booleans. Tri-state profile fields override them;
    None inherits. Non-boolean profile values (daily_budget_usd --
    informational only, max_concurrent_requests) are echoed when set.
    """
    gs = global_settings or {}
    result: Dict[str, Any] = {
        "enforce_guardrails": bool(gs.get("enforce_guardrails", False)),
        "pii_masking": bool(gs.get("pii_masking", False)),
    }
    if profile is None:
        return result

    for key in ("enforce_guardrails", "pii_masking"):
        value = _get(profile, key)
        if value is not None:
            result[key] = bool(value)

    for key in ("daily_budget_usd", "max_concurrent_requests"):
        value = _get(profile, key)
        if value is not None:
            result[key] = value
    return result


def invalidate_profile_cache(owner_id: Optional[str] = None) -> None:
    """Drop cached profile(s). Call after any write to policy_profiles."""
    if owner_id:
        _profile_cache.invalidate(owner_id)
    else:
        _profile_cache.clear()


def _get(profile: Any, key: str) -> Any:
    if isinstance(profile, dict):
        return profile.get(key)
    return getattr(profile, key, None)
