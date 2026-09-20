"""
Regression tests for the backend health-probe time budget.

A backend on a silently-dropping network (firewalled host, dead VLAN) never
sends a TCP reset, so each probe costs the full connect timeout.  The original
implementation tried four candidate paths sequentially at 10s each -- 40s per
backend -- while holding a pooled DB connection and one of the browser's six
per-origin sockets.  With several such backends configured, the admin UI froze
and the connection pool drained.

These tests fail the build if that budget regresses.
"""
import asyncio
import time

import pytest

from llm_gateway.routers import backends as backends_router

# RFC 1918 address that is routable but has nothing listening and no router to
# reject, so connections hang until they time out rather than failing fast.
BLACKHOLE = "http://10.255.255.1:9999"


def test_probe_budget_constants_are_bounded():
    """The overall budget must stay short enough for an interactive UI."""
    assert backends_router._HEALTH_TOTAL_BUDGET <= 10.0, (
        "health probe budget is too long for an interactive page; "
        "the browser allows only ~6 concurrent connections per origin"
    )
    timeout = backends_router._HEALTH_TIMEOUT
    assert timeout.connect is not None and timeout.connect <= 3.0, (
        "connect timeout must be short: unreachable hosts cost the full value"
    )


def test_probe_budget_is_not_multiplied_by_path_count():
    """
    Guards the specific regression: budget must bound *all* paths combined,
    not apply per candidate path.
    """
    worst_case_if_sequential = (
        backends_router._HEALTH_TIMEOUT.connect * len(backends_router._HEALTH_PROBE_PATHS)
    )
    assert backends_router._HEALTH_TOTAL_BUDGET < worst_case_if_sequential, (
        "the total budget must cut probing short before every path has "
        "individually timed out"
    )


def test_unreachable_backend_returns_none_within_budget(monkeypatch):
    """An unreachable host must give up on time and report failure, not hang."""
    # Shrink the budget so the test stays fast while exercising the real path.
    monkeypatch.setattr(backends_router, "_HEALTH_TOTAL_BUDGET", 1.0)

    async def run():
        start = time.monotonic()
        result = await backends_router._probe_backend(BLACKHOLE)
        return result, time.monotonic() - start

    result, elapsed = asyncio.new_event_loop().run_until_complete(run())

    assert result is None, "a black-holed backend must not report healthy"
    assert elapsed < 4.0, (
        f"probe took {elapsed:.1f}s against a 1.0s budget; the deadline is "
        "not bounding the candidate-path loop"
    )
