# Enterprise Reliability Backlog & Architecture Review

This document provides a prioritized reliability and production-hardening backlog for **Beauty Ops Copilot** (Google ADK 2.13 + Gemini 3.8 Flash). Every finding is grounded in direct code inspection.

---

## 1. Streaming & Server-Sent Events (SSE) Lifecycle
- **Evidence**: `backend/app.py:141` uses `async with _locks[thread_id]:` inside an async generator (`StreamingResponse(gen(), media_type="text/event-stream")`).
- **Operational Impact**: If a client disconnects or aborts the HTTP connection mid-stream, some ASGI servers do not immediately cancel the background generator or can delay lock release, causing subsequent requests on the same `thread_id` to queue or timeout.
- **Recommended Remediation**: Wrap generator body in an explicit `try...finally` block that guarantees lock release and logs client disconnects cleanly.
- **Acceptance Criteria**: Aborted client stream does not cause thread starvation on next turn.
- **Lab vs Production**: In the lab, single-user demos rarely abort streams; in production with flaky mobile networks, aborted streams represent 2-5% of traffic.

---

## 2. Tool Execution, Retries & Fallbacks
- **Evidence**: `advisor/tools.py:192` implements exponential backoff with `API_RETRIES = 2` on `httpx.ReadTimeout` when calling the mock retail API.
- **Operational Impact**: Well-mitigated for retail inventory with `status: unavailable`, but `advisor/claims.py` database writes use direct SQLite connections with re-entrant locks.
- **Recommended Remediation**: Implement a circuit breaker pattern (e.g. 5 consecutive 5xx errors trips the breaker for 30s) to prevent hammering failing downstream inventory or claims services.
- **Acceptance Criteria**: When mock API returns 503 repeatedly, calls fast-fail immediately without consuming retry latency budget.
- **Lab vs Production**: Lab mock API has a toggle `/admin/fault`; production requires distributed rate-limiting and circuit breakers (e.g. Resilience4j or Envoy sidecar).

---

## 3. Session State & Scoped Prefixes
- **Evidence**: `advisor/claims_guardrail.py` and `tests/test_enterprise_adk_patterns.py` enforce scoped prefixes: `user:id` (preventing IDOR) and `temp:` (ephemeral).
- **Operational Impact**: `backend/app.py:71` holds locks and turn counters in unbounded memory dictionaries: `_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)` and `_turns: dict[str, int] = defaultdict(int)`.
- **Recommended Remediation**: Use an LRU or TTL-based cache (e.g. `cachetools.TTLCache(maxsize=10000, ttl=3600)`) to evict locks and counters for abandoned conversations.
- **Acceptance Criteria**: Memory footprint remains flat under continuous simulated traffic with unique thread IDs.
- **Lab vs Production**: Lab runs for minutes during presentations; production containers would leak memory over days of continuous operation.

---

## 4. Tracing & OpenTelemetry (OTel)
- **Evidence**: `backend/telemetry.py` sets up Google Cloud Trace exporter or console fallback depending on `TELEMETRY=gcp|console|off`.
- **Operational Impact**: Tracing spans are correctly established around chat turns, but exceptions inside asynchronous tool calls do not always record an exception event on the child tool span before failing.
- **Recommended Remediation**: Ensure all tool executions inside ADK runners record `span.record_exception(e)` and set span status to `StatusCode.ERROR`.
- **Acceptance Criteria**: Cloud Trace displays error root cause directly on the leaf tool span.
- **Lab vs Production**: Lab relies primarily on `kpi.jsonl` local structured logs; production requires Cloud Trace + BigQuery log sink.

---

## 5. Evaluation Fidelity & Parity
- **Evidence**: Two-tier evaluation (`evals/adk/` offline vs `evals/vertex/` live). Offline runner executes 7 golden cases in <1s using `InMemoryRunner`.
- **Operational Impact**: Mock tool responses in `evalset.json` must stay synchronized with live tool schemas. If a tool changes its response structure, offline evals could pass while live execution fails.
- **Recommended Remediation**: Add a schema contract check validating that offline eval mock tool responses conform to the Pydantic models of real tools.
- **Acceptance Criteria**: Schema drift between live tools and offline golden fixtures triggers a test failure in CI.
- **Lab vs Production**: Golden set is currently 7 curated cases; production evaluation requires continuous sampling of production traces into Vertex AI Evaluation Service.

---

## 6. Configuration & Secret Management
- **Evidence**: `.env` and `copilot/.env` are loaded via `dotenv`. `.gitignore` correctly ignores `.env` and `service_account*.json`.
- **Operational Impact**: Developers running local tests might accidentally populate sensitive API keys into files checked into git.
- **Recommended Remediation**: Introduce a pre-commit check (or CI step) running secret scanning (e.g. `gitleaks` or trufflehog) to block commits containing private keys or GCP service account credentials.
- **Acceptance Criteria**: Git commit with dummy GCP private key pattern is rejected automatically.
- **Lab vs Production**: Lab uses local service account key file; production uses GCP Workload Identity Federation (WIF) with zero stored secrets.

---

## 7. Guardrails & Prompt Injection Defense
- **Evidence**: `guardrails/model_armor.py` and `advisor/claims_guardrail.py` provide perimeter prompt filtering and tool authorization.
- **Operational Impact**: Prompt injection triggers a short-circuiting response before invoking Gemini.
- **Recommended Remediation**: Add automated adversarial regression evals testing indirect prompt injection embedded inside retrieved customer reviews and product markdown catalogs.
- **Acceptance Criteria**: Evaluator tests adversarial payloads inside mock KB search results and asserts model does not follow embedded jailbreak instructions.
- **Lab vs Production**: Implemented defense-in-depth is robust for the lab; production requires automated red-teaming pipelines.

---

## 8. Asynchronous Tools & Human-In-The-Loop (HITL)
- **Evidence**: `copilot/agent.py` and `backend/app.py:219` implement native ADK confirmation via `adk_request_confirmation` and `/chat/confirm`.
- **Operational Impact**: `ConfirmRequest` accepted empty `call_id` strings and lacked defensive error wrapping on confirmation failure (addressed in SPEC-001).
- **Recommended Remediation**: Enforce strict Pydantic bounds on `call_id` and guarantee structured error events when confirmation cannot be completed.
- **Acceptance Criteria**: Verified by `test_confirm_request_validation` and `test_chat_confirm_handles_engine_failure`.
- **Lab vs Production**: Lab uses single-tab confirmation; production requires multi-user RBAC verifying that the reviewer has permissions to approve claims.

---

## 9. MCP Connectors (Google Workspace)
- **Evidence**: `workspace_mcp/server.py` implements a narrow 4-tool contract (`search_mail`, `list_events`, `search_files`, `create_draft`) over MCP standard protocol.
- **Operational Impact**: MCP server runs as a separate subprocess or HTTP service. If the MCP child process terminates abruptly, ADK tool calls can hang or timeout.
- **Recommended Remediation**: Implement healthcheck probes and auto-restarting process supervisors for local MCP stdio transports.
- **Acceptance Criteria**: Subprocess crash recovers gracefully on the next agent turn.
- **Lab vs Production**: Lab uses local mock/stdio MCP server; production uses remote Cloud Run MCP server authenticated with Google Cloud Service-to-Service OIDC tokens.

---

## 10. Deployment & Cloud Run Containerization
- **Evidence**: `Dockerfile` and `terraform/` target Cloud Run.
- **Operational Impact**: Cloud Run scales to zero. Cold starts with ADK + Gemini SDK imports can take 4-8 seconds.
- **Recommended Remediation**: Configure Cloud Run with `min-instances: 1` during business hours, and pre-warm engine singletons during startup (`_init_engines()`).
- **Acceptance Criteria**: First-byte latency on `/health` is <500ms after container initialization.
- **Lab vs Production**: Lab runs locally via `uvicorn backend.app:app`; production runs on Google Cloud Run with custom domain and Cloud Armor WAF.

---

## 11. Dependency Drift & ADK Upgrades
- **Evidence**: Pytest captures `DeprecationWarning: SequentialAgent is deprecated in favor of Workflow and will be removed in a future version.`
- **Operational Impact**: While `SequentialAgent` works reliably in ADK 2.13.0, future ADK releases will require migration to the new `Workflow` construct once it supports sub-agent execution.
- **Recommended Remediation**: Pin `google-adk==2.13.0` in `pyproject.toml` (already pinned with `uv.lock`) and track ADK release notes for `Workflow` stabilization.
- **Acceptance Criteria**: Pinned lockfile ensures reproducible builds across all developer and CI machines.
- **Lab vs Production**: Pinned lockfile protects both lab and production from breaking upstream releases.

