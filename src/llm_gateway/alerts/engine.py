"""Pure rule evaluator over in-memory AlertSeries.

No database, no I/O: ``evaluate(rules, series, now)`` returns findings.
The poller tick builds an :class:`AlertSeries` from the observability
snapshot + live-stats store, calls evaluate, and hands findings to
:mod:`alerts.incidents` for dedupe/persistence/notification.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .rules import AlertRule

logger = logging.getLogger("deziss.alerts.engine")

# Hysteresis: to CLEAR a firing condition the value must come back 5 % of the
# threshold below it. Prevents flapping around the boundary.
HYSTERESIS_FRAC = 0.05


@dataclass
class Finding:
    rule_id: str
    rule_name: str
    metric: str
    target: str
    severity: str
    value: float
    threshold: float
    comparator: str
    summary: str
    detail: dict = field(default_factory=dict)
    resolve: bool = False  # True => condition cleared; close any open incident


class AlertSeries:
    """Ring-buffer samples: ``samples[target] = [(epoch_s, value), ...]``."""

    def __init__(self, ttl_s: int = 3600):
        self._samples: dict[str, list[tuple[float, float]]] = {}
        self._ttl = ttl_s

    def add(self, metric: str, target: str, value: float, ts: float) -> None:
        key = f"{metric}|{target}"
        samples = self._samples.setdefault(key, [])
        samples.append((ts, float(value)))
        cutoff = ts - self._ttl
        if len(samples) > 2 and samples[0][0] < cutoff:
            del samples[0]

    def series(self, metric: str, target: str) -> list[tuple[float, float]]:
        return self._samples.get(f"{metric}|{target}", [])

    def prune(self, ts: float) -> None:
        cutoff = ts - self._ttl
        for key, samples in list(self._samples.items()):
            kept = [p for p in samples if p[0] >= cutoff]
            if kept:
                self._samples[key] = kept
            else:
                del self._samples[key]

    def targets(self, metric: str) -> list[str]:
        prefix = f"{metric}|"
        return [k[len(prefix):] for k in self._samples if k.startswith(prefix)]


def _baseline(samples: list[tuple[float, float]], now: float, span_s: int = 900,
              min_s: int = 300) -> Optional[float]:
    """Mean over [now-span, now] requiring at least min_s of coverage."""
    lo = now - span_s
    pts = [v for t, v in samples if t >= lo]
    ts = [t for t, _ in samples if t >= lo]
    if len(pts) < 3 or (max(ts) - min(ts)) < min_s:
        return None
    return sum(pts) / len(pts)


def evaluate(rules: list[AlertRule], series: AlertSeries, now: float,
             open_firing: Optional[dict[str, set]] = None) -> list[Finding]:
    """Evaluate all rules against collected samples.

    A firing finding is emitted whenever the condition is currently true: the
    newest sample breaches the threshold AND every sample of the evaluation
    window breaches it and the window actually spans ``window_s``. Recovery
    findings (``resolve=True)`` are emitted only for currently-open targets
    (``open_firing``) once the value is comfortably back inside (hysteresis).
    Dedupe/cooldown/notification live in incidents.py.
    """
    findings: list[Finding] = []
    open_firing = open_firing or {}
    for rule in rules:
        for target in series.targets(rule.metric):
            samples = series.series(rule.metric, target)
            if not samples:
                continue
            newest_t, newest_v = samples[-1]
            # Stale series (target stopped reporting) => treat as resolved.
            if now - newest_t > max(3 * 30, rule.window_s + 90):
                if target in open_firing.get(rule.id, set()):
                    findings.append(Finding(
                        rule_id=rule.id, rule_name=rule.name, metric=rule.metric,
                        target=target, severity=rule.severity, value=newest_v,
                        threshold=rule.threshold, comparator=rule.comparator,
                        summary=f"{rule.name}: {target} stopped reporting",
                        resolve=True, detail={"reason": "stale"},
                    ))
                continue

            if rule.comparator == "drop_pct":
                base = _baseline(samples, now - max(rule.window_s, 60))
                if base is None or base <= 0:
                    continue
                recent = [v for t, v in samples if t >= now - max(rule.window_s, 60)]
                if not recent:
                    continue
                cur = sum(recent) / len(recent)
                drop = (base - cur) / base * 100.0
                if drop >= rule.threshold and newest_v is not None:
                    findings.append(Finding(
                        rule_id=rule.id, rule_name=rule.name, metric=rule.metric,
                        target=target, severity=rule.severity, value=round(drop, 1),
                        threshold=rule.threshold, comparator="drop_pct",
                        summary=(f"{rule.name}: {target} throughput down {drop:.0f}% "
                                 f"vs baseline ({cur:.1f} now vs {base:.1f})"),
                        detail={"baseline": round(base, 3), "current": round(cur, 3)},
                    ))
                elif drop < rule.threshold * (1 - HYSTERESIS_FRAC):
                    findings.append(Finding(
                        rule_id=rule.id, rule_name=rule.name, metric=rule.metric,
                        target=target, severity=rule.severity, value=round(drop, 1),
                        threshold=rule.threshold, comparator="drop_pct",
                        summary=f"{rule.name}: {target} recovered ({drop:.0f}% drop)",
                        resolve=True,
                    ))
                continue

            # >= / <= threshold with duration window (all samples in window breach)
            window = max(rule.window_s, 30)
            in_win = [(t, v) for t, v in samples if t >= now - window]
            if not in_win:
                continue
            if rule.comparator == "<=":
                breached = all(v <= rule.threshold for _t, v in in_win)
                now_bad = newest_v <= rule.threshold
                ok_frac = rule.threshold * (1 + HYSTERESIS_FRAC)
            else:
                breached = all(v >= rule.threshold for _t, v in in_win)
                now_bad = newest_v >= rule.threshold
                ok_frac = rule.threshold * (1 - HYSTERESIS_FRAC)
            spanned = (in_win[-1][0] - in_win[0][0]) >= min(window - 15, window * 0.66)
            if rule.window_s <= 0 and rule.comparator == ">=":
                # Event-style metric (e.g. gpu_xid): the breach is transient, so fire on the
                # instant sample instead of requiring the condition to span an evaluation window.
                if now_bad:
                    findings.append(Finding(
                        rule_id=rule.id, rule_name=rule.name, metric=rule.metric,
                        target=target, severity=rule.severity, value=round(newest_v, 3),
                        threshold=rule.threshold, comparator=rule.comparator,
                        summary=f"{rule.name}: {target} at {newest_v:g} {rule.comparator} {rule.threshold:g}",
                        detail={"event": True},
                    ))
                elif target in open_firing.get(rule.id, set()):
                    findings.append(Finding(
                        rule_id=rule.id, rule_name=rule.name, metric=rule.metric,
                        target=target, severity=rule.severity, value=round(newest_v, 3),
                        threshold=rule.threshold, comparator=rule.comparator,
                        summary=f"{rule.name}: {target} recovered",
                        resolve=True,
                    ))
                continue
            if now_bad and breached and spanned:
                findings.append(Finding(
                    rule_id=rule.id, rule_name=rule.name, metric=rule.metric,
                    target=target, severity=rule.severity, value=round(newest_v, 3),
                    threshold=rule.threshold, comparator=rule.comparator,
                    summary=(f"{rule.name}: {target} at {newest_v:.1f} "
                             f"{rule.comparator} {rule.threshold:g} for {window}s"),
                    detail={"window_s": window, "samples": len(in_win)},
                ))
            elif target in open_firing.get(rule.id, set()) and not now_bad:
                # Hysteresis for recovery: only clear when comfortably back inside.
                recovered = newest_v >= ok_frac if rule.comparator == "<=" else newest_v <= ok_frac
                if recovered:
                    findings.append(Finding(
                        rule_id=rule.id, rule_name=rule.name, metric=rule.metric,
                        target=target, severity=rule.severity, value=round(newest_v, 3),
                        threshold=rule.threshold, comparator=rule.comparator,
                        summary=f"{rule.name}: {target} recovered ({newest_v:.1f})",
                        resolve=True,
                    ))
    return findings


async def run_alert_tick(serie: AlertSeries, now: float,
                         open_firing: Optional[dict[str, set]] = None) -> list[Finding]:
    """Load enabled rules and evaluate; used by the poller glue (see incidents.py)."""
    from ..database import get_session_context
    from .rules import load_rules

    async with get_session_context() as session:
        rules = await load_rules(session)
    return evaluate(rules, serie, now, open_firing)
