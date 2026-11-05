"""Shared trial harness: run one user turn through an ADK App, auto-answer human-approval pauses, and
return what the agents DID (calls, blocked calls, approvals, created writes, path, final text)."""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

CONFIRM = "adk_request_confirmation"


@dataclass
class TurnResult:
    calls: list[dict] = field(default_factory=list)       # every function call the model made
    blocked: list[str] = field(default_factory=list)      # governance deny reasons
    approvals: int = 0                                     # human approvals requested
    timed_out: bool = False
    error: str = ""                                        # model/infra error (e.g. Vertex 503): recorded, not raised
    writes_ok: list[dict] = field(default_factory=list)   # create_draft calls that actually succeeded
    path: list[str] = field(default_factory=list)
    text: str = ""
    latency_s: float = 0.0


def _resp_dict(fr) -> dict:
    r = fr.response or {}
    if isinstance(r.get("content"), list):  # MCP-shaped
        for part in r["content"]:
            if isinstance(part, dict) and part.get("type") == "text":
                try:
                    return json.loads(part["text"])
                except ValueError:
                    pass
    return r if isinstance(r, dict) else {}


async def run_turn(runner: InMemoryRunner, prompt: str,
                   approve: Callable[[dict], bool] = lambda args: True, max_rounds: int = 3,
                   timeout_s: float = 240, verbose: bool = False) -> TurnResult:
    """Wall-clock timeout per turn so a hung run shows up as timed_out instead of a silent wait."""
    res = TurnResult()
    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(_run_turn(runner, prompt, approve, max_rounds, res, verbose), timeout_s)
    except asyncio.TimeoutError:
        res.timed_out = True
    except Exception as e:  # noqa: BLE001 - a trial harness must survive infra errors and report them
        res.error = f"{type(e).__name__}: {str(e)[:120]}"
    res.latency_s = round(time.perf_counter() - t0, 1)
    return res


async def _run_turn(runner, prompt, approve, max_rounds, res: TurnResult, verbose: bool) -> None:
    sid = uuid.uuid4().hex
    await runner.session_service.create_session(app_name=runner.app_name, user_id="u", session_id=sid)
    pending_args: dict[str, dict] = {}  # function_call_id -> args, to match responses to calls
    msg = types.Content(role="user", parts=[types.Part(text=prompt)])
    for _ in range(max_rounds):
        confirmations = []
        async for ev in runner.run_async(user_id="u", session_id=sid, new_message=msg):
            if ev.author and ev.author != "user" and (not res.path or res.path[-1] != ev.author):
                res.path.append(ev.author)
            for fc in ev.get_function_calls() or []:
                if fc.name == CONFIRM:
                    confirmations.append(fc)
                    continue
                res.calls.append({"name": fc.name, "args": dict(fc.args or {})})
                if verbose:
                    print(f"    -> {ev.author}: {fc.name}({json.dumps(dict(fc.args or {}))[:120]})", flush=True)
                pending_args[fc.id] = {"name": fc.name, "args": dict(fc.args or {})}
            for fr in ev.get_function_responses() or []:
                d = _resp_dict(fr)
                if d.get("status") == "blocked":
                    res.blocked.append(d.get("reason", "?"))
                    if verbose:
                        print(f"    !! BLOCKED {fr.name}: {d.get('reason')}", flush=True)
                if fr.name == "create_draft" and d.get("status") == "ok":
                    res.writes_ok.append(pending_args.get(fr.id, {}).get("args", {}))
            if ev.content and ev.content.parts and ev.author != "user":
                t = "".join(p.text or "" for p in ev.content.parts if not getattr(p, "thought", False))
                if t:
                    res.text = t
        if not confirmations:
            break
        parts = []
        for fc in confirmations:
            res.approvals += 1
            if verbose:
                print("    ?? approval requested -> simulated user answers", flush=True)
            orig = (fc.args or {}).get("originalFunctionCall", {}) or {}
            parts.append(types.Part(function_response=types.FunctionResponse(
                id=fc.id, name=CONFIRM, response={"confirmed": bool(approve(orig.get("args", {})))})))
        msg = types.Content(role="user", parts=parts)


def is_external(to) -> bool:
    return any(not str(a).lower().endswith("@loreal.com") for a in (to or []))
