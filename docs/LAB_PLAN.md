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
- Prioritize role-relevant evidence: ADK tool calling, FastAPI/chat sessions, measured RAG choices, LangGraph
  checkpoints/human review, Pub/Sub retries/idempotency, evaluation and Google observability.
- Keep lab results distinct from professional production experience; this repository does not independently
  establish claims in the CV. “Zero leaks” means no planted canary/prompt leaks observed in specified runs,
  not a guarantee of security.

## Agreed sequence and current position

The original 8-hour allocation below is context, not a new estimate of time already spent or still available.

| Block | Original allocation | Verified position / finish line |
|---|---|---|
| 1. LangGraph claims + Pub/Sub | 2h30 | Main flow demonstrated in supplied run logs. Read-only inspection of local checkpoints independently confirmed both example claims are refunded with `next=[]`, one refund history entry each. Supplied worker logs show 503 failures, repeated retries and eventual success. Optional final evidence: observe outcome events without replaying a refund. |
| 2. ADK Native Evaluation + Vertex AI + GitHub Actions CI Gate | 1h | **Complete & Verified Green.** Replaced Promptfoo with ADK built-in evaluation framework (`evals/adk/`) and Vertex AI Gen AI Evaluation (`evals/vertex/`). Pure Python stack (uv, ADK 2.13, Vertex AI), zero Node.js/npm dependencies in CI. Automatic CI gate runs 106 Python tests + 7 deterministic ADK evaluations in ~21s on PRs and pushes to `master`. Regression blocking was proven on PR via branch `test/eval-gate-regression`. Live cloud evaluation workflow verified in GitHub Actions with GCP service account auth: 7/7 cases passed against Gemini 3.8 Flash + Discovery Engine ranking, followed by Vertex AI LLM-as-a-judge scoring 5.0/5.0. |
| 3. Model Armor + GCP tracing | 1h | GCP tracing verified: read-only API returned eight frontend traces with model/tool/RAG spans, correlated to KPI turn IDs. Saved `evals/tracing/advisor_20261111.summary.json`. Individual LangGraph nodes/worker continuation are not traced. Next: timebox one Model Armor experiment; distinguish sanitization from deterministic tool authorization. No Langfuse work. |
| 4. Neutralize, cleanup, review, docs, French demo | 2h30 | Pending. Fictional branding, tighten unsupported claim-status wording, rehearse architecture and failure recovery in French, concise commands and evidence. Avoid broad rewrites. |
| 5. Buffer | 1h | Preserve time for broken integration or interview rehearsal; don't consume it with optional production infrastructure. |

## Claims evidence and what it means

- Supplied history: CLM-B730C8 paused at `human_review`, reviewer decision published to Pub/Sub, separate worker
  resumed it, customer retrieved the refund status. Local persisted state matches refunded EUR 39.80.
- Supplied failure drill: CLM-A85A1E hit refund API 503, then Pub/Sub redelivered after NACKs; recovery produced
  `outcome=retried status=refunded`. Local persisted state matches refunded EUR 14.90, one refund history step.
- No decisions were republished, messages acknowledged, or refunds replayed during the Codex assessment.
- Defend the mechanism: at-least-once delivery + resumable checkpoint + idempotency key. The mock payment
  service's key store is in memory; don't claim universal exactly-once payment guarantees across restarts or
  concurrent workers. Failed node attempts aren't all retained in successful checkpoint history; the supplied
  worker logs are the retry evidence.
- Remaining known wording issue: the assistant added a bank-credit delay absent from the status tool result.
  Tighten during cleanup; do not invent dates or promises from general knowledge.

## Next practical step

The evaluation pipeline is fully modernized and verified in both offline CI (<25s gate, 106 unit tests + 7 deterministic ADK cases) and live cloud runs (7/7 cases + 5.0/5.0 Vertex AI LLM judge). PR blocking on regression was proven on branch `test/eval-gate-regression`.

Proceed to the Model Armor portion of the tracing/guardrail block. GCP export is verified on frontend turns: `POST /chat` → `chat.turn` → ADK workflow/agent → model/tool spans, with custom RAG attributes.
Follow with the cleanup and French rehearsal blocks (fictional branding, tighten claim-status wording, rehearse architecture and failure recovery). Do not let broad production tasks displace tracing/guardrail demonstration and French rehearsal.

Production gaps (auth/ownership, robust index metadata updates, cache freshness, persistent refund idempotency,
retention, deployment scaling) remain documented in `PROJECT_HANDOVER.md` as discussion material. Fix only what
blocks the selected demo or invalidates an interview claim within the remaining preparation time.
