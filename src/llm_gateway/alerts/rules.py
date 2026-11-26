"""Alert rule definitions and defaults.

An AlertRule is a threshold rule evaluated over in-memory time series that
the engine builds from the observability poller snapshot and the live-stats
store. Rules are stored in the ``alert_rules`` table; ``ensure_default_rules``
seeds the user-specified starting policy set on first boot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# metric -> (target_kind, unit, human description)
METRIC_REGISTRY: dict[str, tuple[str, str, str]] = {
    "kv_cache_pct":         ("model", "%", "vLLM KV-cache utilisation"),
    "waiting":              ("model", "requests", "Requests waiting in the inference queue"),
    "gpu_memory_pct":       ("gpu", "%", "GPU memory used"),
    "gpu_temp_c":           ("gpu", "°C", "GPU temperature"),
    "gpu_xid":              ("gpu", "errors", "New GPU XID errors (requires DCGM)"),
    "gpu_ecc_dbe":          ("gpu", "errors", "Cumulative double-bit ECC errors (requires DCGM)"),
    "backend_down":         ("server", "bool", "Model server unreachable"),
    "server_error_rate":    ("server", "%", "Gateway-observed error rate per server"),
    "server_avg_latency_ms": ("server", "ms", "Average TTFT observed through the gateway"),
    "gateway_tps_drop":     ("gateway", "%", "Gateway throughput drop vs 15-min baseline"),
    "gateway_error_rate":   ("gateway", "%", "Overall gateway error rate"),
}

COMPARATORS = (">=", "<=", "drop_pct")
SEVERITIES = ("info", "warning", "critical")


@dataclass
class AlertRule:
    """Runtime view of one alert rule (mirrors the ``alert_rules`` table)."""

    id: str
    name: str
    metric: str
    comparator: str = ">="
    threshold: float = 95.0
    window_s: int = 60
    severity: str = "warning"
    cooldown_s: int = 300
    target_kind: str = "gateway"
    enabled: bool = True
    channels: list[str] = field(default_factory=lambda: ["webhook"])
    target_filter: list[str] = field(default_factory=list)
    description: str = ""

    @property
    def horizon_s(self) -> int:
        """How much history the evaluator must retain for this rule."""
        return max(self.window_s, 900 if self.comparator == "drop_pct" else 0) + 120


def default_rules() -> list[AlertRule]:
    """The starting policy set (proposed thresholds, not hardware safety limits)."""
    R = AlertRule
    return [
        R("gpu_memory_critical", "GPU memory critical", "gpu_memory_pct", ">=", 95, 30,
          "critical", 600, "gpu", True, ["webhook"],
          description="Sustained >=95% GPU memory for 30 s; new allocations will fail."),
        R("gpu_memory_warning", "GPU memory warning", "gpu_memory_pct", ">=", 85, 120,
          "warning", 900, "gpu", True, ["webhook"]),
        R("kv_cache_saturated", "KV cache saturation", "kv_cache_pct", ">=", 95, 60,
          "critical", 300, "model", True, ["webhook"]),
        R("inference_queue_high", "Inference queue too long", "waiting", ">=", 32, 60,
          "warning", 300, "model", True, ["webhook"]),
        R("server_down", "Model server unreachable", "backend_down", ">=", 0.5, 30,
          "critical", 120, "server", True, ["webhook"]),
        R("server_error_rate", "Server error rate high", "server_error_rate", ">=", 25, 120,
          "warning", 600, "server", True, ["webhook"]),
        R("gateway_error_rate", "Gateway error rate high", "gateway_error_rate", ">=", 10, 120,
          "critical", 600, "gateway", True, ["webhook"]),
        R("gateway_tps_drop", "Gateway throughput collapsed", "gateway_tps_drop", "drop_pct", 70,
          120, "warning", 600, "gateway", True, ["webhook"]),
        # gpu XID/ECC need DCGM extended telemetry (DCGM_FI_DEV_XID_ERRORS / ECC_DBE),
        # which the dcgm parser collects; gpu temp works from every exporter that reports temp.
        R("gpu_temp_high", "GPU temperature high", "gpu_temp_c", ">=", 90, 60,
          "warning", 600, "gpu", True, ["webhook"]),
        R("gpu_xid_error", "GPU XID error", "gpu_xid", ">=", 1, 0,
          "critical", 120, "gpu", True, ["webhook"]),
        R("gpu_ecc_dbe_error", "GPU double-bit ECC error", "gpu_ecc_dbe", ">=", 1, 60,
          "critical", 3600, "gpu", True, ["webhook"]),
    ]


async def ensure_default_rules(session) -> int:
    """Insert any default rule that does not exist yet. Returns count added."""
    from sqlmodel import select

    from ..models import AlertRule as AlertRuleRow

    existing = set((await session.execute(select(AlertRuleRow.id))).scalars().all())
    added = 0
    for r in default_rules():
        if r.id in existing:
            continue
        session.add(AlertRuleRow(
            id=r.id, name=r.name, metric=r.metric, comparator=r.comparator,
            threshold=r.threshold, window_s=r.window_s, severity=r.severity,
            cooldown_s=r.cooldown_s, target_kind=r.target_kind, enabled=r.enabled,
            channels=list(r.channels), target_filter=list(r.target_filter),
            description=r.description or None,
        ))
        added += 1
    if added:
        await session.commit()
    return added


def _from_row(row) -> AlertRule:
    return AlertRule(
        id=row.id, name=row.name, metric=row.metric, comparator=row.comparator,
        threshold=float(row.threshold), window_s=int(row.window_s), severity=row.severity,
        cooldown_s=int(row.cooldown_s), target_kind=row.target_kind,
        enabled=bool(row.enabled), channels=list(row.channels or []),
        target_filter=[str(x) for x in (getattr(row, "target_filter", None) or [])],
        description=str(getattr(row, "description", None) or ""),
    )


async def load_rules(session, include_disabled: bool = False) -> list[AlertRule]:
    from sqlmodel import select

    from ..models import AlertRule as AlertRuleRow

    stmt = select(AlertRuleRow)
    if not include_disabled:
        stmt = stmt.where(AlertRuleRow.enabled == True)  # noqa: E712
    rows = (await session.execute(stmt)).scalars().all()
    return [_from_row(r) for r in rows]


def to_dict(r: AlertRule) -> dict:
    return {
        "id": r.id, "name": r.name, "metric": r.metric, "comparator": r.comparator,
        "threshold": r.threshold, "window_s": r.window_s, "severity": r.severity,
        "cooldown_s": r.cooldown_s, "target_kind": r.target_kind, "enabled": r.enabled,
        "channels": list(r.channels), "target_filter": list(r.target_filter),
        "description": r.description,
    }


DEFAULT_RULE_IDS = frozenset(r.id for r in default_rules())


def is_default_rule(rule_id: str) -> bool:
    """Built-in seeded rules cannot be deleted, only edited/toggled."""
    return rule_id in DEFAULT_RULE_IDS


def make_custom_rule_id(name: str) -> str:
    import hashlib
    import re
    import time

    base = re.sub(r"[^a-z0-9]+", "-", (name or "rule").lower()).strip("-")[:40] or "rule"
    digest = hashlib.sha1(f"{name}|{time.time()}".encode()).hexdigest()[:8]
    return f"c-{base}-{digest}"


def normalize_filters(patterns: list[str] | None) -> list[str]:
    """Trim, dedupe, clamp per-pattern length; drop empties."""
    out: list[str] = []
    for p in patterns or []:
        p = str(p).strip()[:128]
        if p and p not in out:
            out.append(p)
    return out[:20]


def target_matches(patterns: list[str] | None, target: str) -> bool:
    """Glob match (fnmatch) against a target id; empty patterns match everything.

    A pattern with no wildcard that equals a target's prefix (e.g. "wardha"
    matching "wardha/gpu0" or "wardha:llama3") also matches, so operators can
    filter by host/server name without remembering the * syntax.
    """
    from fnmatch import fnmatch

    pats = normalize_filters(patterns)
    if not pats:
        return True
    for p in pats:
        if fnmatch(target, p):
            return True
        if "*" not in p and "?" not in p:
            if target.startswith(p + "/") or target.startswith(p + ":"):
                return True
    return False
