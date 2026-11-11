# Interview lab: remaining plan

Authority: the user supplied the agreed plan and previous assistant's handoff on 2026-11-11. Interview is Friday
2026-11-13, with one preparation day remaining. Purpose: understand, demonstrate and defend working backend/agent
designs. This is an interview lab, not a production-hardening project. The opportunity's client identity is
confidential and must not be added to the repository or used for outreach.

## Decisions already made

- Neutralize the former company branding into a fictional retail demo. Do this in the cleanup block; preserve
  fixture/test consistency and don't blindly rewrite historical evaluation evidence.
- Use GCP tracing/Cloud Trace; no new Langfuse integration (the user already has another Langfuse lab).
- Use **GitHub Actions**, matching the GitHub repository. This replaces the GitLab CI named in the original
  schedule. The automatic CI gate is offline; live model checks are explicit manual runs.
- Continue step by step with explanations, one bounded milestone at a time. Retain a working demo.
- Prioritize role-relevant evidence: ADK tool calling, FastAPI/chat sessions, measured RAG choices, ADK Guardrail Callbacks & native HITL confirmation, evaluation and Google observability.
- Keep lab results distinct from professional production experience; this repository does not independently
  establish claims in the CV. “Zero leaks” means no planted canary/prompt leaks observed in specified runs,
  not a guarantee of security.

## Agreed sequence and current position

The original 8-hour allocation below is context, not a new estimate of time already spent or still available.

| Block | Original allocation | Verified position / finish line |
|---|---|---|
| 1. Unified Claims Governance (ADK Guardrail Callbacks + HITL) | 2h | **Next Milestone (Migrating from LangGraph to 100% ADK).** Eliminate LangGraph to achieve full architectural coherence. Use ADK native Callbacks ("Callbacks Give You Guardrail Control") and Human-In-The-Loop (HITL) tool confirmation (`require_confirmation=True`) for claims validation. Deterministic return policy rules and human review routing are enforced at the ADK tool/plugin boundary without external graph orchestrator dependencies. |
| 2. ADK Native Evaluation + Vertex AI + GitHub Actions CI Gate | 1h | **Complete & Verified Green.** Native ADK evaluation framework (`evals/adk/`) and Vertex AI Gen AI Evaluation (`evals/vertex/`). Pure Python stack (uv, ADK 2.13, Vertex AI), zero Node.js/npm dependencies in CI. Automatic CI gate runs 106 Python tests + 7 deterministic ADK evaluations in ~21s on PRs and pushes to `master`. Regression blocking was proven on PR via branch `test/eval-gate-regression`. Live cloud evaluation workflow verified in GitHub Actions with GCP service account auth: 7/7 cases passed against Gemini 3.8 Flash + Discovery Engine ranking, followed by Vertex AI LLM-as-a-judge scoring 5.0/5.0. |
| 3. Model Armor + GCP tracing | 1h | GCP tracing verified: read-only API returned eight frontend traces with model/tool/RAG spans, correlated to KPI turn IDs. Saved `evals/tracing/advisor_20261111.summary.json`. Individual LangGraph nodes/worker continuation are not traced. Next: timebox one Model Armor experiment; distinguish sanitization from deterministic tool authorization. No Langfuse work. |
| 4. Neutralize, cleanup, review, docs, French demo | 2h30 | Pending. Fictional branding, tighten unsupported claim-status wording, rehearse architecture and failure recovery in French, concise commands and evidence. Avoid broad rewrites. |
| 5. Buffer | 1h | Preserve time for broken integration or interview rehearsal; don't consume it with optional production infrastructure. |

## Claims governance and HITL design (ADK Guardrail Callbacks)

- **Architecture Unification**: The previous prototype used a hybrid LangGraph state machine alongside the ADK agent for claims. To make the architecture 100% coherent, explainable, and interview-ready, LangGraph is being replaced entirely by **ADK Native Guardrail Callbacks** ("Callbacks Give You Guardrail Control") and native Human-In-The-Loop (HITL) approval (`require_confirmation=True`).
- **Guardrails at the Callback Level**:
  - Deterministic checks (30 days unopened, photo provided for reactions) run inside ADK `before_tool_callback` / policy plugins before any state mutation.
  - Claims requiring human intervention (e.g. skin reaction reports or edge cases) trigger ADK's native confirmation workflow (`require_confirmation=True`), pausing execution until a human advisor reviews and authorizes the refund.
  - Eliminates external graph checkpointers, duplicate state engines, and dual-stack orchestration complexity.

## Next practical step

Execute the **Claims Modernization** milestone:
1. Replace the LangGraph claims state machine with ADK Native Tool Callbacks (`before_tool_callback`) and `require_confirmation=True` on `open_return_claim`.
2. Remove LangGraph dependencies (`langgraph`, `langgraph-checkpoint-sqlite`) from `pyproject.toml` and `backend/`.
3. Verify that the 106 tests and 7 ADK evaluation benchmarks remain 100% green under pure ADK.
4. Follow with Model Armor guardrail integration, fictional branding cleanup, and French interview rehearsal.

Production gaps (auth/ownership, robust index metadata updates, cache freshness, persistent refund idempotency,
retention, deployment scaling) remain documented in `PROJECT_HANDOVER.md` as discussion material. Fix only what
blocks the selected demo or invalidates an interview claim within the remaining preparation time.
