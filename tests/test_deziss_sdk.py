"""Tests for the deziss SDK header contract (sdk/python)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))

from deziss import DezissRun, agent_run, current_run, new_run_id, run_headers  # noqa: E402


def test_headers_match_gateway_contract():
    with agent_run("researcher", run_id="r-1", session_id="s-1") as run:
        assert run.headers == {
            "x-deziss-run-id": "r-1",
            "x-deziss-agent": "researcher",
            "x-deziss-session-id": "s-1",
        }
        assert current_run() is run
        assert run_headers({"Authorization": "x"})["Authorization"] == "x"


def test_handoff_updates_headers_and_history():
    run = DezissRun(run_id="r", agent="a")
    run.handoff("b")
    assert run.headers["x-deziss-agent"] == "b"
    assert run.headers["x-deziss-parent-agent"] == "a"
    assert run.history == [{"from": "a", "to": "b"}]


def test_child_shares_session_with_new_run_id():
    parent = DezissRun(run_id="r1", agent="orchestrator", session_id="s")
    kid = parent.child("worker")
    assert kid.run_id != parent.run_id
    assert kid.session_id == "s" and kid.parent_agent == "orchestrator"


def test_no_active_run_yields_empty_headers():
    assert current_run() is None
    assert run_headers() == {}


def test_mapping_ducktype_for_openai_extra_headers():
    run = DezissRun(run_id="r", agent="a")
    assert dict(run.items())["x-deziss-run-id"] == "r"
    assert run["x-deziss-agent"] == "a"
    assert new_run_id().startswith("run-")
