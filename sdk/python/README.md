# deziss-sdk

Thin Python SDK for **DEZISS agent intelligence**. It adds four headers to your
LLM calls; the DEZISS gateway uses them to stitch individual requests into
**runs** — with per-step latency, tokens, cost, and agent handoffs — visible in
*Observability → Agent Intelligence*.

| Header | Meaning |
| --- | --- |
| `x-deziss-run-id` | stable id for one agent workflow |
| `x-deziss-agent` | which agent is making this call |
| `x-deziss-parent-agent` | previous agent, when a handoff happened |
| `x-deziss-session-id` | end-user session tying multiple runs together |

No dependencies. Works with any HTTP client.

## Quick start (OpenAI SDK against a DEZISS gateway)

```python
from openai import OpenAI
from deziss import agent_run

client = OpenAI(base_url="http://gateway:6130/v1", api_key="sk-...")

with agent_run("researcher", session_id="chat-42") as run:
    r1 = client.chat.completions.create(
        model="llama-3.1-8b",
        messages=[{"role": "user", "content": "summarise ACME's Q3"}],
        extra_headers=run.headers,
    )

    run.handoff("writer")           # recorded as a handoff edge on the same run
    r2 = client.chat.completions.create(
        model="llama-3.1-8b",
        messages=[{"role": "user", "content": "write the email"}],
        extra_headers=run.headers,
    )
```

Both calls appear as steps 1-2 of one run, with the `researcher → writer`
handoff, in the Agent Intelligence tab.

## Plain requests / httpx

```python
import requests
from deziss import agent_run, run_headers

with agent_run("coder"):
    requests.post("http://gateway:6130/v1/chat/completions",
                  headers=run_headers({"Authorization": "Bearer sk-..."}),
                  json={...})
```

## Sub-agents and nested code

```python
from deziss import current_run

def call_llm(payload):
    # Deep inside any helper, the active run is available:
    return client.chat.completions.create(extra_headers=current_run().headers, ...)
```

`run.child("agent-name")` spawns a *separate* run linked by session and parent
agent — use it when a sub-agent has its own independent lifecycle.

## Process boundaries

Orchestrators can propagate a run across processes via environment variables:
`DEZISS_RUN_ID` and `DEZISS_SESSION_ID` are picked up automatically by
`agent_run()`.

## Install

```bash
pip install ./sdk/python         # from the DEZISS repo
```
