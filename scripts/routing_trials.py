"""D1.4 experiment: transfer vs AgentTool composition on single-domain and MIXED requests.

Needs the MCP server running. Run:
  uv run --env-file copilot/.env python scripts/routing_trials.py --n 3
Scores OUTCOMES (draft content / answer facts), and records the agent PATH (event authors) and calls.
"""
import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from copilot.agent import build_root  # noqa: E402
from copilot.m365_agent import build_m365_toolset  # noqa: E402


def _draft_to(calls, who):
    return [c["args"] for c in calls if c["name"] == "create_draft" and who in json.dumps(c["args"].get("to"))]


CASES = [
    ("catalog_only", "What's the price of GAR-SER-002?",
     lambda calls, text: "11.5" in text or "11,5" in text),
    ("m365_only", "What meetings do I have on Thursday 5 November 2026?",
     lambda calls, text: "steerco" in text.lower()),
    ("mixed_write", "Draft an email to sophie.martin@loreal.com summarizing the Q4 Garnier campaign: budget, channels and hero SKUs.",
     lambda calls, text: any("450" in d.get("body", "") and "GAR-SER-00" in d.get("body", "")
                             for d in _draft_to(calls, "sophie.martin"))),
    ("mixed_read", "Check Sophie's latest email about Garnier and tell me the prices of the hero SKUs she mentions.",
     lambda calls, text: ("12.9" in text or "12,9" in text) and ("11.5" in text or "11,5" in text)),
]


async def run_once(runner, prompt):
    sid = uuid.uuid4().hex
    await runner.session_service.create_session(app_name=runner.app_name, user_id="u", session_id=sid)
    calls, path, text, t0 = [], [], "", time.perf_counter()
    msg = types.Content(role="user", parts=[types.Part(text=prompt)])
    async for ev in runner.run_async(user_id="u", session_id=sid, new_message=msg):
        if ev.author and ev.author != "user" and (not path or path[-1] != ev.author):
            path.append(ev.author)
        for fc in ev.get_function_calls() or []:
            calls.append({"name": fc.name, "args": dict(fc.args or {})})
        if ev.content and ev.content.parts and ev.author != "user":
            t = "".join(p.text or "" for p in ev.content.parts if not getattr(p, "thought", False))
            if t:
                text = t  # keep the LAST text the user would see
    return calls, path, text, round(time.perf_counter() - t0, 1)


async def main(n, modes):
    summary = {}
    for mode in modes:
        toolset = build_m365_toolset()
        runner = InMemoryRunner(agent=build_root(mode, toolset), app_name="routing")
        ok = {}
        try:
            for name, prompt, check in CASES:
                ok[name] = 0
                for i in range(n):
                    calls, path, text, dt = await run_once(runner, prompt)
                    passed = bool(check(calls, text))
                    ok[name] += passed
                    print(f"[{mode}] {name} #{i}: {'PASS' if passed else 'FAIL'} path={'>'.join(path)} "
                          f"calls={[c['name'] for c in calls]} {dt}s")
                    if not passed:
                        print(f"      last text: {text[:200]!r}")
        finally:
            await toolset.close()
        summary[mode] = ok
    print("\n=== SUMMARY (passes / n) ===")
    print(f"{'case':14}" + "".join(f"{m:>12}" for m in modes))
    for name, *_ in CASES:
        print(f"{name:14}" + "".join(f"{summary[m][name]:>9}/{n:<2}" for m in modes))
    (ROOT / "evals" / "routing_trials.json").write_text(json.dumps({"ts": time.time(), "n": n, "summary": summary}, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--modes", nargs="+", default=["transfer", "agenttool"])
    a = ap.parse_args()
    asyncio.run(main(a.n, a.modes))
