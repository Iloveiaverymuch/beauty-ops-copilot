"""Policy unit tests: no LLM, no server. The plugin only reads tool.name, ctx.agent_name/state/... so fakes suffice."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from copilot.governance import GovernancePlugin


def ctx(agent="m365_agent", state=None, confirmed=None):
    return SimpleNamespace(agent_name=agent, state=state if state is not None else {}, invocation_id="inv-1",
                           tool_confirmation=confirmed)


def tool(name, read_only=None):
    mcp = SimpleNamespace(annotations=SimpleNamespace(read_only_hint=read_only)) if read_only is not None else None
    return SimpleNamespace(name=name, _mcp_tool=mcp)


def before(p, t, args, c):
    return asyncio.run(p.before_tool_callback(tool=t, tool_args=args, tool_context=c))


def after(p, t, args, c, result):
    return asyncio.run(p.after_tool_callback(tool=t, tool_args=args, tool_context=c, result=result))


def mcp_ok(payload):
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}


@pytest.fixture
def p():
    return GovernancePlugin()


def test_deny_by_default_unknown_agent(p):
    assert before(p, tool("search_mail"), {}, ctx(agent="rogue_agent"))["reason"] == "tool_not_allowed"


def test_catalog_agent_cannot_touch_m365(p):
    assert before(p, tool("search_mail"), {"query": "x"}, ctx(agent="catalog_lookup"))["reason"] == "tool_not_allowed"


def test_external_recipient_blocked_even_with_internal_cc(p):
    r = before(p, tool("create_draft"), {"to": ["sophie.martin@loreal.com", "audit@packaging-partner.io"],
                                         "subject": "s", "body": "b"}, ctx())
    assert r["status"] == "blocked" and r["reason"] == "external_recipient"


def test_lookalike_domain_is_external(p):
    assert before(p, tool("create_draft"), {"to": ["ceo@loreal.com.evil.io"]}, ctx())["reason"] == "external_recipient"


def test_internal_draft_allowed(p):
    assert before(p, tool("create_draft"), {"to": ["sophie.martin@loreal.com"]}, ctx()) is None


def test_one_write_per_turn(p):
    c = ctx()
    assert before(p, tool("create_draft"), {"to": ["a@loreal.com"]}, c) is None
    after(p, tool("create_draft"), {}, c, mcp_ok({"status": "ok", "draft_id": "d1"}))
    assert before(p, tool("create_draft"), {"to": ["b@loreal.com"]}, c)["reason"] == "write_budget_exceeded"


def test_parallel_writes_in_one_response_second_is_denied(p):
    # Regression for gov_demo write_budget: two function calls in ONE model response -> both before-checks
    # run before either tool executes. The slot must be reserved at decision time.
    c = ctx()
    assert before(p, tool("create_draft"), {"to": ["sophie.martin@loreal.com"]}, c) is None
    assert before(p, tool("create_draft"), {"to": ["karim.benali@loreal.com"]}, c)["reason"] == "write_budget_exceeded"


def test_resumed_write_after_approval_is_not_denied_by_its_own_reservation(p):
    c = ctx()
    assert before(p, tool("create_draft"), {"to": ["a@loreal.com"]}, c) is None   # reserves the slot
    c.tool_confirmation = SimpleNamespace(confirmed=True)
    assert before(p, tool("create_draft"), {"to": ["a@loreal.com"]}, c) is None   # same call, resumed


def test_failed_write_releases_reservation(p):
    c = ctx()
    assert before(p, tool("create_draft"), {"to": ["a@loreal.com"]}, c) is None
    after(p, tool("create_draft"), {}, c, mcp_ok({"status": "error", "error_code": "insufficient_scope"}))
    assert before(p, tool("create_draft"), {"to": ["b@loreal.com"]}, c) is None


def test_tool_call_budget(p):
    c = ctx()
    for _ in range(6):
        assert before(p, tool("search_mail"), {"query": "x"}, c) is None
    assert before(p, tool("search_mail"), {"query": "x"}, c)["reason"] == "tool_budget_exceeded"


def test_resumed_call_after_approval_not_double_counted(p):
    # The draft attempt counts once (before approval is requested); its re-execution after approval must not.
    c = ctx()
    for _ in range(5):
        before(p, tool("search_mail"), {"query": "x"}, c)
    assert before(p, tool("create_draft"), {"to": ["a@loreal.com"]}, c) is None   # 6th call, counted
    c.tool_confirmation = SimpleNamespace(confirmed=True)
    assert before(p, tool("create_draft"), {"to": ["a@loreal.com"]}, c) is None   # resumed, not counted
    assert c.state["temp:gov:m365_agent:calls"] == 6


def test_confidential_taint_recorded(p):
    c = ctx()
    after(p, tool("search_files"), {}, c, mcp_ok({"status": "ok", "files": [
        {"name": "Q4_pricing_grid.xlsx", "sensitivity": "Confidential"}, {"name": "b.docx", "sensitivity": "General"}]}))
    assert c.state["temp:gov:confidential"] == ["Q4_pricing_grid.xlsx"]


def test_shadow_mode_logs_but_never_blocks(tmp_path, monkeypatch):
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("GOV_AUDIT_FILE", str(audit))
    p = GovernancePlugin(enforce=False)
    assert before(p, tool("create_draft"), {"to": ["x@evil.io"]}, ctx()) is None
    rec = json.loads(audit.read_text().splitlines()[-1])
    assert rec["decision"] == "deny" and rec["enforced"] is False and "x@evil.io" not in audit.read_text()


def test_annotation_mismatch_alerts(p, tmp_path, monkeypatch):
    audit = tmp_path / "a.jsonl"
    monkeypatch.setenv("GOV_AUDIT_FILE", str(audit))
    before(p, tool("create_draft", read_only=True), {"to": ["a@loreal.com"]}, ctx())
    assert any(json.loads(l)["reason"] == "annotation_mismatch" for l in audit.read_text().splitlines())
