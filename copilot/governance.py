"""Governance plugin: central tool-call policy for every agent in the app (incl. AgentTool sub-runs).

Why a plugin and not per-agent callbacks: a platform owns policy; agent authors can't forget to attach it,
and AgentTool(include_plugins=True) propagates it into nested runs.

Order of enforcement for one tool call:
  1. before_tool (this plugin)  -> deny-by-default allowlist, per-turn budgets, recipient policy
  2. tool.run_async             -> ADK native require_confirmation (human approval) for the WRITE toolset
  3. MCP server                 -> bearer + audience + scopes
  4. after_tool (this plugin)   -> audit + taint tracking (Confidential content seen this turn)
Policy runs BEFORE approval on purpose: never ask a human to approve what policy forbids.

The write set is OUR list, not the MCP server's read_only_hint (annotations are hints from the server; a
compromised server could mislabel). We cross-check and alert on mismatch.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from google.adk.plugins import BasePlugin
from google.adk.tools import BaseTool, ToolContext

POLICY_VERSION = "gov/v1"
INTERNAL_DOMAINS = tuple(os.getenv("GOV_INTERNAL_DOMAINS", "loreal.com").split(","))

audit_log = logging.getLogger("governance.audit")


@dataclass(frozen=True)
class AgentPolicy:
    allow: frozenset[str]
    writes: frozenset[str] = field(default_factory=frozenset)
    max_calls: int = 6      # per agent per turn: stops search sprees (D1.4: 9 calls on a vague prompt)
    max_writes: int = 1     # per agent per turn: stops unrequested drafts (D1.3 naive: 1-3 per run)


_CATALOG = AgentPolicy(allow=frozenset({"search_products", "get_campaign"}), max_calls=4)
POLICY: dict[str, AgentPolicy] = {
    "copilot": AgentPolicy(allow=frozenset({"transfer_to_agent"}), max_calls=2),
    "m365_agent": AgentPolicy(
        allow=frozenset({"search_mail", "list_events", "search_files", "create_draft",
                         "catalog_lookup", "transfer_to_agent"}),
        writes=frozenset({"create_draft"}), max_calls=6, max_writes=1),
    "catalog_agent": AgentPolicy(allow=_CATALOG.allow | {"transfer_to_agent"}, max_calls=4),
    "catalog_lookup": _CATALOG,
}
_DENY_ALL = AgentPolicy(allow=frozenset(), max_calls=0)

_MESSAGES = {
    "tool_not_allowed": "This tool is not allowed for this agent. Do not retry; answer with what you have.",
    "tool_budget_exceeded": "Tool-call budget for this turn is used up. Stop calling tools and answer with what you "
                            "have, saying what you could not check.",
    "write_budget_exceeded": "Only one write is allowed per turn. Do not create more drafts; tell the user what else "
                             "you would draft and ask them to confirm.",
    "external_recipient": "Drafts to recipients outside the company are blocked by policy. Do not retry with other "
                          "addresses; tell the user which recipient was blocked and why.",
    "no_recipient": "A draft needs at least one recipient named by the user.",
}


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _domain(addr: str) -> str:
    return str(addr).rsplit("@", 1)[-1].lower()


def _is_internal(addr: str) -> bool:
    return _domain(addr) in INTERNAL_DOMAINS


def _mcp_payload(result: Any) -> dict:
    """MCP results arrive as {"content":[{"type":"text","text":"<json>"}], "isError":...}; unwrap best-effort."""
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        for part in result["content"]:
            if isinstance(part, dict) and part.get("type") == "text":
                try:
                    return json.loads(part["text"])
                except (ValueError, TypeError):
                    return {}
    return result if isinstance(result, dict) else {}


class GovernancePlugin(BasePlugin):
    def __init__(self, policy: dict[str, AgentPolicy] | None = None, enforce: bool = True):
        super().__init__(name="governance")
        self.policy = policy or POLICY
        self.enforce = enforce  # False = audit-only ("shadow mode"): log decisions, block nothing

    # ---- helpers -------------------------------------------------------------------------------
    def _audit(self, ctx: ToolContext, tool: str, decision: str, reason: str = "", **extra: Any) -> None:
        rec = {"event": "tool_decision", "policy": POLICY_VERSION, "ts": round(time.time(), 3),
               "invocation_id": ctx.invocation_id, "agent": ctx.agent_name, "tool": tool,
               "decision": decision, "reason": reason, "enforced": self.enforce, **extra}
        audit_log.info(json.dumps(rec))
        if path := os.getenv("GOV_AUDIT_FILE"):
            with open(path, "a") as fh:
                fh.write(json.dumps(rec) + "\n")

    def _deny(self, ctx: ToolContext, tool: str, reason: str, **extra: Any) -> dict | None:
        self._audit(ctx, tool, "deny", reason, **extra)
        if not self.enforce:
            return None
        return {"status": "blocked", "policy": POLICY_VERSION, "reason": reason, "message": _MESSAGES[reason]}

    # ---- hooks ---------------------------------------------------------------------------------
    async def before_tool_callback(self, *, tool: BaseTool, tool_args: dict[str, Any],
                                   tool_context: ToolContext) -> dict | None:
        agent, name, st = tool_context.agent_name, tool.name, tool_context.state
        pol = self.policy.get(agent, _DENY_ALL)
        resumed = tool_context.tool_confirmation is not None  # re-execution after human approval: don't double count
        args_info = {"args_sha": _sha(tool_args)}

        if name not in pol.allow:
            return self._deny(tool_context, name, "tool_not_allowed", **args_info)

        key_calls = f"temp:gov:{agent}:calls"
        calls = st.get(key_calls, 0) + (0 if resumed else 1)
        st[key_calls] = calls
        if calls > pol.max_calls:
            return self._deny(tool_context, name, "tool_budget_exceeded", calls=calls, **args_info)

        is_write = name in pol.writes
        ann = getattr(getattr(tool, "_mcp_tool", None), "annotations", None)
        if ann is not None and ann.read_only_hint is True and is_write:
            self._audit(tool_context, name, "alert", "annotation_mismatch")  # server claims read-only; we disagree

        if is_write:
            key_w = f"temp:gov:{agent}:writes"
            # RESERVE the write slot at decision time (not on completion): the model can emit several
            # function calls in ONE response, and all their before-checks run before any tool executes.
            # Counting on completion = check-then-act race (gov_demo write_budget: 2 drafts slipped through).
            if not resumed and st.get(key_w, 0) >= pol.max_writes:
                return self._deny(tool_context, name, "write_budget_exceeded", **args_info)
            to = tool_args.get("to") or []
            if not to:
                return self._deny(tool_context, name, "no_recipient", **args_info)
            external = sorted({_domain(a) for a in to if not _is_internal(a)})
            if external:
                return self._deny(tool_context, name, "external_recipient", external_domains=external,
                                  confidential_seen=st.get("temp:gov:confidential", []), **args_info)

        if is_write and not resumed:
            st[f"temp:gov:{agent}:writes"] = st.get(f"temp:gov:{agent}:writes", 0) + 1  # reservation
        self._audit(tool_context, name, "allow", resumed=resumed, write=is_write, **args_info)
        return None

    async def after_tool_callback(self, *, tool: BaseTool, tool_args: dict[str, Any],
                                  tool_context: ToolContext, result: dict) -> dict | None:
        agent, name, st = tool_context.agent_name, tool.name, tool_context.state
        payload = _mcp_payload(result)
        if name in self.policy.get(agent, _DENY_ALL).writes and payload.get("status") == "error":
            key_w = f"temp:gov:{agent}:writes"  # release the reservation: a failed write didn't happen
            st[key_w] = max(0, st.get(key_w, 0) - 1)
        if name == "search_files":  # taint: remember Confidential files that entered the context this turn
            conf = [f["name"] for f in payload.get("files", []) if f.get("sensitivity") == "Confidential"]
            if conf:
                st["temp:gov:confidential"] = sorted(set(st.get("temp:gov:confidential", [])) | set(conf))
                self._audit(tool_context, name, "taint", "confidential_in_context", files=conf)
        if payload.get("status") == "error":
            self._audit(tool_context, name, "tool_error", payload.get("error_code", "unknown"))
        return None
