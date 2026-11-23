# Beauty Ops Copilot

## About

**Beauty Ops Copilot** is a multi-agent retail reference architecture (engineering lab) built with **Google ADK 2.13**, **Vertex AI Gemini 3.8 Flash**, and **Model Context Protocol (MCP)**. It unifies customer engagement and back-office operations into a single coherent system:

> **Lab Scope & Demo-Only Components:** Local retail stock/delivery API (`mock_api/`), Workspace fixtures (`workspace_mcp/data/`), SQLite sessions (`backend/engines.py`), and `handoff_to_human` (ticket-shaped response without external queue) are demo-only unless wired to external services. See [docs/PROJECT_HANDOVER.md](docs/PROJECT_HANDOVER.md) for current gaps and hardening roadmap.

- **🌸 Léa (Customer Skincare & Quote Advisor — B2C):** An Agentic RAG chatbot that answers skincare, ingredient, and policy questions grounded strictly in validated catalog data with verifiable citations (groundedness verified by automated evaluation gates). It calculates deterministic routine budgets (`estimate_quantity`), generates official print-ready PDF quotes on the fly, and initiates customer return claims guarded by native ADK lifecycle callbacks (`before_tool_callback`).
- **💼 Operations Copilot (Internal Back-Office Assistant — B2B):** A workplace copilot connected to Google Workspace (Gmail, Calendar, Drive) via MCP, performing authoritative catalog lookups and arbitrating customer claims under deterministic code-enforced governance (call/write budgets, corporate recipient allowlist) with native Human-in-the-Loop approval (`require_confirmation=True`).

---

## Key Architecture & Core Capabilities

The platform implements a pure **Google ADK 2.13** architecture on **Google Cloud Vertex AI** with zero third-party agent frameworks (no external wrappers, 100% native ADK). It orchestrates two complementary retail agents through a unified web interface:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                DUAL-AGENT WEB PLATFORM                                  │
│                 FastAPI + SSE Streaming Engine (backend/app.py)                        │
│                 Layer 1: Google Cloud Model Armor Prompt Sanitization                  │
└────────────────────────────────────┬───────────────────────────────────────────────────┘
                                     │
         ┌───────────────────────────┴───────────────────────────┐
         ▼                                                       ▼
┌─────────────────────────────────┐     ┌────────────────────────────────────────────────┐
│   CLIENT AGENT: LÉA (B2C)       │     │   INTERNAL COPILOT: OPERATIONS (B2B)           │
│   (beauty_advisor/agent.py)     │     │   (copilot/agent.py)                           │
├─────────────────────────────────┤     ├────────────────────────────────────────────────┤
│ • Agentic RAG (Discovery Engine │     │ • Workplace Google Workspace Tools             │
│   reranking & exact citations)  │     │   (Gmail, Calendar, Drive) via MCP Server      │
│ • Deterministic Devis Calc      │     │   (workspace_mcp/server.py)                    │
│   (estimate_quantity)           │     │ • Operational Claims Arbitration               │
│ • Official Quote PDF Generator  │     │   (list_pending_claims, review_claim)          │
│   (headless Chrome + fallback)  │     │ • Native Human-in-the-Loop (HITL)              │
│ • Return Claim Submission       │     │   (require_confirmation=True on writes)        │
│   Guarded by ADK lifecycle hook │     │ • Platform GovernancePlugin                    │
│   (before_tool_callback)        │     │   (call budgets, write limit, domain allowlist)│
└─────────────────────────────────┘     └────────────────────────────────────────────────┘
                                     │
                                     ▼
        ┌────────────────────────────────────────────────────────┐
        │                 GOOGLE CLOUD PLATFORM                  │
        │ • Gemini 3.8 Flash on Vertex AI (eu multi-region)      │
        │ • Distributed Tracing via Cloud Trace (TELEMETRY=gcp)  │
        │ • Structured Governance Audit Logs & BigQuery Analytics│
        │ • Stateless Microservices on Google Cloud Run          │
        └────────────────────────────────────────────────────────┘
```

---

## Executive Documentation & Links

* [**15-Minute Live Interview Demo Script**](docs/DEMO_15MIN.md) — Chronological walkthrough with live GCP console inspection.
* [**5-Page Architecture Dossier (PDF)**](docs/beauty_ops_copilot_architecture_dossier.pdf) — Complete executive engineering overview.
* [**ADR-001: Pure ADK Architecture & Router Patterns**](docs/ADR-001.md) — Why ADK was selected and how agent composition is structured.
* [**ADR-002: Deterministic Governance & Two-Tier Evaluation**](docs/ADR-002.md) — ADK callbacks, offline behavioral gate, and Vertex AI Gen AI Evaluation.
* [**ADR-003: Session Persistence & Caller Ownership**](docs/ADR-003-sessions-and-ownership.md) — `DatabaseSessionService` (Cloud SQL) vs `VertexAiSessionService` vs SQLite, plus signed-token RBAC.
* [**ADK Evaluation Suite**](evals/adk/README.md) & [**Vertex AI Evaluation**](evals/vertex/README.md) — Automated quality gates.

---

## Project Layout

| Component | Path | Description |
|---|---|---|
| **Client Advisor** | `beauty_advisor/agent.py` | Customer skincare & quote agent (Google ADK App with lifecycle callbacks) |
| **Advisor Tools** | `advisor/tools.py` | Catalog search, product comparison, deterministic quantity and budget estimation |
| **Quote PDF Engine** | `advisor/devis_pdf.py` | Official quotation PDF generator (A4 print-ready with mandatory legal disclaimers) |
| **Claims Engine** | `advisor/claims.py` | Thread-safe SQLite claims engine with idempotent refund execution (`Idempotency-Key`) |
| **Claims Guardrail** | `beauty_advisor/agent.py` | Deterministic hygiene policy callback (`guardrail_before_tool`) intercepting out-of-policy claims |
| **Internal Copilot** | `copilot/agent.py` | Root operations router coordinating specialist agents |
| **Workspace Agent** | `copilot/workspace_agent.py` | Google Workspace specialist (Gmail, Calendar, Drive) with read/write separation |
| **Governance** | `copilot/governance.py` | Platform `GovernancePlugin`: tool allowlists, turn budgets, corporate domain allowlist |
| **Workspace MCP Server** | `workspace_mcp/server.py` | Model Context Protocol server exposing Google Workspace tools over HTTP |
| **FastAPI Backend** | `backend/app.py` | Unified streaming SSE server, Model Armor sanitization, and PDF download endpoints |
| **Dual Web UI** | `backend/static/index.html` | Dual-tab web interface with state persistence, timestamps, and HITL approval modals |
| **1-Click Launcher** | `run_demo.sh` | Shell launcher orchestrating mock APIs, backend, and browser auto-launch |
| **ADK Benchmark** | `evals/adk/` | 7 golden evaluation cases with custom deterministic evaluators (<1s offline runner) |
| **Vertex AI Eval** | `evals/vertex/` | Pointwise rubrics (Groundedness, Persona, Safety, Compliance) scored with LLM judge |
| **Sequential Quote Pipeline** | `advisor/structured_quote.py` | Expert ADK `SequentialAgent`: decoupled collector with tools and strict Pydantic `output_schema` formatter |
| **Enterprise Connectors** | `advisor/enterprise_connectors.py` | Production mapping: `VertexAiSearchTool`, `ToolboxToolset` (MCP DB), `LongRunningFunctionTool` |
| **Agent2Agent Protocol** | `advisor/a2a.py` | Google A2A protocol `AgentCard` for multi-system interoperability and capability discovery |
| **Unit Tests** | `tests/` | 144 comprehensive tests validating tools, governance, MCP contracts, PDF generation, docs consistency, sessions/auth, and ADK patterns |

---

## Quickstart & Execution Modes

The platform supports two distinct execution modes: **Zero-Config Offline Mode** (test and verify without any cloud accounts or API keys) and **Live Interactive Mode** (browser UI with real-time Gemini inference).

### Mode 1: Instant Offline Verification (Zero-Config, No API Keys)

Anyone can clone the repository and verify 100% of the architecture, contracts, and evaluation benchmarks immediately with **zero configuration, zero external network calls, and zero cost**:

```bash
# 1. Install locked dependencies
uv sync --frozen

# 2. Run the canonical offline verification suite (<10s)
uv run scripts/verify.py
```

This runs:
- **144 Unit & Integration Tests** (`tests/`) validating tools, governance, MCP contracts, PDF generation, docs consistency, sessions/auth, and ADK callbacks.
- **7 ADK Golden Benchmark Evals** (`evals/adk/`) checking deterministic behavior, tool execution, and guardrails.
- **Docs Consistency Gate** (`scripts/check_docs.py`) verifying test counts, relative links, and forbidden terms.

---

### Mode 2: Live Interactive Chatbot (Browser UI)

To converse with **Léa** (B2C) or **Operations Copilot** (B2B) in real time through the web interface, live LLM credentials and the retail mock service are required:

#### 1. Configure Credentials (`.env`)

Copy `.env.example` to `.env` at the project root:

```bash
cp .env.example .env
```

Configure **one** of the two supported authentication providers:

- **Option A — Google Cloud Vertex AI (Default Enterprise Sovereign Mode):**
  ```bash
  gcloud auth application-default login
  gcloud auth application-default set-quota-project <YOUR_GCP_PROJECT>
  ```
  Set `GOOGLE_CLOUD_PROJECT=<YOUR_GCP_PROJECT>` in `.env`.

- **Option B — Google AI Studio (Standalone API Key):**
  If you do not have a GCP project, comment out Option A in `.env` and set:
  ```env
  GEMINI_API_KEY=AIzaSy...
  MODEL=gemini-2.0-flash
  ```

#### 2. Launch Services

You can launch the entire stack with the 1-Click launcher:

```bash
./run_demo.sh
```

Or start the required services manually in separate terminals:

```bash
# Terminal 1: Retail stock and delivery mock API (Port 8090)
uv run uvicorn mock_api.stock_api:app --port 8090

# Terminal 2: Copilot Backend & Dual Web UI (Port 8000)
uv run uvicorn backend.app:app --port 8000 --reload
```

#### 3. Access Web Interface

Open **`http://localhost:8000`** in your browser. Switch between **🌸 Conseillère Léa (Client)** and **💼 Copilot Opérations (Interne)** tabs.

---

## Verification & CI/CD Gates

The platform enforces a two-tier evaluation architecture: fast offline regression testing for PR blocking, and deep live cloud evaluations on Vertex AI.

```bash
# Run canonical offline verification suite (<10s, 0 network, 0 costs)
uv run scripts/verify.py

# Run unit test suite (144 tests)
uv run pytest -q

# Run offline ADK evaluation benchmark (7/7 passing in <1s)
uv run python -m evals.adk.run_adk_eval --offline

# Run governance policy verification
uv run --env-file copilot/.env python scripts/gov_demo.py
```

For the complete agent-assisted development lifecycle, copy-paste prompts, and skill usage instructions, see [docs/DEVELOPER_WORKFLOW.md](docs/DEVELOPER_WORKFLOW.md).

---

## Governance Policy Enforcement

The platform enforces defense-in-depth where **code guarantees security before human confirmation is requested**:

1. **Layer 1 — Google Cloud Model Armor:** Scans user prompt for jailbreak and injection patterns on entry.
2. **Layer 2 — GovernancePlugin.before_tool:** Deny-by-default allowlist, per-turn call limit (10), write reservation (1 draft/turn), and corporate recipient allowlist (`@retail-group.com`, `@beauty-corp.com`, etc.).
3. **Layer 3 — Native Human-in-the-Loop:** High-impact writes (`create_draft`, `review_claim`) pause execution and require explicit user approval (`require_confirmation=True`).
4. **Layer 4 — Enterprise MCP Security:** Server-side bearer token authentication, audience checks, and OAuth scopes (`Mail.Read`, `Mail.ReadWrite`).
5. **Layer 5 — Cloud Trace Telemetry:** Audit spans exported with personal content elision (`NO_CONTENT`) for GDPR compliance.

---

## Results & Benchmark Evidence

Every measured number below links directly to its recorded run and sample size in [evals/FINDINGS.md](evals/FINDINGS.md).

| Capability Block | Method | Measured Result & Source |
|---|---|---|
| **RAG Retrieval** | Discovery Engine Rerank | Hit@5 `1.00`, MRR `0.98`, top-1 `0.96` (`n = 25` answerable / `30` total; poisoned review `0/30` after moving reviews to `reviews_public`) — [evals/FINDINGS.md](evals/FINDINGS.md#L97-L118) |
| **Token Compaction** | `before_model_callback` | `-40%` input tokens (`28.3K → 16.9K` on turn 7 of a multi-turn flow); `ContextCacheConfig` measured `0` cached tokens on single-turn evals — [evals/FINDINGS.md](evals/FINDINGS.md#L54-L57) & [L219](evals/FINDINGS.md#L219-L226) |
| **Devis Estimation** | Deterministic Pydantic Tool | Exact arithmetic verified in unit tests (`tests/test_advisor_tools.py`); print-ready PDF generated with legal disclaimers |
| **Claims Guardrail** | ADK `before_tool_callback` | Out-of-policy claims quarantined to `pending_review`; unauthorized refunds blocked (`tests/test_claims.py`) |
| **Exfiltration Defense** | Corporate Domain Allowlist | `4/4` attempted exfiltration drafts blocked by code under `naive + enforce` (`n = 3` runs × 4 attacks) — [evals/FINDINGS.md](evals/FINDINGS.md#L275-L299) |
| **Human-in-the-Loop** | Native `require_confirmation` | Arbitrations and drafts gated behind explicit confirmation modal (`tests/test_backend.py`) |
| **CI/CD Quality Gate** | GitHub Actions Pipeline | 144 unit tests + 7 deterministic ADK benchmarks + docs consistency gate (`scripts/verify.py`) |
| **Cloud Evaluation** | Vertex AI Gen AI Eval | `7/7` live cases passed, `5.0/5.0` Vertex LLM judge across 4 rubrics (`n = 7` cases) — [evals/FINDINGS.md](evals/FINDINGS.md#L4-L25) |
