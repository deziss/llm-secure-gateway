"""Incident persistence: dedupe, cooldown, acknowledgement, recovery.

An *incident* is one open (rule, target) episode. New findings for an already
open episode are suppressed (grouping/dedup). Notifications honour per-rule
cooldowns. When a resolve finding arrives the incident closes and a recovery
notification is sent.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import select

from ..database import get_session_context
from ..models import AlertIncident, _utcnow
from .engine import AlertSeries, Finding
from .rules import ensure_default_rules

logger = logging.getLogger("deziss.alerts.incidents")

_series = AlertSeries()


def get_series() -> AlertSeries:
    return _series


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


async def open_firing_map() -> dict[str, set]:
    """rule_id -> set(targets) currently firing (drives evaluate() hysteresis)."""
    async with get_session_context() as session:
        rows = (await session.execute(
            select(AlertIncident).where(AlertIncident.state == "firing")
        )).scalars().all()
    out: dict[str, set] = {}
    for r in rows:
        out.setdefault(r.rule_id, set()).add(r.target)
    return out


async def apply_findings(findings: list[Finding], notify: bool = True) -> dict:
    """Persist findings -> incidents; dispatch notifications. Returns counters."""
    from . import notify as notify_mod

    stats = {"fired": 0, "resolved": 0, "suppressed": 0, "notified": 0}
    if not findings:
        return stats
    async with get_session_context() as session:
        await ensure_default_rules(session)
        firing = {
            (r.rule_id, r.target): r
            for r in (await session.execute(
                select(AlertIncident).where(AlertIncident.state == "firing")
            )).scalars().all()
        }
        now = _utcnow()
        for f in findings:
            key = (f.rule_id, f.target)
            inc = firing.get(key)
            if f.resolve:
                if inc is not None:
                    inc.state = "resolved"
                    inc.resolved_at = now
                    inc.last_notified_at = now
                    inc.detail = f.detail or {"summary": f.summary}
                    session.add(inc)
                    stats["resolved"] += 1
                    if notify:
                        if await notify_mod.notify_recovery(inc, f):
                            stats["notified"] += 1
                continue
            if inc is not None:
                # Dedupe: episode already open -> update detail only, no notification.
                inc.value = f.value
                inc.summary = f.summary
                inc.detail = f.detail
                session.add(inc)
                stats["suppressed"] += 1
                continue
            inc = AlertIncident(
                rule_id=f.rule_id, rule_name=f.rule_name, metric=f.metric,
                target=f.target, severity=f.severity, state="firing",
                started_at=now, summary=f.summary, detail=f.detail or {},
                value=f.value, notified_count=0,
            )
            session.add(inc)
            await session.flush()
            stats["fired"] += 1
            if notify:
                if await notify_mod.notify_firing(inc):
                    inc.notified_count += 1
                    inc.last_notified_at = now
                    session.add(inc)
                    stats["notified"] += 1
                firing[key] = inc
        await session.commit()
    return stats


async def collect_samples(now: float) -> None:
    """Build this tick's samples into the shared AlertSeries.

    Sources: observability poller snapshot (servers, GPU hosts) and the
    LiveStats store (gateway/server error rate, TTFT, throughput).
    """
    from ..observability.collect.poller import get_snapshot
    from ..observability.store import get_live_stats

    snap = get_snapshot()
    for s in snap.get("servers", []):
        name = s.get("name")
        if not name:
            continue
        # backend_down: 1 unreachable, 0 reachable-ish
        status = s.get("status")
        down = 1.0 if status in ("unreachable",) else 0.0
        _series.add("backend_down", name, down, now)
        waiting = s.get("waiting")
        if waiting is not None:
            _series.add("waiting", name, float(waiting), now)
        kv = s.get("kv_cache_pct")
        if kv is not None:
            _series.add("kv_cache_pct", name, float(kv), now)
        for model, mstat in (s.get("model_stats") or {}).items():
            tgt = f"{name}:{model}"
            if mstat.get("waiting") is not None:
                _series.add("waiting", tgt, float(mstat["waiting"]), now)
            if mstat.get("kv_cache_pct") is not None:
                _series.add("kv_cache_pct", tgt, float(mstat["kv_cache_pct"]), now)
    for h in snap.get("gpu_hosts", []):
        if h.get("status") != "ok":
            continue
        host = h.get("name", "?")
        for g in h.get("gpus", []) or []:
            _add_gpu_samples(g, now, host=host)

    # Gateway + per-server views from LiveStats (60 s window).
    try:
        rows = get_live_stats().snapshot(now=now)
    except Exception:  # pragma: no cover - defensive
        rows = []
    tot_req = tot_err = 0
    for r in rows:
        req = r.get("requests_60s") or 0
        err = r.get("errors_60s") or 0
        srv, model = r.get("server") or "?", r.get("model") or "?"
        tot_req += req
        tot_err += err
        if req:
            _series.add("server_error_rate", srv, 100.0 * err / req, now)
            _series.add("server_error_rate", f"{srv}:{model}", 100.0 * err / req, now)
        ttft = r.get("ttft_p50_ms")
        if ttft is not None:
            _series.add("server_avg_latency_ms", srv, float(ttft), now)
        tps = (r.get("tok_s_throughput") or 0) + (r.get("rps_60s") or 0)
        _series.add("gateway_tps_drop", f"{srv}:{model}", float(tps), now)
    if tot_req:
        _series.add("gateway_error_rate", "gateway", 100.0 * tot_err / tot_req, now)
    # Aggregate throughput target for a global collapse rule.
    _series.add("gateway_tps_drop", "gateway", _agg_tps_from(rows), now)
    _series.prune(now)


def _agg_tps_from(rows: list[dict]) -> float:
    total = 0.0
    for r in rows:
        total += (r.get("tok_s_throughput") or 0.0)
    return float(total)


def _add_gpu_samples(g: dict, now: float, host: str = "") -> None:
    idx = g.get("index", "?")
    tgt = f"{host}/gpu{idx}" if host else f"gpu{idx}"
    used, total = g.get("mem_used_mib"), g.get("mem_total_mib")
    if used is not None and total:
        _series.add("gpu_memory_pct", tgt, 100.0 * used / total, now)
    if g.get("temp_c") is not None:
        _series.add("gpu_temp_c", tgt, float(g["temp_c"]), now)
    xid = g.get("xid_error")
    if xid is not None:
        _series.add("gpu_xid", tgt, float(xid), now)
    ecc = g.get("ecc_dbe")
    if ecc is not None:
        _series.add("gpu_ecc_dbe", tgt, float(ecc), now)


async def alert_tick() -> dict:
    """One engine tick: collect -> evaluate -> persist/notify. Never raises."""
    import time as _t

    now = _t.time()
    try:
        await collect_samples(now)
        firing = await open_firing_map()
        from .engine import run_alert_tick

        findings = await run_alert_tick(_series, now, open_firing=firing)
        return await apply_findings(findings)
    except Exception:
        logger.exception("alerts: tick failed")
        return {"error": 1}
