"""Efficiency gate on top of `adk eval` (ADK reports tool_call_count_v1 but refuses thresholds on it).

Budget per case = len(expected trajectory) + SLACK. A case can pass trajectory (IN_ORDER allows extras) and
hallucination checks while flailing: D2.1 cat_price made 5 calls for a 1-call lookup (SKU not searchable).
Reads the newest result in copilot/.adk/eval_history. Exit 1 on any violation -> usable as a CI gate.

Run: uv run python evals/check_eval_budgets.py [--slack 1] [--max-seconds 45]
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
from build_evalset import CASES  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--slack", type=int, default=1)
ap.add_argument("--max-seconds", type=float, default=45.0)
a = ap.parse_args()

files = sorted((ROOT / "copilot" / ".adk" / "eval_history").glob("*.evalset_result.json"), key=lambda p: p.stat().st_mtime)
if not files:
    sys.exit("no eval results found: run `adk eval` first")
data = json.loads(files[-1].read_text())
data = json.loads(data) if isinstance(data, str) else data
budget = {f"{cat}__{cid}": len(traj) + a.slack for cid, cat, _, traj, _ in CASES}

bad, rows = [], []
for c in data["eval_case_results"]:
    m = {x["metric_name"]: x.get("score") for x in c["overall_eval_metric_results"]}
    calls, secs, toks = m.get("tool_call_count_v1"), m.get("invocation_duration_v1"), m.get("token_usage_v1")
    lim = budget.get(c["eval_id"])
    over = (lim is not None and calls is not None and calls > lim) or (secs is not None and secs > a.max_seconds)
    rows.append((c["eval_id"], c["final_eval_status"], calls, lim, secs, toks, over))
    bad += [c["eval_id"]] if over else []

print(f"{files[-1].name}\n{'case':28}{'status':>8}{'calls':>7}{'budget':>8}{'secs':>8}{'tokens':>8}")
for eid, st, calls, lim, secs, toks, over in sorted(rows):
    st = {1: "PASSED", 2: "FAILED", 3: "NOT_RUN"}.get(st, str(st))
    print(f"{eid:28}{st:>8}{calls or 0:>7.0f}{lim or 0:>8}{secs or 0:>8.1f}{toks or 0:>8.0f}{'  OVER' if over else ''}")
print(f"\n{len(bad)} case(s) over budget" + (f": {bad}" if bad else ""))
sys.exit(1 if bad else 0)
