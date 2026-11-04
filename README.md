# Beauty Ops Copilot — ADK + Vertex AI + MCP POC
Lab plan: AI Formation project doc `claude/pipeline/loreal-adk-agent-poc-lab.md`. Pinned versions: PINNED.txt (google-adk 2.13.0).

## Run (on the Mac)
    uv sync
    cp copilot/.env.example copilot/.env   # set GOOGLE_CLOUD_PROJECT
    uv run pytest -q                       # offline tool tests
    uv run adk web                         # http://localhost:8000 -> pick "copilot"
    uv run adk run copilot                 # CLI chat

    # M365 mock MCP server (terminal 2)
    uv run python -m m365_mcp.server       # http://127.0.0.1:8080/mcp, bearer auth
    uv run python scripts/mcp_smoke.py                     # full-scope token
    uv run python scripts/mcp_smoke.py dev-readonly-token  # create_draft -> insufficient_scope

## Progress
- [x] D1.1 catalog agent + tools + tests (6 -> 3 tool calls on core prompts; near-miss hint)
- [x] D1.2 m365 MCP server (mcp 2.3 MCPServer, bearer+audience+scopes, annotations)   - [x] D1.3 m365 agent + injection baseline   - [x] D1.4 router (agenttool)   - [x] D1.5 governance plugin (built; trials pending)
- [ ] D2.1 eval   - [ ] D2.2 observability   - [ ] D2.3 deploy   - [ ] D2.4 package
