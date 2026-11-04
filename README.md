# Beauty Ops Copilot — ADK + Vertex AI + MCP POC
Lab plan: AI Formation project doc `claude/pipeline/loreal-adk-agent-poc-lab.md`. Pinned versions: PINNED.txt (google-adk 2.13.0).

## Run (on the Mac)
    uv sync
    cp copilot/.env.example copilot/.env   # set GOOGLE_CLOUD_PROJECT
    uv run pytest -q                       # offline tool tests
    uv run adk web                         # http://localhost:8000 -> pick "copilot"
    uv run adk run copilot                 # CLI chat

## Progress
- [x] D1.1 catalog agent + tools + tests
- [ ] D1.2 m365 MCP server   - [ ] D1.3 m365 agent   - [ ] D1.4 router   - [ ] D1.5 governance
- [ ] D2.1 eval   - [ ] D2.2 observability   - [ ] D2.3 deploy   - [ ] D2.4 package
