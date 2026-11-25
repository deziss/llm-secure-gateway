"""DEZISS agent-intelligence SDK.

Adds run/agent attribution headers (``x-deziss-run-id``, ``x-deziss-agent``,
``x-deziss-parent-agent``, ``x-deziss-session-id``) to any HTTP call, so the
DEZISS gateway stitches individual LLM calls into agent runs with steps,
handoffs, latency and cost.

Works with plain ``requests``/``httpx``, the OpenAI SDK, LangChain, or anything
else that lets you pass extra headers. Zero required dependencies.

    from deziss import agent_run

    with agent_run("research", session_id="chat-42") as run:
        client.chat.completions.create(..., extra_headers=run.headers)
        run.handoff("writer")
        client.chat.completions.create(..., extra_headers=run.headers)
"""
from .core import DezissRun, agent_run, current_run, new_run_id, run_headers, set_default_base_url

__all__ = ["DezissRun", "agent_run", "current_run", "new_run_id", "run_headers", "set_default_base_url"]
__version__ = "0.1.0"
