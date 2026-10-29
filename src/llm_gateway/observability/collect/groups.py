"""Model-group keys: which served models count as "the same model" for the collage view.

Normalisation: lowercase, drop the org/ prefix, drop :latest and .gguf, and unify the
_ : and whitespace separators to "-". Explicit overrides (SystemSetting OBS_MODEL_GROUPS,
JSON {group: [glob, ...]}) are matched first.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import re

logger = logging.getLogger(__name__)

OVERRIDES_KEY = "OBS_MODEL_GROUPS"
_SEP_RE = re.compile(r"[_:\s]+")


def normalize(model: str | None) -> str:
    s = (model or "").strip().lower()
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    if s.endswith(":latest"):
        s = s[: -len(":latest")]
    if s.endswith(".gguf"):
        s = s[: -len(".gguf")]
    return _SEP_RE.sub("-", s).strip("-")


def group_key(model: str, overrides: dict[str, list[str]] | None = None) -> str:
    """Group a model name. Overrides are tried first, in dict order, by glob."""
    raw = (model or "").strip()
    if overrides:
        lowered = raw.lower()
        tail = lowered.rsplit("/", 1)[-1]
        norm = normalize(raw)
        for group, patterns in overrides.items():
            for pat in patterns or []:
                p = str(pat).lower()
                if fnmatch.fnmatchcase(lowered, p) or fnmatch.fnmatchcase(tail, p) or fnmatch.fnmatchcase(norm, p):
                    return group
    return normalize(raw)


def parse_overrides(raw: str | None) -> dict[str, list[str]]:
    """Tolerant parse of the stored JSON. Bad input yields {} (logged), never an exception."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("%s is not valid JSON; ignoring", OVERRIDES_KEY)
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for group, pats in data.items():
        if not isinstance(group, str) or not group.strip():
            continue
        if isinstance(pats, str):
            pats = [pats]
        if not isinstance(pats, list):
            continue
        clean = [str(p).strip() for p in pats if str(p).strip()]
        if clean:
            out[group.strip()] = clean
    return out


def validate_overrides(data: dict) -> dict[str, list[str]]:
    """Strict check used by the admin PUT. Raises ValueError with a readable message."""
    out: dict[str, list[str]] = {}
    for group, pats in (data or {}).items():
        g = (group or "").strip()
        if not g or len(g) > 64:
            raise ValueError(f"invalid group name: {group!r}")
        if not isinstance(pats, list) or not all(isinstance(p, str) for p in pats):
            raise ValueError(f"group {g!r} must map to a list of glob strings")
        clean = [p.strip() for p in pats if p.strip()]
        if not clean:
            raise ValueError(f"group {g!r} has no patterns")
        out[g] = clean
    return out


async def load_overrides(session) -> dict[str, list[str]]:
    """Read OBS_MODEL_GROUPS through the shared (30 s cached) settings accessor."""
    from ..services.config_service import config_service

    raw = await config_service.get_setting(session, OVERRIDES_KEY, "")
    return parse_overrides(raw)
