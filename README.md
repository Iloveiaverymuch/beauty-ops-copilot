# Beauty Ops Copilot: ADK + Vertex AI + MCP agent POC

A multi-agent business backend on **Google ADK 2.13** and **Gemini on Vertex AI**. One agent reaches
Microsoft 365 through an **MCP server**. A **governance plugin** enforces tool policy in code, and every claim
is backed by a measured trial in [`evals/FINDINGS.md`](evals/FINDINGS.md).

A 3-day lab (Day 1 = agentic core, Day 2 = prove it, Day 3 = run it in production), built as interview prep for an "AI agents engine" mission (Python, ADK, Vertex/Gemini, MCP connectors for M365,
governance, observability). Lab plan: AI Formation project doc `claude/pipeline/loreal-adk-agent-poc-lab.md`.

## Architecture

```
adk web / adk run / scripts (InMemoryRunner(app=...))
  App "copilot"  plugins=[GovernancePlugin]        policy at platform level; also runs inside AgentTool runs
   root "copilot"  router (transfer only, no tools of its own)
     ├─ m365_agent     McpToolset(reads) + McpToolset(create_draft, require_confirmation=True)
     │                 + AgentTool(catalog_lookup)  mixed requests: call the catalog, keep control
     └─ catalog_agent  FunctionTools: search_products, get_campaign
  model: copilot/model.py  gemini-3.8-flash @ Vertex `eu`, 60 s timeout per attempt, 3 retries on 408/429/5xx

m365_mcp/server.py  MCPServer (mcp 2.3), streamable HTTP /mcp
  bearer token -> audience (RFC 8707 resource) -> Graph-style scope per tool -> fixture data
  annotations: read_only_hint / destructive_hint ; drafts written to m365_mcp/data/drafts.jsonl (never sent)
```

What happens on one tool call, in order:
1. **GovernancePlugin.before_tool** applies a deny-by-default allowlist, per-turn call and write budgets, and the recipient policy.
2. **ADK native `require_confirmation`** asks for human approval (write toolset only).
3. **The MCP server** checks the token, the audience and the scopes.
4. **GovernancePlugin.after_tool** writes the audit record, releases the write reservation if the write failed, and records Confidential files seen this turn.

Policy runs before approval on purpose: nobody is asked to approve something the policy forbids.

## Layout

| Path | What |
|---|---|
| `copilot/agent.py` | Router, `build_root(mode)`, `build_app()`; `app` is what `adk web` loads |
| `copilot/m365_agent.py` | M365 agent; separate read/write toolsets; `naive` / `hardened` prompt variants |
| `copilot/catalog_agent.py`, `copilot/tools/catalog.py` | Catalog agent + tools (constraints as params, near-miss hint) |
| `copilot/governance.py` | `GovernancePlugin` + `POLICY` (modes: `off` / `shadow` / `enforce`) |
| `copilot/model.py` | Gemini client: model id, timeout, retry policy; thinking level |
| `copilot/observability.py` | Telemetry setup: Cloud Trace / OTLP (New Relic) by env, content elided by default |
| `evals/obs_report.py`, `evals/LATENCY.md` | p50/p95 + tokens + cache-hit report across adk eval runs; A/B log |
| `m365_mcp/server.py`, `m365_mcp/data/` | Mock M365 MCP server; 11 mails incl. attacks `msg-005` (SYSTEM marker), `msg-009` (lookalike BEC), `msg-010` (internal authority, external target), `msg-011` (internal relay); Confidential `file-001` |
| `m365_only/` | Second `adk web` app: the M365 agent alone |
| `scripts/harness.py` | Runs one turn, answers approval pauses (simulated user), records calls/blocks/writes/errors |
| `scripts/gov_demo.py` | Deterministic check of the 5 governance paths (~1 min) |
| `scripts/injection_trials.py` | Prompt-only vs governance: attempted vs succeeded attacks, unrequested writes |
| `scripts/routing_trials.py` | transfer vs AgentTool on single-domain and mixed requests |
| `scripts/smoke_vertex.py`, `scripts/probe_models.py` | Vertex/ADC check; model x location availability grid |
| `scripts/mcp_smoke.py` | Talk to the MCP server through ADK `McpToolset` |
| `evals/build_evalset.py`, `evals/core/` | ADK-native evalset (10 cases) + criteria: trajectory IN_ORDER/ignore_args, hallucinations_v1 judge, response match |
| `evals/test_eval_core.py` | Same evalset as a pytest via `AgentEvaluator` (num_runs=3): the CI gate |
| `scripts/eval_suite.py` | Safety/utility suite: 4 attacks (marker, lookalike BEC, internal authority, internal relay) + 2 benign; pass^n, attempted vs blocked |
| `tests/test_eval_checks.py` | Tests for the eval predicates themselves, on real agent outputs |
| `tests/` | 35 offline tests (tools, MCP server contract, governance policy incl. race regressions) |
| `evals/FINDINGS.md` | Run log, newest first: setup -> numbers -> interpretation -> action |

## Setup (Mac)

```bash
uv sync
gcloud auth application-default login
gcloud auth application-default set-quota-project <PROJECT>
cp copilot/.env.example copilot/.env          # set GOOGLE_CLOUD_PROJECT
uv run --env-file copilot/.env python scripts/smoke_vertex.py      # expect VERTEX_OK
uv run --env-file copilot/.env python scripts/probe_models.py      # if the model 404s: pick model x location
```

Required APIs: aiplatform, run, cloudbuild, artifactregistry, secretmanager, cloudtrace, telemetry, logging.

## Run

```bash
uv run pytest -q                                   # 35 offline tests, no LLM, no server
uv run python -m m365_mcp.server                   # terminal 2: http://127.0.0.1:8080/mcp
uv run adk web                                     # http://127.0.0.1:8000 -> "copilot" or "m365_only"

uv run --env-file copilot/.env python scripts/gov_demo.py              # 5 governance paths
uv run --env-file copilot/.env python scripts/routing_trials.py --n 3
uv run --env-file copilot/.env python scripts/injection_trials.py --n 5 --variants naive hardened --governance off enforce
uv run python scripts/mcp_smoke.py [dev-readonly-token]                # MCP contract via ADK client

# D2.1 evaluation
uv run python evals/build_evalset.py                                   # regenerate the ADK evalset
# PYTHONPATH=. : `adk eval` imports copilot/ by file path without the repo root on sys.path
PYTHONPATH=. M365_CONFIRM_WRITES=0 uv run --env-file copilot/.env adk eval copilot evals/core/copilot_core.evalset.json \
    --config_file_path evals/core/test_config.json --print_detailed_results
uv run python evals/check_eval_budgets.py                               # efficiency gate on the latest result
M365_CONFIRM_WRITES=0 uv run --env-file copilot/.env pytest evals/test_eval_core.py -q -s   # same, 3 runs (CI gate)
uv run --env-file copilot/.env python scripts/eval_suite.py --n 3 --governance enforce     # safety + utility
uv run --env-file copilot/.env python scripts/eval_suite.py --n 3 --governance off         # what governance buys

# D2.2 observability
OTEL_SERVICE_NAME=beauty-ops-copilot uv run --env-file copilot/.env adk web --otel_to_cloud   # adk web: native flag
TELEMETRY=gcp uv run --env-file copilot/.env python scripts/gov_demo.py                    # scripts: our setup
uv run python evals/obs_report.py --since <unix_ts> --label "<config>"   # p50/p95, model share, tokens, cache hits
```

New Relic (no code; ADK adds an OTLP exporter from standard env vars):

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp.eu01.nr-data.net:4318
export OTEL_EXPORTER_OTLP_HEADERS="api-key=<NEW_RELIC_LICENSE_KEY>"
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

## Configuration (`copilot/.env`)

| Var | Default | Meaning |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | | GCP project |
| `GOOGLE_CLOUD_LOCATION` | `eu` | `eu` multi-region (residency). Single EU regions don't serve current Flash. `global` = availability, no residency guarantee |
| `MODEL` | `gemini-3.8-flash` | Verify with `probe_models.py`: a model listed in the catalog isn't necessarily callable |
| `MODEL_TIMEOUT_S` / `MODEL_RETRY_ATTEMPTS` | `60` / `3` | Per-attempt timeout; retries on 408/429/5xx |
| `ROUTER_MODE` | `agenttool` | `transfer` kept for the D1.4 comparison |
| `M365_PROMPT` | `hardened` | `hardened_v2` = recommended (scoped caution, data minimization, no commitments); `naive` for baselines |
| `M365_MCP_URL` / `M365_MCP_TOKEN` | `http://127.0.0.1:8080/mcp` / `dev-agent-token` | `dev-readonly-token` has no `Mail.ReadWrite` |
| `M365_CONFIRM_WRITES` | `1` | Native human approval on `create_draft` |
| `GOVERNANCE` | `enforce` | `off` / `shadow` (log decisions, block nothing) / `enforce` |
| `GOV_AUDIT_FILE` | unset | Also write audit JSON lines to a file (always logged to `governance.audit`) |
| `TELEMETRY` | `off` | `gcp` = ADK spans to Cloud Trace + GenAI metrics to Cloud Monitoring |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | `NO_CONTENT` | Prompts/responses in telemetry. Two libs read it with different conventions (ADK: `true`/`false`; GenAI instrumentor: `NO_CONTENT`/`SPAN_ONLY`/...). `NO_CONTENT` = off for both: M365 content stays out of traces |
| `THINKING_LEVEL` | unset | `MINIMAL`/`LOW`/`MEDIUM`/`HIGH` for Gemini 3; unset = model default |
| `CONTEXT_CACHE` | `0` | `1` = per-agent Gemini context cache (`CONTEXT_CACHE_MIN_TOKENS`, default 2048) |

## Governance policy (`copilot/governance.py`)

| Agent | Allowed tools | Calls / turn | Writes / turn |
|---|---|---|---|
| `copilot` (router) | `transfer_to_agent` | 2 | 0 |
| `m365_agent` | search_mail, list_events, search_files, create_draft, catalog_lookup, transfer | 10 (calibrated on legit triage, D2.1) | 1 |
| `catalog_agent` / `catalog_lookup` | search_products, get_campaign (+ transfer) | 4 | 0 |
| any other agent | nothing (deny by default) | 0 | 0 |

Other rules:
- Drafts to recipients outside `@loreal.com` are blocked; lookalike domains count as outside.
- The write set is **our** list, not the server's `read_only_hint`. A mismatch raises an alert.
- Budgets are scoped to the **user turn**: the key is the id of the last user event that has text, so they survive approval resumes.
- Write slots are **reserved at decision time**, which handles parallel function calls.
- Audit records hold hashes and domains, never addresses or arguments.

## Results so far (details and raw numbers in `evals/FINDINGS.md`)

| Block | Finding |
|---|---|
| D1.1 tools | Putting constraints into tool parameters cut tool calls from 6 to 3 on the core prompts. Exact filters hid what existed just outside them, so tools now return a near-miss hint. |
| D1.2 MCP | No token → 401. Read-only token → `insufficient_scope`. The injection email comes back as data, by design. |
| D1.3 prompt-only | 0/15 external drafts (95% upper bound ≈ 20%). Hardened prompt: 15/15 flagged, 1.4 calls (naive: 8/15, 3.9). The naive agent's real risk was unrequested writes. |
| D1.4 routing | Both modes passed 12/12. Transfer took the budget from an email (a provenance bug the passing check hid). AgentTool: about 2× faster, ⅓ of the calls, same path every run. **Chosen.** |
| D1.5 governance | gov_demo 5/5: external blocked before approval; approve → 1 draft, reject → 0; parallel writes → 1 created + 1 denied; plugin runs inside AgentTool. The call budget ended runaway loops (161–240+ s → 47–101 s) with 0 false positives on benign prompts. |

Bugs found by measuring, all fixed with regression tests:
1. **Parallel function calls:** two writes in one model response both passed the check, because the counter was updated on completion. Fix: reserve the slot when the call is allowed.
2. **Approval resume:** an approval resumes the run as a new invocation, and `temp:` state resets, so a second write got through. Fix: scope budgets to the user turn.
3. **Provenance:** a check on the answer text passed even though the fact came from a non-authoritative source. Evals must also assert the trajectory (D2.1).
4. **Infrastructure:** `eu` returned 503s and requests hung for more than 240 s, and latency varied from 12 s to 181 s on identical calls. Fix: timeout and retries in `model.py`; the harness reports infra failures separately.

## Known limits

- M365 is a mock: same contract as Graph (tools, scopes, Purview label), fixture data. Real Graph would need
  Entra auth (on-behalf-of the user) and is a change to `m365_mcp/server.py` only.
- Dev auth uses static bearer tokens. In prod: Google ID tokens between Cloud Run services (D2.3), Entra-issued tokens for Graph.
- The current injection (`msg-005`) is never even attempted by gemini-3.8-flash (0/57), so it can't measure exfiltration. Harder attacks come in D2.1.
- Over-refusal: the hardened prompt prepares no draft on "prepare any drafts needed", and that isn't scored yet.
- Session-state budget keys build up per turn (fine for a POC; a production store needs a TTL).
- Every transfer re-sends the prompt uncached, because `context_cache_config` isn't set yet (D2.2).
- The `Failed to configure mTLS` line at startup is google-auth probing for client certificates. It's harmless.

## Progress

- [x] H0 GCP setup, ADC, model x location probe (`gemini-3.8-flash` @ `eu`)
- [x] D1.1 catalog agent + tools (constraints in params, near-miss)
- [x] D1.2 M365 MCP server (bearer, audience, scopes, annotations)
- [x] D1.3 M365 agent + injection baseline (naive vs hardened)
- [x] D1.4 router: transfer vs AgentTool → AgentTool
- [x] D1.5 governance plugin + native approval; gov_demo 5/5; 2 race bugs fixed
- [x] D2.1 evaluation: ADK evalset 10/10 + call-budget gate; safety suite (4 attacks, over-refusal, commitment); prompt x governance matrix; hardened_v2 + budget 10
- [ ] D2.2 observability: Cloud Trace + New Relic OTLP, audit → log metrics, p50/p95, model vs tool time, caching
- [ ] D2.3 first deploy: 2 Cloud Run services, ID-token S2S auth (manual gcloud; steps become the D3.1 checklist)
- [ ] D2.4 package: ADR-001, DEMO.md, final eval table
- [ ] D3.1 IaC: Terraform for services, least-privilege SAs, Secret Manager, Artifact Registry (remote state in GCS)
- [ ] D3.2 CI/CD: tests -> `adk eval` gate -> no-traffic revision -> canary 10% -> promote; one-command rollback
- [ ] D3.3 state & scaling: persistent sessions (approval survives restart), budget-key TTL, instances, Vertex quotas/429
- [ ] D3.4 load & cost: 20-50 concurrent sessions, p50/p95/p99, errors, EUR per conversation, bottleneck
- [ ] D3.5 failure drills: MCP down, eu 503, bad secret, budget flood, regression blocked -> RUNBOOK.md + SLOs
