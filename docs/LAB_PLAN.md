# Interview Lab: Master Execution Plan

Authority: Job interview preparation for Friday 2026-11-13 (one preparation day remaining).
Purpose: Understand, demonstrate, and defend a working multi-agent backend on **Google ADK 2.13** and **Google Cloud Vertex AI**.
Key Requirement: The final result must be **100% coherent, explainable, and demonstrable** during a 15-minute live executive presentation.

---

## Decisions & Architecture Principles

- **Pure Google ADK Architecture**: Unify all agents and workflows onto Google ADK 2.13 and Gemini on Vertex AI. Eliminate auxiliary orchestration frameworks (LangGraph) to present a clean, unified mental model.
- **Callbacks Give You Guardrail Control**: Enforce security and business policies deterministically in code via ADK lifecycle hooks (`before_tool_callback`, `after_tool_callback`, `before_model_callback`) rather than relying on probabilistic system prompts.
- **Native Human-in-the-Loop (HITL)**: Use ADK's native `require_confirmation=True` for high-stakes actions (M365 drafts, claim refund approvals).
- **Two-Tier Verification**: Sub-25s offline CI gate (106 unit tests + 7 deterministic ADK evaluations) for PR blocking, combined with on-demand live Vertex AI evaluation (LLM-as-a-judge rubrics).
- **Google Cloud Observability**: Cloud Trace spans with content elision (`NO_CONTENT`) and structured governance audit logs.
- **Confidentiality & Neutralization**: Retail demo branding is neutralized. Client identity is strictly confidential.

---

## Master Roadmap & Current Position

```
[COMPLETED] Block 2: ADK Native & Vertex AI Evals (106 tests, 7/7 live green, 5.0/5.0 judge)
     │
     ▼
[COMPLETED] Phase 1: Pure ADK Architecture & Claims Modernization (108 tests, 0 LangGraph)
     │
     ▼
[PHASE 2] Dual-Agent Web UI (Showcase Beauty Advisor + Internal Copilot)   <-- NEXT
     │
     ▼
[PHASE 3] Model Armor Guardrails & Cloud Trace Observability
     │
     ▼
[PHASE 4] 15-Minute Live Interview Demo Script & Rehearsal
     │
     ▼
[PHASE 5] Project Architecture Dossier PDF Generation (Max 5 Pages)
     │
     ▼
[PHASE 6] Final Markdown Coherence Sweep & Presentation Polish
```

---

## Execution Phases Detail

### Completed Milestones
- **ADK Native Evaluation & Vertex AI Evaluation Architecture**:
  - Implemented `evals/adk/` with 7 benchmark cases, custom evaluators (canaries, citations, refunds, filters), and `<1s` offline fixture runner.
  - Implemented `evals/vertex/` with 4 Pointwise rubrics (Groundedness, Persona Quality, Safety Refusal, Claim Compliance) and BigQuery trace monitoring.
  - Cleaned up CI/CD: removed all Node.js/npm dependencies; verified CI gate PR blocking on `test/eval-gate-regression`.
  - Ran live evaluation in GitHub Actions with GCP SA authentication: **7/7 PASSED + 5.0/5.0 Vertex LLM Judge**.
  - Documented in `docs/ADR-002.md`, `evals/FINDINGS.md`, and updated `README.md`.

- **Pure ADK Architecture & Claims Modernization (LangGraph Elimination)**:
  - **Complete & Verified Green (108 tests passing)**.
  - Replaced LangGraph with pure ADK SQLite claims engine (`advisor/claims.py`).
  - Added ADK Guardrail Callback (`guardrail_before_tool` via `before_tool_callback`) in `beauty_advisor/agent.py` to intercept out-of-policy returns.
  - Added operational arbitration tools (`list_pending_claims`, `review_claim`) to `copilot/agent.py` and `copilot/governance.py` with native `require_confirmation=True`.
  - Purged all `langgraph*` dependencies from `pyproject.toml` and regenerated lockfile. All 108 tests passing.

---

### Phase 2: Dual-Agent Web UI (Beauty Advisor + Internal Copilot)
- **Goal**: Build a unified, sleek web UI that showcases both agents in the live presentation without relying on raw developer CLI tools (`adk web`).
- **Deliverables**:
  1. *Internal Copilot Frontend*: A dedicated view or tab matching Beauty Advisor's aesthetic (`backend/static/copilot.html` or integrated switcher) that displays:
     - M365 email triage and calendar summary cards.
     - Catalog lookup with hero SKUs and campaign details.
     - Interactive **Human Approval Modal** when `create_draft` requests confirmation.
     - Visual **Policy Blocked Badges** (e.g. external recipient blocked before approval).
  2. *Live Mode Switcher*: Seamlessly switch between "Léa (Customer Skincare Advisor)" and "Copilot (Internal Operations & M365 Assistant)".

---

### Phase 3: Model Armor Guardrails & Cloud Trace Observability
- **Goal**: Timebox Model Armor prompt filtering and demonstrate defense-in-depth with Cloud Trace.
- **Deliverables**:
  1. Experiment with Google Cloud Model Armor as an input/output prompt sanitization layer.
  2. Articulate the interview distinction: Model Armor handles prompt sanitization/injection filtering; ADK Guardrail Callbacks enforce hard, deterministic tool authorization and write budgets.
  3. Verify end-to-end trace spans in Cloud Trace / OTLP for both agents.

---

### Phase 4: 15-Minute Live Interview Demo Plan
- **Goal**: Script the exact 15-minute walkthrough for the Friday interview.
- **Minute-by-Minute Breakdown**:
  - `00:00 - 02:00`: Problem Statement, Architectural Vision, and Tech Stack (Google ADK 2.13, Gemini on Vertex AI, MCP).
  - `02:00 - 06:00`: **Demo 1 — Beauty Advisor (Agentic RAG + HITL Claims)**:
    - Clarification without retrieval -> SQL-filtered product search + comparison -> 30-day policy citation.
    - Return claim opening with deterministic policy check and human review pause.
  - `06:00 - 10:00`: **Demo 2 — Internal Operations Copilot (M365 MCP + Governance)**:
    - Mixed request: M365 draft with authoritative catalog budget.
    - Governance in action: external exfil attempt blocked by code *before* human approval.
    - Cloud Trace waterfall & audit logs.
  - `10:00 - 12:30`: **Engineering Rigor & Evaluation**:
    - Show `evals/adk/` offline CI gate (<25s) and PR regression blocking.
    - Show live Vertex AI Gen AI Evaluation Service results (5.0/5.0 rubrics).
  - `12:30 - 15:00`: Key Architectural Decisions (ADR-001, ADR-002) & Q&A Defense.

---

### Phase 5: Technical Dossier PDF Generation (Max 5 Pages)
- **Goal**: Produce an executive PDF document (max 5 pages) to share with interviewers, summarizing the entire engineering scope.
- **Page Layout**:
  - **Page 1**: Executive Summary, Problem Context & Architecture Scope (Beauty Advisor & Operations Copilot).
  - **Page 2**: End-to-End System Architecture & Data Pipelines (Mermaid diagrams, RAG hybrid search + Discovery Engine cross-encoder reranking, MCP connector).
  - **Page 3**: Deterministic Governance, Guardrail Callbacks & Human-in-the-Loop ("Callbacks Give You Guardrail Control", `require_confirmation=True`).
  - **Page 4**: Evaluation Architecture & Quality Gates (ADK native evalset, Vertex AI EvalTask rubrics, CI/CD PR gate).
  - **Page 5**: Production Readiness, Cloud Run Deployment & Key Decisions (ADR-001, ADR-002, cost & latency trade-offs).
- **Execution**: Generated via headless Chrome from styled HTML/CSS with print-optimized pagination and SVG diagrams.

---

### Phase 6: Final Markdown Coherence Sweep & Presentation Polish
- **Goal**: Complete sweep of all repository documentation.
- **Tasks**:
  1. Ensure consistent naming, zero broken links, and identical test/latency numbers across `README.md`, `PROJECT_HANDOVER.md`, `DEMO.md`, and `ADR` files.
  2. Final verification of offline pytest (106 tests) and offline ADK evaluation runner.
