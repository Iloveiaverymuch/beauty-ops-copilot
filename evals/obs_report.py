"""Latency/token report over many `adk eval` runs (the ADK efficiency metrics, aggregated).

Usage (one label per configuration you want to compare):
  T=$(date +%s); for i in 1 2 3; do <adk eval ...>; done
  uv run python evals/obs_report.py --since $T --label "eu thinking=default cache=off"
Appends a block to evals/LATENCY.md and prints it.

Per case: p50/p95 total s, model share, mean reasoning/output tokens, cached tokens. Overall: p50/p95/max.
p95 over n=3 runs x 10 cases = 30 samples: fine for A/B direction, too few for an SLO (that's D3.4).
"""
import argparse
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("--since", type=float, required=True, help="unix ts: include result files written after it")
ap.add_argument("--label", required=True)
a = ap.parse_args()


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, round(q * (len(xs) - 1))))] if xs else 0.0


files = [f for f in (ROOT / "copilot" / ".adk" / "eval_history").glob("*.evalset_result.json") if f.stat().st_mtime >= a.since]
per = defaultdict(lambda: defaultdict(list))
status = defaultdict(list)
for f in files:
    d = json.loads(f.read_text())
    d = json.loads(d) if isinstance(d, str) else d
    for c in d["eval_case_results"]:
        m = {x["metric_name"]: x for x in c["overall_eval_metric_results"]}
        dur = ((m.get("invocation_duration_v1") or {}).get("details") or {}).get("duration_details") or {}
        tok = ((m.get("token_usage_v1") or {}).get("details") or {}).get("token_usage_details") or {}
        e = per[c["eval_id"]]
        e["total"].append(dur.get("total_seconds") or 0)
        e["model"].append(dur.get("model_seconds") or 0)
        e["reason"].append(tok.get("reasoning_tokens") or 0)
        e["out"].append(tok.get("candidates_tokens") or 0)
        e["in"].append(tok.get("input_tokens") or 0)
        e["cached"].append(tok.get("cached_tokens") or 0)
        e["calls"].append((m.get("tool_call_count_v1") or {}).get("score") or 0)
        status[c["eval_id"]].append(c["final_eval_status"] == 1)

lines = [f"\n## {a.label}  ({time.strftime('%Y-%m-%d %H:%M')}, {len(files)} eval runs)",
         "| case | pass | p50 s | p95 s | model % | reasoning tok | answer tok | input tok | cached tok | calls |",
         "|---|---|---|---|---|---|---|---|---|---|"]
alls = []
for cid in sorted(per):
    e = per[cid]
    alls += e["total"]
    share = 100 * sum(e["model"]) / max(1e-9, sum(e["total"]))
    lines.append(f"| {cid} | {sum(status[cid])}/{len(status[cid])} | {pct(e['total'], .5):.1f} | {pct(e['total'], .95):.1f} "
                 f"| {share:.0f} | {statistics.mean(e['reason']):.0f} | {statistics.mean(e['out']):.0f} "
                 f"| {statistics.mean(e['in']):.0f} | {statistics.mean(e['cached']):.0f} | {statistics.mean(e['calls']):.1f} |")
tot_in = sum(sum(per[c]["in"]) for c in per)
tot_cached = sum(sum(per[c]["cached"]) for c in per)
lines.append(f"\n**Overall:** p50 {pct(alls, .5):.1f} s, p95 {pct(alls, .95):.1f} s, max {max(alls or [0]):.1f} s over "
             f"{len(alls)} invocations; cache hit ratio {100 * tot_cached / max(1, tot_in):.0f}% of input tokens.")
block = "\n".join(lines)
print(block)
out = ROOT / "evals" / "LATENCY.md"
if not out.exists():
    out.write_text("# Latency / token A/B log (from adk eval efficiency metrics)\n")
with out.open("a") as fh:
    fh.write(block + "\n")
