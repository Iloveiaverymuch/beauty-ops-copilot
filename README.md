# Beauty Ops Copilot

## About

**Beauty Ops Copilot** is an enterprise-grade multi-agent retail platform built with **Google ADK 2.13**, **Vertex AI Gemini 3.8 Flash**, and **Model Context Protocol (MCP)**. It unifies customer engagement and back-office operations into a single coherent system:

- **🌸 Léa (Customer Skincare & Quote Advisor — B2C):** An Agentic RAG chatbot that answers skincare, ingredient, and policy questions grounded strictly in validated catalog data with verifiable citations (zero hallucinations). It calculates deterministic routine budgets (`estimate_quantity`), generates official print-ready PDF quotes on the fly, and initiates customer return claims guarded by native ADK lifecycle callbacks (`before_tool_callback`).
- **💼 Operations Copilot (Internal Back-Office Assistant — B2B):** A workplace copilot connected to Microsoft 365 (Outlook, Calendar, OneDrive) via MCP, performing authoritative catalog lookups and arbitrating customer claims under deterministic code-enforced governance (call/write budgets, corporate recipient allowlist) with native Human-in-the-Loop approval (`require_confirmation=True`).

---

## Key Architecture & Core Capabilities

The platform implements a pure **Google ADK 2.13** architecture on **Google Cloud Vertex AI** with zero third-party agent frameworks (no LangGraph, no CrewAI, no external wrappers). It orchestrates two complementary retail agents through a unified web interface:

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
│ • Agentic RAG (Discovery Engine │     │ • Workplace M365 Tools (Mail, Calendar, Files) │
│   reranking & exact citations)  │     │   via MCP Server (m365_mcp/server.py)          │
│ • Deterministic Devis Calc      │     │ • Official Catalog Lookup (SKUs & campaigns)   │
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
* [**ADR-001: Pure ADK Architecture vs LangGraph**](docs/ADR-001-adk-vs-langgraph.md) — Why ADK was selected over auxiliary frameworks.
* [**ADR-002: Deterministic Governance & Human-in-the-Loop**](docs/ADR-002-callbacks-guardrails-hitl.md) — ADK callbacks vs prompt-only guardrails.
* [**ADK Evaluation Suite**](evals/adk/README.md) & [**Vertex AI Evaluation**](evals/vertex/README.md) — Automated quality gates.

---

## Project Layout

| Component | Path | Description |
|---|---|---|
| **Client Advisor** | `beauty_advisor/agent.py` | Customer skincare & quote agent (Google ADK App with lifecycle callbacks) |
| **Advisor Tools** | `advisor/tools.py` | Catalog search, product comparison, deterministic quantity and budget estimation |
| **Quote PDF Engine** | `advisor/devis_pdf.py` | Official quotation PDF generator (A4 print-ready with mandatory legal disclaimers) |
| **Claims Engine** | `advisor/claims.py` | Thread-safe SQLite claims engine with idempotent refund execution (`Idempotency-Key`) |
| **Claims Guardrail** | `advisor/claims_guardrail.py` | Deterministic hygiene policy guardrail intercepting claims into `pending_review` |
| **Internal Copilot** | `copilot/agent.py` | Root operations router coordinating specialist agents |
| **M365 Agent** | `copilot/m365_agent.py` | Workplace assistant with read/write separation and prompt injection hardening |
| **Governance** | `copilot/governance.py` | Platform `GovernancePlugin`: tool allowlists, turn budgets, corporate domain allowlist |
| **M365 MCP Server** | `m365_mcp/server.py` | Model Context Protocol server exposing Outlook and OneDrive tools over HTTP |
| **FastAPI Backend** | `backend/app.py` | Unified streaming SSE server, Model Armor sanitization, and PDF download endpoints |
| **Dual Web UI** | `backend/static/index.html` | Dual-tab web interface with state persistence, timestamps, and HITL approval modals |
| **1-Click Launcher** | `run_demo.sh` | Shell launcher orchestrating mock APIs, backend, and browser auto-launch |
| **ADK Benchmark** | `evals/adk/` | 7 golden evaluation cases with custom deterministic evaluators (<1s offline runner) |
| **Vertex AI Eval** | `evals/vertex/` | Pointwise rubrics (Groundedness, Persona, Safety, Compliance) scored with LLM judge |
| **Unit Tests** | `tests/` | 111 comprehensive tests validating tools, governance, MCP contracts, and PDF generation |

---

## Quickstart (Local Development & Demo)

### 1. Prerequisites & Setup

```bash
uv sync
gcloud auth application-default login
gcloud auth application-default set-quota-project <YOUR_GCP_PROJECT>
cp copilot/.env.example copilot/.env
```

### 2. Launch the Complete Demo (1-Click)

Launch both the retail mock API (port 8090) and the dual-agent web platform (port 8000) with automatic browser opening:

```bash
./run_demo.sh
```

Or run services manually in separate terminals:
```bash
# Terminal 1: Retail stock and delivery API
uv run uvicorn mock_api.stock_api:app --port 8090

# Terminal 2: Copilot Backend & Web UI
uv run uvicorn backend.app:app --port 8000 --reload
```

---

## Verification & CI/CD Gates

The platform enforces a two-tier evaluation architecture: fast offline regression testing for PR blocking, and deep live cloud evaluations on Vertex AI.

```bash
# Run unit test suite (111 tests passing in ~5s)
uv run pytest -q

# Run offline ADK evaluation benchmark (7/7 passing in <1s)
uv run python -m evals.adk.run_adk_eval --offline

# Run governance policy verification
uv run --env-file copilot/.env python scripts/gov_demo.py
```

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

| Capability Block | Method | Measured Result |
|---|---|---|
| **RAG Citations** | Discovery Engine Rerank | Zero hallucinations; strict source grounding on validated knowledge base |
| **Devis Estimation** | Deterministic Pydantic Tool | 100% arithmetical accuracy; print-ready PDF generated with legal disclaimers |
| **Claims Guardrail** | ADK `before_tool_callback` | Out-of-policy claims quarantined to `pending_review`; unauthorized refunds blocked |
| **Exfiltration Defense** | Corporate Domain Allowlist | 4/4 external exfiltration attempts blocked by code before human review |
| **Human-in-the-Loop** | Native `require_confirmation` | Arbitrations and drafts gated behind explicit confirmation modal |
| **CI/CD Quality Gate** | GitHub Actions Pipeline | 111 unit tests + 7 deterministic ADK benchmarks executed in <25s |
| **Cloud Evaluation** | Vertex AI Gen AI Eval | 7/7 live cases passed, 5.0/5.0 Vertex LLM judge rating across all rubrics |
