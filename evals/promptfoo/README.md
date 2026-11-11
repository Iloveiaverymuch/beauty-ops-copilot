# HTTP evaluation lab

`/chat/sync` buffers the **same** execution as `/chat`: sessions, tool calls, UI events and KPIs are preserved.
It returns `{status, thread_id, turn_id, text, events, metrics}`. Pass `thread_id` back for another turn. A technical
fallback sets `status=error`; do not score HTTP 200 alone as success. The API remains async internally; “sync” means
one complete JSON response rather than an SSE stream, not a separate synchronous agent implementation.

Run from the repository root with Node >=22.22.0 (tested with Node 24.4.1) and the pinned Promptfoo 0.125.1:

```bash
# Evaluator regression tests: no network, credentials or installed npm dependencies.
node --test evals/promptfoo/check.test.cjs

# Offline wiring smoke: fixture agent behind the REAL FastAPI /chat/sync route (terminal 1).
SESSIONS=memory BACKEND_DATA_DIR=/tmp/beauty-eval uv run uvicorn evals.promptfoo.offline_app:app --host 127.0.0.1 --port 8011

# Terminal 2. First npx invocation downloads the CLI; no LLM calls in this offline run.
ADVISOR_URL=http://127.0.0.1:8011 PROMPTFOO_DISABLE_TELEMETRY=1 PROMPTFOO_DISABLE_UPDATE=1 \
  npx --yes promptfoo@0.125.1 eval -c evals/promptfoo/promptfooconfig.yaml --no-cache --max-concurrency 1 -o /tmp/beauty-promptfoo.json
```

For a live run, start `mock_api.stock_api:app` on 8090 and the real `backend.app:app` on 8000 using the existing
GCP env, then set `ADVISOR_URL=http://127.0.0.1:8000`. Use a separate `BACKEND_DATA_DIR` so evaluation conversations
do not mix with demo sessions. This needs Vertex, BigQuery and ranking access and incurs usage charges. The seven
cases do not open/refund claims; the claim case looks up the deliberately unknown `CLM-000000`.
Do not point this lab configuration at a real customer system.

Checks cover clarification, product filter compliance, current policy/citations, deterministic quantities,
off-topic tool behavior, planted secret strings, and unknown-claim lookup. They score structured tool-driven
events as well as text. They do **not** prove broad grounding, semantic refusal quality, multi-turn behavior or
general prompt-injection resistance. Keep `evals/advisor/run_agent_eval.py` for the richer conversations/judge.

HTTP retries are disabled and caching is disabled for fresh measurements. Serial execution limits API load and
avoids parallel claims/checkpoint interactions. Normal backend model retries still apply.
Promptfoo exits nonzero for failed assertions (default 100); result JSON includes individual failures.
The CLI is optional development tooling, not a Python/runtime dependency.

GitHub Actions configuration: `.github/workflows/ci.yml` runs the **CI gate** on pull requests and pushes to
`master`. It runs Python regression tests, Node evaluator regressions and the fixture-backed HTTP suite without
GCP credentials. Actions are pinned to verified release commit SHAs; reports upload even after test failures.

To run live evaluations, use **Actions → CI → Run workflow**, select `run_live_evals`, and supply `advisor_url`
pointing to an isolated lab backend reachable **from the GitHub runner**, not your laptop's loopback address.
The backend owns its cloud credentials; this workflow does not deploy it or configure GCP authentication. The live
job waits for the offline gate and fails normally on assertion errors. The known off-topic regression may fail it.
Keep live checks manual; the default automatic gate verifies deterministic behavior and wiring, not live LLM quality.

After the first GitHub run, configure the `master` branch ruleset/protection to require the **CI gate** status check
before merging. The workflow supplies that check; merge enforcement is a separate repository setting, unchanged
here. Local verification: `actionlint 1.7.12` passed, 92 Python tests passed, 10 Node tests passed and the CI HTTP
fixture smoke passed 7/7. This does not establish that a remote runner has executed. No deployment is included.

Official references: [HTTP provider](https://www.promptfoo.dev/docs/providers/http/),
[JavaScript assertions](https://www.promptfoo.dev/docs/configuration/expected-outputs/javascript/),
[CLI exit codes](https://www.promptfoo.dev/docs/usage/command-line/),
[GitHub manual workflows](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow),
[required status checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).

## Observed live run (2026-11-11)

Seven real-backend cases completed without infrastructure errors, one run each. Original checks passed 7/7;
manual review found the off-topic answer gave “Tokyo” before redirecting. Checking zero tool calls alone missed
that scope violation. The off-topic case now also rejects its known answer, and regression tests cover the false
pass. Rescoring the same saved responses offline gives **6/7**, with only off-topic failing. This literal check is
case-specific, not a general semantic classifier. Prompt cleanup and a targeted live retest remain pending.

The seven observed turn latencies have median 3652 ms and maximum 6068 ms (small warm-process sample, not an SLO).
Backend metrics record actual token counts; Promptfoo does not automatically populate token usage from this HTTP
response. Summary: `results/live_20261111.summary.json`; full local report: `/private/tmp/beauty-promptfoo-live.json`.
No claim-opening/refund tools were exercised. Node evaluator regressions: 10 passed. Remote CI remains unrun.
