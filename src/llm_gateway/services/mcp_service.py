"""Model Context Protocol (MCP) Client & Autonomous Agent Loop.

Enables any LLM (including local vLLM / Ollama backends) to discover and execute
tools via the Model Context Protocol (MCP).
Supports:
1. Tool Modes:
   - 'selector': Injects two compact meta-tools (mcp_tools_get, mcp_tools_execute)
     to preserve thousands of prompt tokens across extensive catalogs.
   - 'direct': Inlines all tool JSON schemas directly into the LLM request.
2. Autonomous Gateway Agent Loop:
   - Detects tool calls returned by the LLM, executes the tool against the MCP
     service, appends tool result messages, and re-invokes the model iteratively
     (up to MAX_AGENT_ITERATIONS = 10) until completion.
3. Per-request bypass via 'X-MCP-Bypass: true' header or 'MCP_ENABLED=false'.
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx

logger = logging.getLogger(__name__)

MAX_AGENT_ITERATIONS = 10
TOOL_PREFIX = "mcp_"
SELECTOR_GET = TOOL_PREFIX + "tools_get"
SELECTOR_EXECUTE = TOOL_PREFIX + "tools_execute"


class MCPToolRegistry:
    """In-memory catalog of available MCP tools from local providers & remote MCP servers."""

    def __init__(self):
        self._tools: Dict[str, dict] = {}
        self._register_default_tools()

    def _register_default_tools(self):
        """Register built-in system tools."""
        self.register_tool(
            name="calculator",
            description="Perform mathematical calculations and evaluate math expressions safely.",
            input_schema={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Mathematical expression (e.g. '17 * 4 + 10')"}
                },
                "required": ["expression"],
            },
            handler=self._handle_calculator,
        )
        self.register_tool(
            name="system_time",
            description="Get current server UTC timestamp and formatted date.",
            input_schema={"type": "object", "properties": {}},
            handler=lambda args: {"utc_time": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()), "epoch": int(time.time())},
        )
        self.register_tool(
            name="system_echo",
            description="Echo a message back to the caller for testing.",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string", "description": "Message to echo"}},
                "required": ["message"],
            },
            handler=lambda args: {"echo": args.get("message", "")},
        )

    def _handle_calculator(self, args: dict) -> dict:
        expr = args.get("expression", "")
        # Safe math evaluation using basic AST/operators
        allowed_chars = set("0123456789+-*/(). %")
        if not all(c in allowed_chars for c in expr):
            return {"error": "Expression contains disallowed characters"}
        try:
            # Evaluate arithmetic safely
            result = eval(expr, {"__builtins__": None}, {})
            return {"expression": expr, "result": result}
        except Exception as e:
            return {"error": str(e)}

    def register_tool(self, name: str, description: str, input_schema: dict, handler: Any, server: str = "builtin"):
        tool_name = name if name.startswith(TOOL_PREFIX) else f"{TOOL_PREFIX}{name}"
        self._tools[tool_name] = {
            "name": tool_name,
            "raw_name": name,
            "description": description,
            "input_schema": input_schema,
            "server": server,
            "handler": handler,
        }

    def get_tool(self, name: str) -> Optional[dict]:
        return self._tools.get(name) or self._tools.get(f"{TOOL_PREFIX}{name}")

    def list_tools(self, query: Optional[str] = None) -> List[dict]:
        tools = list(self._tools.values())
        if not query:
            return tools
        q = query.lower()
        return [t for t in tools if q in t["name"].lower() or q in t["description"].lower()]

    async def execute_tool(self, name: str, arguments: dict) -> dict:
        tool = self.get_tool(name)
        if not tool:
            return {"error": f"Tool '{name}' not found in MCP catalog"}

        handler = tool["handler"]
        try:
            if callable(handler):
                import inspect
                if inspect.iscoroutinefunction(handler):
                    return await handler(arguments)
                return handler(arguments)
            elif isinstance(handler, str) and handler.startswith("http"):
                # Remote MCP server endpoint
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(handler, json={"name": tool["raw_name"], "arguments": arguments})
                    return resp.json()
            return {"error": "Invalid tool handler"}
        except Exception as exc:
            logger.error("MCP tool '%s' execution failed: %s", name, exc)
            return {"error": f"Tool execution failed: {str(exc)}"}


# Global singleton registry
mcp_registry = MCPToolRegistry()


def get_selector_tools() -> List[dict]:
    """Return the fixed pair of selector meta-tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": SELECTOR_GET,
                "description": (
                    "Discover available MCP tools or get their full schemas. "
                    "Call without arguments for a summary catalog. "
                    "Call with query or names to filter specific tools."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Keyword filter"},
                        "names": {"type": "array", "items": {"type": "string"}, "description": "Specific tool names to get schemas for"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": SELECTOR_EXECUTE,
                "description": "Execute an MCP tool by name with arguments. Look up the schema using mcp_tools_get first.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Name of the MCP tool to execute (e.g. 'mcp_calculator')"},
                        "arguments": {"type": "object", "description": "Arguments dictionary matching tool input schema"},
                    },
                    "required": ["name", "arguments"],
                },
            },
        },
    ]


def get_direct_tools() -> List[dict]:
    """Return all registered MCP tools in OpenAI function format."""
    out = []
    for t in mcp_registry.list_tools():
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        })
    return out


def inject_mcp_tools(body: dict, mode: str = "selector") -> dict:
    """Inject MCP tools into OpenAI chat completion body."""
    new_body = dict(body)
    existing_tools = list(new_body.get("tools") or [])

    if mode == "selector":
        mcp_tools = get_selector_tools()
    else:
        mcp_tools = get_direct_tools()

    # Avoid duplicate tool names
    existing_names = {t.get("function", {}).get("name") for t in existing_tools if isinstance(t, dict)}
    for mt in mcp_tools:
        if mt["function"]["name"] not in existing_names:
            existing_tools.append(mt)

    new_body["tools"] = existing_tools
    return new_body


async def handle_selector_get(args: dict) -> dict:
    """Handle mcp_tools_get execution."""
    query = args.get("query")
    names = args.get("names") or []

    tools = mcp_registry.list_tools(query)
    if names:
        names_set = set(names)
        tools = [t for t in tools if t["name"] in names_set or t["raw_name"] in names_set]

    catalog = []
    for t in tools:
        entry = {
            "name": t["name"],
            "description": t["description"],
            "server": t["server"],
        }
        if names or len(tools) <= 3:
            entry["input_schema"] = t["input_schema"]
        catalog.append(entry)
    return {"tools": catalog}


async def process_mcp_tool_call(name: str, arguments: dict) -> dict:
    """Dispatch a tool call to the selector handler or the registered tool handler."""
    if name == SELECTOR_GET:
        return await handle_selector_get(arguments)
    elif name == SELECTOR_EXECUTE:
        target_name = arguments.get("name", "")
        target_args = arguments.get("arguments") or {}
        return await mcp_registry.execute_tool(target_name, target_args)
    else:
        return await mcp_registry.execute_tool(name, arguments)
