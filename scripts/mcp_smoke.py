"""Smoke test: talk to the Workspace MCP server exactly like the agent will (ADK McpToolset).
Start the server first:  uv run python -m workspace_mcp.server
Then:                    uv run python scripts/mcp_smoke.py [token]
"""
import asyncio
import json
import os
import sys
from unittest.mock import MagicMock

from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

URL = os.environ.get("WORKSPACE_MCP_URL") or os.environ.get("M365_MCP_URL", "http://127.0.0.1:8080/mcp")
TOKEN = sys.argv[1] if len(sys.argv) > 1 else "dev-agent-token"


async def main() -> None:
    ts = McpToolset(connection_params=StreamableHTTPConnectionParams(
        url=URL, headers={"Authorization": f"Bearer {TOKEN}"}))
    try:
        tools = {t.name: t for t in await ts.get_tools()}
        for name, t in tools.items():
            ann = t._mcp_tool.annotations
            print(f"tool {name:14} read_only={ann.read_only_hint if ann else None}")
        ctx = MagicMock()  # bypasses ADK's agent loop: we call the tool directly to test the server contract
        calls = [
            ("search_mail", {"query": "garnier", "top": 3}),
            ("search_mail", {"query": "packaging"}),
            ("list_events", {"start_iso": "2026-11-05T00:00:00+01:00", "end_iso": "2026-11-07T23:59:00+01:00"}),
            ("search_files", {"query": "pricing"}),
            ("create_draft", {"to": ["sophie.martin@loreal.com"], "subject": "smoke", "body": "test draft"}),
        ]
        for name, args in calls:
            r = await tools[name]._run_async_impl(args=args, tool_context=ctx, credential=None)
            print(f"\n>> {name}({args})\n{json.dumps(r, ensure_ascii=False)[:400]}")
    finally:
        await ts.close()


asyncio.run(main())
