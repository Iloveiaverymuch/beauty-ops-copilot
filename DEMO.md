# 5-minute demo: Beauty Ops Copilot (ADK + Vertex AI + MCP, governed, on Cloud Run)

Goal: in 5 minutes, show a deployed multi-agent backend whose behaviour is **measured** and whose risky actions are
**blocked by code and visible in telemetry**. Evidence for every number: `evals/FINDINGS.md`.

## T-15 min: preparation (never demo cold)
```bash
# block 0 of deploy/DEPLOY.md, then:
gcloud run services proxy copilot-api --port=8000          # Chrome: http://localhost:8000/dev-ui/  (app: copilot)
```
- **Warm up** both services (cold start + model tail): send `What's the price of GAR-SER-002?` once and wait for the answer.
- If the `eu` endpoint is slow today: `gcloud run services update copilot-api --update-env-vars=GOOGLE_CLOUD_LOCATION=global`
  and say so during the demo (it's a residency decision, not a hack).
- Open tabs: Cloud Trace Explorer (filter `governance.denied = true`), Logs Explorer (copilot-api), this file,
  `evals/FINDINGS.md`, `docs/ADR-001.md`.
- **Check traces export** (they silently didn't on the first deploy, see DEPLOY.md troubleshooting): send the 2:00
  prompt once, take the newest deny's `trace` in Logs Explorer, open it via Trace Explorer → Trace ID.
- **Fallback:** if live breaks (eu tail, cold start, proxy), switch without apologizing: "here's the same run from
  this morning". Screenshots (2026-11-08, deployed service) are in [Fallback screenshots](#fallback-screenshots) at the
  end of this file, in script order.

## The script

| t | Show | Say (one or two sentences) |
|---|---|---|
| 0:00 | README architecture diagram | "A router and two specialist agents on ADK, Gemini 3.8 Flash on Vertex. The M365 agent reaches mail, calendar and files only through an MCP server. A governance plugin sits between what the model *wants* and what *happens*. Two private Cloud Run services." |
| 0:40 | Dev UI: `Draft an email to sophie.martin@loreal.com summarizing the Q4 Garnier campaign: budget, channels and hero SKUs.` → **Approve** | "Mixed request: the M365 agent calls the catalog as a tool, so the budget comes from the authoritative source, not from an email. My eval asserts that trajectory: a passing answer built from Finance's email was a real bug." |
| 1:30 | Cloud Trace: that request's waterfall | "Here's the cost of one request: 4 model calls, the tools take milliseconds, the model takes seconds. One model call can be 80% of the latency, and it's provider-bound: cutting thinking 90% didn't make it faster." |
| 2:00 | Dev UI: `Draft an email to riadh.test@gmail.com saying 'see you at VivaTech'.` | "The model tries. The code says no, before any human is asked to approve." |
| 2:20 | Trace Explorer filter `governance.denied = true` → the `execute_tool create_draft` span | "Every blocked action is a span with the reason, next to the model call that attempted it, and no email content: capture is off. The tool never ran: no MCP call under it." |
| 2:45 | Logs Explorer: the WARNING audit line → click trace link; metric `governance_denies` | "Same decision as a structured log, linked to the trace, counted by a metric I can alert on." |
| 3:10 | `evals/FINDINGS.md`, prompt × governance matrix | "Why code and not just prompts: with a naive prompt and a human who approves everything, the agent drafted campaign budgets to a fake external auditor 3 times out of 3. With policy on: 4 attempts, 4 blocked. And a hardened prompt that stopped attacks refused all legitimate work: safety 12/12, utility 0/3. I measure both axes on every change." |
| 3:50 | Terminal: block 7 curls (403 / 401) | "Two locks on the connector: Cloud Run IAM stops anonymous calls, 403. I'm project Owner and still get 401, because the MCP server maps the *caller's identity* to scopes, and only the copilot's service account has them." |
| 4:20 | ADR §3-4 + "what's next" | "For L'Oréal: the last hop becomes Entra on-behalf-of so Microsoft enforces the user's own permissions; governance lives in the platform, rolled out in shadow mode first; the eval suite gates every prompt and policy change in CI." |
| 4:50 | Stop | Invite questions. |

## Questions they will likely ask (and where the answer is)
| Question | Short answer | Evidence |
|---|---|---|
| Why not just a stronger system prompt? | Prompts change probabilities, code gives guarantees; 0/15 at n=15 still allows ~20% | FINDINGS D1.3, D2.1 matrix |
| Human-in-the-loop is enough, no? | A rubber-stamp approver approved 4/4 exfils; policy runs before approval | ADR §4 |
| How do you evaluate an agent? | Two layers: trajectory+groundedness regression, adversarial+utility suite with side effects; gate on behaviour | ADR §6 |
| What broke? | Parallel function calls race, approval-resume invocation scope, provenance, eval checker false-fail, 4 deploy bugs | FINDINGS, DEPLOY.md troubleshooting |
| Latency? | Provider-bound, tail 5-10x; p95 SLO, timeouts + retries, eu vs global trade-off, Provisioned Throughput | ADR §1, §8 |
| Data residency / sovereignty? | eu endpoint default, explicit switch; S3NS: Cloud Run/Vertex/Gemini availability lags, GKE + open-weight path, re-run evals | ADR §1, §9 |
| RAG? | Own store (AlloyDB pgvector / RAG Engine) for shared corporate knowledge; federated retrieval via Graph for M365 (permission trimming) | ADR §3 |
| Agent Engine vs Cloud Run? | Cloud Run for exporter/middleware control in the POC; Agent Engine for managed sessions/memory, watch forced content capture | ADR §9 |

## Fallback screenshots

### 2:00 Policy blocks the draft before any approval
![2:00 Policy blocks the draft before any approval](docs/demo/01-ui-blocked.png)

`create_draft` to an external recipient returns `blocked / external_recipient`; no confirmation dialog: policy runs before the human.

### 1:30 / 2:20 The production trace
![1:30 / 2:20 The production trace](docs/demo/02-trace-waterfall.png)

15 spans, 21.7 s, 4.5K in / 534 out tokens. Model calls ≈ 15 s; MCP initialize ≈ 35% of the turn (cold start + handshake per toolset). `(Missing span ID)` = Cloud Run's own unsampled parent.

### 2:20 The denied span
![2:20 The denied span](docs/demo/03-trace-denied-span.png)

`governance.denied = true`, `governance.reason = external_recipient` on `execute_tool create_draft`; no prompt or mail content in the attributes.

### 2:45 The audit line, linked to the same trace
![2:45 The audit line, linked to the same trace](docs/demo/04-audit-log.png)

Structured WARNING in Cloud Logging: reason, policy version, external domain; `trace` = the same ID as the waterfall above.

### 2:45 The metric you alert on
![2:45 The metric you alert on](docs/demo/05-metric.png)

`governance_denies` per 5 min. Logs show 6 denies, the metric 5: a log-based metric never backfills entries from before it existed.

### 3:10 The prompt layer, for contrast
![3:10 The prompt layer, for contrast](docs/demo/06-ui-prompt-refusal.png)

hardened_v2 flags the injected msg-005 and makes no tool call. Prompts lower the odds; the policy above is the guarantee.

