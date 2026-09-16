"""Policy engine with hierarchical scopes and DB-configurable endpoint rules.

Scope format:  ``namespace:action``  (e.g. ``llm:chat``, ``llm:embed``)
Wildcards:     ``llm:*`` grants all ``llm:`` actions, ``*`` grants everything.

Endpoint-to-scope mapping is loaded from SystemSetting (key=SCOPE_RULES)
with a 30s cache.  If no DB rule is found, the built-in DEFAULT_RULES apply.
Admins can add/override rules in Settings without redeploying.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from .cache import TTLCache

logger = logging.getLogger(__name__)

# ── Built-in defaults (used when no DB override exists) ──────────────────
DEFAULT_RULES: list[tuple[str, str]] = [
    ("/api/chat",            "llm:chat"),
    ("/v1/chat/completions", "llm:chat"),
    ("/v1/completions",      "llm:chat"),
    ("/api/generate",        "llm:chat"),
    ("/api/embeddings",      "llm:embed"),
    ("/v1/embeddings",       "llm:embed"),
    ("/api/tags",            "llm:read"),
    ("/v1/models",           "llm:read"),
    ("/api/ps",              "llm:read"),
]

# Backward-compatible aliases so old keys with flat scopes still work.
_LEGACY_ALIASES: dict[str, str] = {
    "chat":       "llm:chat",
    "embeddings": "llm:embed",
    "read_only":  "llm:read",
    "admin":      "admin:*",
}

# Cache for the merged rule set (DB + defaults).
_rules_cache = TTLCache(ttl_seconds=30.0, max_size=1)


# ── Hierarchical scope matching ──────────────────────────────────────────

def _normalize_scopes(raw: list[str]) -> set[str]:
    """Expand legacy flat scopes into hierarchical equivalents."""
    out: set[str] = set()
    for s in raw:
        out.add(_LEGACY_ALIASES.get(s, s))
    return out


def scope_matches(held: set[str], required: str) -> bool:
    """Check if any held scope satisfies the required scope.

    Rules:
        "*"          matches everything
        "llm:*"      matches "llm:chat", "llm:embed", "llm:read", etc.
        "llm:chat"   matches "llm:chat" exactly
        "admin:*"    matches "admin:backends", "admin:users", etc.
    """
    if "*" in held:
        return True
    if required in held:
        return True
    # Check namespace wildcards:  "llm:*" covers "llm:chat"
    ns = required.split(":")[0] if ":" in required else ""
    if ns and f"{ns}:*" in held:
        return True
    return False


# ── Rule loading (DB with code fallback) ─────────────────────────────────

def _parse_rules_json(raw: str) -> list[tuple[str, str]]:
    """Parse a JSON array of [prefix, scope] pairs from SystemSetting."""
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [(p, s) for p, s in data if isinstance(p, str) and isinstance(s, str)]
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Invalid SCOPE_RULES JSON, using defaults: %s", exc)
    return []


async def _load_rules(session) -> list[tuple[str, str]]:
    """Load endpoint-scope rules: DB overrides merged on top of defaults."""
    hit, cached = _rules_cache.get("rules")
    if hit:
        return cached

    rules = list(DEFAULT_RULES)  # start with built-in defaults

    if session is not None:
        try:
            from .models import SystemSetting
            from sqlmodel import select
            result = await session.execute(
                select(SystemSetting).where(SystemSetting.key == "SCOPE_RULES")
            )
            setting = result.scalars().first()
            if setting and setting.value:
                db_rules = _parse_rules_json(setting.value)
                if db_rules:
                    # DB rules take priority — prepend so prefix match hits them first
                    rules = db_rules + rules
        except Exception as exc:
            logger.warning("Failed to load SCOPE_RULES from DB: %s", exc)

    _rules_cache.set("rules", rules)
    return rules


def required_scope_for(path: str, rules: list[tuple[str, str]]) -> str:
    """Return the scope required to access the given endpoint path."""
    normalized = "/" + path.lstrip("/")
    for prefix, scope in rules:
        if normalized.startswith(prefix):
            return scope
    return "llm:chat"  # safe default for unmapped proxy paths


# ── Policy engine ────────────────────────────────────────────────────────

class PolicyRequest(BaseModel):
    user: Any
    action: str
    resource: str
    context: Dict[str, Any] = {}


class PolicyEngine:
    async def evaluate(self, request: PolicyRequest, session=None) -> bool:
        user = request.user

        # API Key Logic
        if hasattr(user, "scopes"):
            raw_scopes = getattr(user, "scopes", None) or []
            held = _normalize_scopes(raw_scopes)

            rules = await _load_rules(session)
            required = required_scope_for(request.resource, rules)

            if not scope_matches(held, required):
                return False

        # SPIFFE Logic
        if isinstance(user, dict) and user.get("type") == "workload":
            return True

        return True


policy_engine = PolicyEngine()


def get_policy_engine() -> PolicyEngine:
    return policy_engine
