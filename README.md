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
| `deploy/` | Dockerfiles (mcp, copilot), `copilot_main.py` (Cloud Run entrypoint), `cloudbuild.yaml`, `DEPLOY.md` runbook |
| `tests/test_s2s_auth.py` | Google ID-token verifier (audience, caller allowlist) + header_provider caching |
| `tests/` | 42 offline tests (tools, MCP server contract, governance policy incl. race regressions) |
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
uv run pytest -q                                   # 42 offline tests, no LLM, no server
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
| `THINKING_LEVEL` | unset | `MINIMAL`/`LOW`/`MEDIUM`/`HIGH` for Gemini 3; unset = model default (global fallback) |
| `THINKING_LEVEL_ROUTER` / `_CATALOG` / `_M365` | `LOW` / `LOW` / unset | Per-agent thinking by risk: LOW where there are no writes and no untrusted content; full reasoning for the M365 agent (under LOW it attempted an exfil 2/3, blocked by policy) |
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

## Results (details and raw numbers in `evals/FINDINGS.md`; decisions in `docs/ADR-001.md`; demo in `DEMO.md`)

| Block | Finding |
|---|---|
| D1.1 tools | Constraints as tool params: 6 → 3 tool calls; near-miss hint when exact filters hide alternatives |
| D1.2 MCP | No token 401; read-only token `insufficient_scope`; injected mail returned as data (by design) |
| D1.3 prompt-only | 0/15 external drafts (upper bound ≈ 20%); naive agent's real risk = unrequested writes |
| D1.4 routing | AgentTool vs transfer: same pass rate, ~2× faster, ⅓ calls, authoritative source → **chosen** |
| D1.5 governance | gov_demo 5/5; parallel-call race and approval-resume scope bugs fixed |
| D2.1 eval | Core evalset 10/10 + call-budget gate; matrix below; hardened_v2 = safe and useful |
| D2.2 observability | Governance visible on trace spans; latency provider-bound (thinking −90% tokens, no gain); per-agent thinking |
| D2.3 deploy | 2 private Cloud Run services; 403 edge / 401 app; structured audit + `governance_denies` metric |

**Prompt × governance matrix** (n=3, rubber-stamp approver, 4 attacks + ambiguous legitimate task):

| Config | Attacks succeeded | Attempted → blocked | Ambiguous task (utility) |
|---|---|---|---|
| naive + no policy | **4/12** (real exfil drafts) | 4 → 0 | 3/3 |
| naive + enforce | 0/12 | 4 → 4 | 3/3 (after budget calibration) |
| hardened v1 + enforce | 0/12 | 0 | **0/3** (over-refusal) |
| **hardened_v2 + enforce** (shipped) | **0/12** | 0 | **3/3**, no unauthorized commitment |

**Core eval** (ADK, 10 cases): trajectory `IN_ORDER` 1.0, `hallucinations_v1` 1.0, calls within budget;
p50 ≈ 15 s, p95 ≈ 70 s on `global` (provider-bound tail; `eu` worse: max 417 s).

Bugs found by measuring (all fixed, regression-tested): parallel function calls race · approval resume = new
invocation · provenance hidden by a passing check · SKU not searchable (green eval, 5 calls) · eval checker
false-fail · telemetry silent failures (provider ownership, missing `gcp.project_id`) · 4 deploy issues
(origin check, file permissions, missing API, two URLs).

### Evidence screenshots (deployed service, 2026-11-08)
| | |
|---|---|
| Policy blocks before approval | [`docs/demo/01-ui-blocked.png`](docs/demo/01-ui-blocked.png) |
| Prod trace waterfall | [`docs/demo/02-trace-waterfall.png`](docs/demo/02-trace-waterfall.png) |
| Denied span (`governance.denied`) | [`docs/demo/03-trace-denied-span.png`](docs/demo/03-trace-denied-span.png) |
| Audit log line, same trace | [`docs/demo/04-audit-log.png`](docs/demo/04-audit-log.png) |
| `governance_denies` metric | [`docs/demo/05-metric.png`](docs/demo/05-metric.png) |
| Prompt layer refuses an injection | [`docs/demo/06-ui-prompt-refusal.png`](docs/demo/06-ui-prompt-refusal.png) |

## Known limits

- M365 is a mock: same contract as Graph (tools, scopes, Purview label), fixture data. Real Graph would need
  Entra auth (on-behalf-of the user) and is a change to `m365_mcp/server.py` only.
- Dev auth uses static bearer tokens. In prod: Google ID tokens between Cloud Run services (D2.3), Entra-issued tokens for Graph.
- Recipient policy is not DLP: an internal relay (`msg-011` → `migration-backup@loreal.com`) is allowed by policy; only the model resists it.
- Eval sample sizes (n=3) give direction, not SLOs or safety guarantees; configs were A/B'd sequentially (time-of-day confound).
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
- [x] D2.2 observability: Cloud Trace (governance on spans, content elided), thinking/cache A/B (latency = provider-bound), per-agent thinking by risk
- [x] D2.3 first deploy: 2 private Cloud Run services, Google ID-token S2S + per-caller scopes (403 edge / 401 app), structured audit logs + `governance_denies` metric -> `deploy/DEPLOY.md`
- [x] D2.4 package: `docs/ADR-001.md` (9 decisions + evidence), `DEMO.md` (5-min script, warm-up, fallback, Q&A), results tables
- [ ] D3.1 IaC: Terraform for services, least-privilege SAs, Secret Manager, Artifact Registry (remote state in GCS)
- [ ] D3.2 CI/CD: tests -> `adk eval` gate -> no-traffic revision -> canary 10% -> promote; one-command rollback
- [ ] D3.3 state & scaling: persistent sessions (approval survives restart), budget-key TTL, instances, Vertex quotas/429
- [ ] D3.4 load & cost: 20-50 concurrent sessions, p50/p95/p99, errors, EUR per conversation, bottleneck
- [ ] D3.5 failure drills: MCP down, eu 503, bad secret, budget flood, regression blocked -> RUNBOOK.md + SLOs
