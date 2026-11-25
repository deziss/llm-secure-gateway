"""Agent intelligence: correlate gateway requests into agent runs.

The gateway sees individual LLM calls; agents see runs. We stitch the two
together from (in priority order):

1. Explicit headers: ``x-deziss-run-id``, ``x-deziss-agent``,
   ``x-deziss-parent-agent``, ``x-deziss-session-id`` (sent by deziss-sdk or
   by any client that wants first-class run attribution).
2. Session signals inside the request body: OpenAI-style ``user``,
   ``metadata.session_id``/``metadata.user_id``, or Anthropic/Gemini-style
   ``metadata.user_id``. Combined with owner + detected agent this yields a
   deterministic pseudo-run id (stable for a session, no client changes).
3. Otherwise nothing: unattributed requests are not grouped (no noise).

Steps accumulate in memory (bounded) and are flushed to Postgres by the
observability poller tick, so the request path never blocks on the DB and
monitoring never depends on the model servers (and vice versa).
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger("deziss.agents")

_MAX_QUEUE = 2000
RUN_IDLE_COMPLETE_S = 300  # no steps for 5 min -> run considered completed
MAX_HANDOFFS = 50

_run_headers = ("x-deziss-run-id", "x-deziss-agent", "x-deziss-parent-agent", "x-deziss-session-id")


def _clean(value: Any, maxlen: int = 128) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:maxlen] if value else None


def extract_run_context(headers, body: Any, agent_name: Optional[str] = None,
                        owner_id: Optional[str] = None) -> Optional[dict]:
    """Derive {run_id, agent, parent_agent, session_key} or None. Never raises."""
    try:
        run_id = _clean(headers.get("x-deziss-run-id"), 64)
        agent = _clean(headers.get("x-deziss-agent"), 64) or (agent_name if agent_name and agent_name != "unknown" else None)
        parent = _clean(headers.get("x-deziss-parent-agent"), 64)
        session = _clean(headers.get("x-deziss-session-id"), 128)

        meta: dict = {}
        if isinstance(body, dict):
            user = body.get("user")
            if isinstance(user, str):
                meta["user"] = user
            m = body.get("metadata")
            if isinstance(m, dict):
                meta.update(m)
        session = session or _clean(meta.get("session_id"), 128) or _clean(meta.get("user_id"), 128) or _clean(meta.get("user"), 128)

        if not run_id:
            if not session or not agent:
                return None
            seed = f"{owner_id or 'anon'}|{agent}|{session}"
            run_id = "ses-" + hashlib.sha1(seed.encode("utf-8", "ignore")).hexdigest()[:16]
        if not agent:
            agent = "unknown"
        return {"run_id": run_id, "agent": agent, "parent_agent": parent, "session_key": session}
    except Exception:
        return None


class _RunAgg:
    __slots__ = ("run_id", "agent", "parent_agent", "session_key", "owner_id",
                 "requests", "errors", "tokens_in", "tokens_out", "cost_usd",
                 "first_ts", "last_ts", "handoffs")

    def __init__(self, ctx: dict, owner_id: Optional[str]):
        self.run_id = ctx["run_id"]
        self.agent = ctx.get("agent") or "unknown"
        self.parent_agent = ctx.get("parent_agent")
        self.session_key = ctx.get("session_key")
        self.owner_id = owner_id
        self.requests = 0
        self.errors = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.first_ts = now
        self.last_ts = now
        self.handoffs: list[dict] = []


class AgentRecorder:
    """Batches run/step writes in memory; flushed by the poller tick."""

    def __init__(self):
        self._lock = threading.Lock()
        self._runs: dict[str, _RunAgg] = {}
        self._steps: deque[dict] = deque(maxlen=_MAX_QUEUE)
        self._steps_dropped = 0
        self._seq: dict[str, int] = {}

    def record_llm_step(self, *, run_ctx: dict, model: str, server: Optional[str],
                        owner_id: Optional[str], ok: bool, cancelled: bool,
                        dur_ms: float, ttft_ms: Optional[float], tokens_in: int,
                        tokens_out: int, cost_usd: float, error: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._lock:
            agg = self._runs.get(run_ctx["run_id"])
            if agg is None:
                agg = _RunAgg(run_ctx, owner_id)
                self._runs[agg.run_id] = agg
            elif run_ctx.get("agent") and run_ctx["agent"] != agg.agent:
                # Agent identity evolved mid-run (handoff): record the edge now
                # so handoffs within one flush window are not lost.
                if agg.agent and agg.agent != "unknown":
                    if len(agg.handoffs) < MAX_HANDOFFS:
                        agg.handoffs.append({"from": agg.agent, "to": run_ctx["agent"],
                                             "ts": now.isoformat()})
                agg.parent_agent = agg.parent_agent or run_ctx.get("parent_agent")
                agg.agent = run_ctx["agent"]
            agg.requests += 1
            agg.tokens_in += int(tokens_in or 0)
            agg.tokens_out += int(tokens_out or 0)
            agg.cost_usd += float(cost_usd or 0.0)
            agg.last_ts = now
            if not ok:
                agg.errors += 1
            seq = self._seq.get(agg.run_id, 0) + 1
            self._seq[agg.run_id] = seq
            if len(self._steps) >= self._steps.maxlen - 1:
                self._steps_dropped += 1
            self._steps.append({
                "run_id": agg.run_id, "seq": seq, "ts": now,
                "kind": run_ctx.get("kind", "llm"), "agent": agg.agent,
                "model": (model or "unknown")[:128], "server": (server or None),
                "owner_id": owner_id, "status": "ok" if ok else ("cancelled" if cancelled else "error"),
                "dur_ms": round(float(dur_ms or 0.0), 2),
                "ttft_ms": round(float(ttft_ms), 2) if ttft_ms is not None else None,
                "tokens_in": int(tokens_in or 0), "tokens_out": int(tokens_out or 0),
                "tool": run_ctx.get("tool"),
                "error": (error or None) and str(error)[:256],
            })

    def record_tool_step(self, *, run_ctx: dict, tool: str, ok: bool, dur_ms: float,
                         owner_id: Optional[str] = None, model: str = "tool",
                         error: Optional[str] = None) -> None:
        ctx = {**run_ctx, "kind": "tool", "tool": tool[:128]}
        self.record_llm_step(run_ctx=ctx, model=model, server=None, owner_id=owner_id,
                             ok=ok, cancelled=False, dur_ms=dur_ms, ttft_ms=None,
                             tokens_in=0, tokens_out=0, cost_usd=0.0, error=error)

    def drain(self) -> tuple[list[_RunAgg], list[dict]]:
        with self._lock:
            runs = list(self._runs.values())
            self._runs.clear()
            steps = list(self._steps)
            self._steps.clear()
            self._seq.clear()
            return runs, steps



_recorder = AgentRecorder()


def get_recorder() -> AgentRecorder:
    return _recorder


def record_llm_step(**kwargs) -> None:
    """Fire-and-forget entry point used from RequestTracker.finish()."""
    try:
        _recorder.record_llm_step(**kwargs)
    except Exception:  # pragma: no cover - must never break the request path
        logger.debug("agents: record_llm_step failed", exc_info=True)


def run_status(agg: _RunAgg) -> str:
    if agg.errors and agg.errors >= max(1, agg.requests // 2):
        return "failed"
    idle = (datetime.now(timezone.utc).replace(tzinfo=None) - agg.last_ts).total_seconds()
    return "active" if idle < RUN_IDLE_COMPLETE_S else "completed"


async def flush_agent_records() -> int:
    """Persist queued runs/steps. Called by the poller tick. Returns rows written."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlmodel import select

    from ..database import get_session_context
    from ..models import AgentRun, AgentStep

    runs, steps = _recorder.drain()
    if not runs and not steps:
        return 0
    written = 0
    async with get_session_context() as session:
        bind = session.get_bind()
        sqlite = "sqlite" in getattr(getattr(bind, "dialect", None), "name", "")
        ins = (sqlite_insert if sqlite else pg_insert)
        for agg in runs:
            existing = await session.get(AgentRun, agg.run_id)
            parent = agg.parent_agent
            if existing is not None:
                already = bool(agg.handoffs) and agg.handoffs[-1].get("to") == agg.agent
                if not already and agg.agent and existing.agent and agg.agent != existing.agent \
                        and len(existing.handoffs or []) + len(agg.handoffs) < MAX_HANDOFFS:
                    agg.handoffs.insert(0, {"from": existing.agent, "to": agg.agent,
                                            "ts": agg.first_ts.isoformat()})
                agg.first_ts = min(existing.first_ts, agg.first_ts)
                agg.requests += existing.requests
                agg.errors += existing.errors
                agg.tokens_in += existing.tokens_in
                agg.tokens_out += existing.tokens_out
                agg.cost_usd += existing.cost_usd
            else:
                if parent is None and agg.session_key:
                    # Find sibling runs in same session -> parent chain.
                    sib = (await session.execute(
                        select(AgentRun.agent).where(AgentRun.session_key == agg.session_key)
                        .order_by(AgentRun.last_ts.desc()).limit(1)
                    )).scalars().first()
                    parent = sib if sib and sib != agg.agent else None
            values = {
                "run_id": agg.run_id, "agent": agg.agent, "parent_agent": parent,
                "session_key": agg.session_key, "owner_id": agg.owner_id,
                "status": run_status(agg), "requests": agg.requests, "errors": agg.errors,
                "tokens_in": agg.tokens_in, "tokens_out": agg.tokens_out,
                "cost_usd": agg.cost_usd, "first_ts": agg.first_ts, "last_ts": agg.last_ts,
                "handoffs": agg.handoffs,
            }
            stmt = ins(AgentRun).values(**values)
            upd = {k: v for k, v in values.items() if k not in ("run_id", "first_ts")}
            upd["handoffs"] = stmt.excluded.handoffs
            await session.execute(stmt.on_conflict_do_update(
                index_elements=["run_id"], set_=upd))
            written += 1
        if steps:
            # seq collisions across flushes: renumber using max(seq) per run.
            max_seq: dict[str, int] = {}
            run_ids = {s["run_id"] for s in steps}
            rows = (await session.execute(
                select(AgentStep.run_id, AgentStep.seq).where(AgentStep.run_id.in_(run_ids))
            )).all()
            for rid, sq in rows:
                max_seq[rid] = max(max_seq.get(rid, 0), sq)
            step_vals = []
            for s in steps:
                offset = max_seq.get(s["run_id"], 0)
                if offset:
                    s = dict(s)
                    s["seq"] = offset + s["seq"]
                step_vals.append(s)
            await session.execute(ins(AgentStep).values(step_vals))
            written += len(step_vals)
        await session.commit()
    return written


async def agent_run_queries(limit: int = 50, agent: Optional[str] = None,
                            status: Optional[str] = None) -> list[dict]:
    from sqlmodel import col, select

    from ..database import get_session_context
    from ..models import AgentRun

    async with get_session_context() as session:
        stmt = select(AgentRun)
        if agent:
            stmt = stmt.where(col(AgentRun.agent).ilike(f"%{agent}%"))
        if status:
            stmt = stmt.where(AgentRun.status == status)
        stmt = stmt.order_by(col(AgentRun.last_ts).desc()).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
    return [_run_dict(r) for r in rows]


async def agent_run_detail(run_id: str) -> Optional[dict]:
    from sqlmodel import col, select

    from ..database import get_session_context
    from ..models import AgentRun, AgentStep

    async with get_session_context() as session:
        run = await session.get(AgentRun, run_id)
        if run is None:
            return None
        steps = (await session.execute(
            select(AgentStep).where(AgentStep.run_id == run_id)
            .order_by(col(AgentStep.ts), col(AgentStep.seq))
        )).scalars().all()
    return {"run": _run_dict(run),
            "steps": [{
                "seq": s.seq, "ts": s.ts.isoformat() if s.ts else None, "kind": s.kind,
                "agent": s.agent, "model": s.model, "server": s.server, "status": s.status,
                "dur_ms": s.dur_ms, "ttft_ms": s.ttft_ms, "tokens_in": s.tokens_in,
                "tokens_out": s.tokens_out, "tool": s.tool, "error": s.error,
            } for s in steps]}


async def agents_summary(hours: int = 1) -> dict:
    from sqlalchemy import func
    from sqlmodel import select

    from ..database import get_session_context
    from ..models import AgentRun

    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
    async with get_session_context() as session:
        active = (await session.execute(
            select(func.count()).select_from(AgentRun).where(AgentRun.status == "active")
        )).scalar() or 0
        rows = (await session.execute(
            select(func.count(), func.coalesce(func.sum(AgentRun.errors), 0),
                   func.coalesce(func.sum(AgentRun.tokens_in + AgentRun.tokens_out), 0),
                   func.coalesce(func.sum(AgentRun.cost_usd), 0.0), AgentRun.agent)
            .where(AgentRun.last_ts >= since).group_by(AgentRun.agent)
        )).all()
        total = (await session.execute(
            select(func.count()).select_from(AgentRun).where(AgentRun.last_ts >= since)
        )).scalar() or 0
    return {
        "active_runs": int(active),
        "runs_1h": int(total),
        "agents": [{"agent": r[4], "runs_1h": int(r[0]), "errors_1h": int(r[1]),
                    "tokens_1h": int(r[2]), "cost_1h_usd": float(r[3])} for r in rows],
    }


def _run_dict(r) -> dict:
    return {
        "run_id": r.run_id, "agent": r.agent, "parent_agent": r.parent_agent,
        "session_key": r.session_key, "owner_id": r.owner_id, "status": r.status,
        "requests": r.requests, "errors": r.errors, "tokens_in": r.tokens_in,
        "tokens_out": r.tokens_out, "cost_usd": float(r.cost_usd),
        "first_ts": r.first_ts.isoformat() if r.first_ts else None,
        "last_ts": r.last_ts.isoformat() if r.last_ts else None,
        "handoffs": list(r.handoffs or []),
    }
