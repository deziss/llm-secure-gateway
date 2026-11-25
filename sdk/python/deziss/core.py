"""Core of the DEZISS SDK: run contexts + header injection.

The gateway derives everything from four headers; this module just makes them
easy to carry correctly through nested agent code. No network calls, no deps.
"""
from __future__ import annotations

import contextlib
import contextvars
import os
import uuid
from dataclasses import dataclass, field
from typing import Iterator, Optional

_current: contextvars.ContextVar[Optional["DezissRun"]] = contextvars.ContextVar(
    "deziss_current_run", default=None
)


def new_run_id() -> str:
    return "run-" + uuid.uuid4().hex[:16]


@dataclass
class DezissRun:
    """An agent run: one workflow that may involve many LLM calls and handoffs."""

    run_id: str
    agent: str
    session_id: Optional[str] = None
    parent_agent: Optional[str] = None
    history: list[dict] = field(default_factory=list)

    @property
    def headers(self) -> dict[str, str]:
        h = {"x-deziss-run-id": self.run_id, "x-deziss-agent": self.agent}
        if self.parent_agent:
            h["x-deziss-parent-agent"] = self.parent_agent
        if self.session_id:
            h["x-deziss-session-id"] = self.session_id
        return h

    # Mapping duck-typing so OpenAI-style `extra_headers=run` works too.
    def keys(self):
        return self.headers.keys()

    def items(self):
        return self.headers.items()

    def __getitem__(self, k: str) -> str:
        return self.headers[k]

    def handoff(self, to_agent: str) -> "DezissRun":
        """Continue this run as another agent (the gateway records the edge)."""
        self.history.append({"from": self.agent, "to": to_agent})
        self.parent_agent = self.agent
        self.agent = to_agent
        return self

    def child(self, agent: str) -> "DezissRun":
        """A sub-agent run sharing the session but with its own run id."""
        return DezissRun(
            run_id=new_run_id(),
            agent=agent,
            session_id=self.session_id,
            parent_agent=self.agent,
        )


def current_run() -> Optional[DezissRun]:
    return _current.get()


def run_headers(extra: Optional[dict] = None) -> dict:
    """Headers for the active run (empty dict when no run is active)."""
    out = dict(current_run().headers) if current_run() else {}
    if extra:
        out.update(extra)
    return out


@contextlib.contextmanager
def agent_run(
    agent: str,
    run_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Iterator[DezissRun]:
    """Open an agent run for the duration of the block.

    run_id/session_id also read DEZISS_RUN_ID / DEZISS_SESSION_ID from the
    environment, so an orchestrator can propagate one run across processes.
    """
    run = DezissRun(
        run_id=run_id or os.environ.get("DEZISS_RUN_ID") or new_run_id(),
        agent=agent,
        session_id=session_id or os.environ.get("DEZISS_SESSION_ID"),
    )
    token = _current.set(run)
    try:
        yield run
    finally:
        _current.reset(token)


def set_default_base_url(url: str) -> None:
    """Convenience: store the gateway base URL for SDK users that want one."""
    os.environ["DEZISS_BASE_URL"] = url
