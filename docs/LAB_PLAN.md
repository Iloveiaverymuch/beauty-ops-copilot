# Operational Readiness Lab: Master Execution Plan (W0–W10)

Authority: 10-day operational readiness plan for an enterprise e-commerce ADK mission (omnichannel customer assistant, BFF/API/BigQuery/Kafka integration, GCP Cloud Run, LLMOps, and internal ADK referent enablement).
Purpose: Ensure this repository is an honest, verifiable reference architecture, every production pattern is implemented and understood cold, and an onboarding kit exists to train internal ADK referents.

---

## Non-Negotiable Operating Rules

- **SDD Workflow**: Spec in `docs/specs/SPEC-NNN-*.md` (using `docs/specs/TEMPLATE.md`) → explicit approval → smallest viable change → regression tests → `uv run scripts/verify.py` → `handover` skill → learning check (`docs/learning/WNN-*.md` + `explain-back`).
- **Offline by Default**: `scripts/verify.py` and CI run with zero network egress and zero cloud cost. Live Vertex AI / GCP runs are explicit and budgeted.
- **Strict Authorization**: Never commit, push, deploy, apply Terraform, or incur cloud spend without explicit approval.
- **Confidentiality**: Never mention client names, client product names, or internal systems in code, commits, branches, specs, or docs. Keep neutral wording and the beauty retail lab domain.
- **Honesty Over Polish**: Every number in `README.md`, `PROJECT_HANDOVER.md`, or demo scripts must map to a results file or `evals/FINDINGS.md` with its sample size `n`. Unknown cost is `"unknown"`, never `0`. Demo-only features are explicitly labelled.
- **Verify Installed ADK APIs**: Always check `uv pip show google-adk` (currently `2.13.0`) and inspect `.venv` source before coding against ADK classes.

---

## Master Roadmap (W0–W10)

| ID | Target | Workstream | Deliverables & Acceptance | Cut Order (if short on time) |
|---|---|---|---|---|
| **W0** | Day 1 AM | Business-card cleanup & docs consistency | Remove brand residue, link every README/demo metric to `evals/FINDINGS.md` with `n`, add `scripts/check_docs.py` to `verify.py`, ignore `output/` | Never cut |
| **W1** | Day 1–2 | Persistent sessions, multi-instance & ownership | Wire `DatabaseSessionService` (PostgreSQL via Docker / Cloud SQL target), prove 2-instance continuity, signed-token customer identity & reviewer RBAC negative tests, ADR (Cloud SQL vs `VertexAiSessionService` vs SQLite) | Never cut |
| **W2** | Day 2–3 | Advisor appointment booking | `book_advisor_appointment` (`hold_slot` → HITL `require_confirmation=True` → `confirm_slot` with `Idempotency-Key`), identity/store from state, transient-only retries, emits `appointment.confirmed`, `review-adk-tool` audit | Never cut |
| **W3** | Day 3–4 | OpenAPI tools, BFF aggregator & resilience | ADK OpenAPI toolset on mock stock/delivery API, thin BFF aggregator + measured comparison in `evals/FINDINGS.md`, circuit breaker + typed errors + timeouts | Keep if possible |
| **W4** | Day 4–5 | BigQuery safe analytics tool & long-term memory | Read-only allowlisted/parameterized BigQuery tool (`maximum_bytes_billed`, row limit, RO IAM) + ADR vs built-in/MCP Toolbox; cross-session memory with retention & GDPR deletion path | Cut memory part 3rd |
| **W5** | Day 6 | Event-driven integration with Kafka | Redpanda in Docker Compose, idempotent consumer (`stock.changed` / `product.updated` → cache refresh) with processed-event table & DLQ, `appointment.confirmed` producer, ADR Kafka vs Pub/Sub | Keep if possible |
| **W6** | Day 7 | Deployment on GCP (plan-only) | Terraform plan-only (Cloud Run + Cloud SQL + Secret Manager + least-privilege SAs + `--no-cpu-throttling` + `OTEL_TRACES_SAMPLER=always_on`), deploy runbook, ADR Cloud Run vs Vertex AI Agent Engine | Cut infra 2nd (keep ADR) |
| **W7** | Day 8 | LLMOps: KPIs, cost & eval loop | Per-turn KPIs (p50/p95, tokens, honest cost/`unknown`, errors, escalations, thumbs) → BigQuery + dashboard + alerts, anonymized thumbs-down export, non-zero exit on eval gate failure | Never cut |
| **W8** | Day 9 AM | A2A & multi-language awareness | Real A2A call between Operations Copilot and Léa (`advisor/a2a.py`), 1-page note on ADK in Java/Go/TypeScript | Cut 1st |
| **W9** | Day 9–10 | ADK referent kit & new skills | `docs/ADK_PLAYBOOK.md`, `templates/starter-agent/`, `docs/WORKSHOP_1H.md`, 4 new skills (`write-spec`, `add-adk-tool`, `add-eval-case`, `explain-back`) | Never cut |
| **W10** | Day 10 | Rehearsal & final sign-off | Update `docs/DEMO_15MIN.md` (W1/W2/W5/W7), final handover, all readiness checklist items & learning checks `≥ 4/5` | Never cut |

---

## Learning Protocol (After Every Workstream)

1. Write `docs/learning/WNN-<topic>.md`: problem, one-line mental model, failure modes, measurement, and code pointers.
2. Run `explain-back`: 5 interview-style questions (one at a time, scored 0–5 with one-line corrections).
3. Continuously re-test core concepts (persistent sessions, idempotent writes, transient-only retries, MRR, groundedness, `user:` prefix vs access control, temperature, dense vs BM25) until scored `≥ 4/5` twice on different days.
