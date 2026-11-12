"""Policy unit tests: no LLM, no server. The plugin only reads tool.name, ctx.agent_name/state/... so fakes suffice."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from copilot.governance import GovernancePlugin


def user_event(eid, text=None):
    part = SimpleNamespace(text=text)
    return SimpleNamespace(id=eid, author="user", content=SimpleNamespace(parts=[part]))


def ctx(agent="workspace_agent", state=None, confirmed=None, events=None, invocation_id="inv-1"):
    events = events if events is not None else [user_event("turn-1", "please draft")]
    return SimpleNamespace(agent_name=agent, state=state if state is not None else {}, invocation_id=invocation_id,
                           tool_confirmation=confirmed, session=SimpleNamespace(events=events))


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


def test_catalog_agent_cannot_touch_workspace(p):
    assert before(p, tool("search_mail"), {"query": "x"}, ctx(agent="catalog_lookup"))["reason"] == "tool_not_allowed"


def test_external_recipient_blocked_even_with_internal_cc(p):
    r = before(p, tool("create_draft"), {"to": ["sophie.martin@loreal.com", "audit@packaging-partner.io"],
                                         "subject": "s", "body": "b"}, ctx())
    assert r["status"] == "blocked" and r["reason"] == "external_recipient"


def test_lookalike_domain_is_external(p):
    assert before(p, tool("create_draft"), {"to": ["ceo@loreal.com.evil.io"]}, ctx())["reason"] == "external_recipient"


def test_internal_draft_allowed(p):
    assert before(p, tool("create_draft"), {"to": ["sophie.martin@loreal.com"]}, ctx()) is None
    assert before(p, tool("create_draft"), {"to": ["sophie.martin@retail-group.com"]}, ctx()) is None
    assert before(p, tool("create_draft"), {"to": ["operations@beauty-corp.com"]}, ctx()) is None


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
    from copilot.governance import POLICY
    c = ctx()
    for _ in range(POLICY["workspace_agent"].max_calls):
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
    assert c.state["gov:turn-1:workspace_agent:calls"] == 6


def test_confidential_taint_recorded(p):
    c = ctx()
    after(p, tool("search_files"), {}, c, mcp_ok({"status": "ok", "files": [
        {"name": "Q4_pricing_grid.xlsx", "sensitivity": "Confidential"}, {"name": "b.docx", "sensitivity": "General"}]}))
    assert c.state["gov:turn-1:confidential"] == ["Q4_pricing_grid.xlsx"]


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


def test_budget_survives_approval_resume_as_new_invocation(p):
    # Regression for injection_trials naive+enforce p2#0: draft 1 approved -> resume = NEW invocation; a 2nd
    # draft in the same user turn must still be denied. Approval events are function_response-only (no text).
    state = {}
    events = [user_event("turn-1", "prepare any drafts needed")]
    c1 = ctx(state=state, events=events, invocation_id="inv-1")
    assert before(p, tool("create_draft"), {"to": ["a@loreal.com"]}, c1) is None
    events.append(user_event("approval-evt", None))                       # approval response, no text
    c2 = ctx(state=state, events=events, invocation_id="inv-2", confirmed=SimpleNamespace(confirmed=True))
    assert before(p, tool("create_draft"), {"to": ["a@loreal.com"]}, c2) is None   # the approved call itself
    c3 = ctx(state=state, events=events, invocation_id="inv-2")
    assert before(p, tool("create_draft"), {"to": ["b@loreal.com"]}, c3)["reason"] == "write_budget_exceeded"


def test_new_user_turn_resets_budget(p):
    state = {}
    events = [user_event("turn-1", "draft to a")]
    assert before(p, tool("create_draft"), {"to": ["a@loreal.com"]}, ctx(state=state, events=events)) is None
    events.append(user_event("turn-2", "now draft to b"))
    assert before(p, tool("create_draft"), {"to": ["b@loreal.com"]}, ctx(state=state, events=events)) is None


def test_denial_is_recorded_on_the_current_span(p):
    # Observability: the governance decision must be visible in the trace next to the tool call (Cloud Trace / NR).
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exp = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exp))
    with tp.get_tracer("t").start_as_current_span("execute_tool create_draft"):
        before(p, tool("create_draft"), {"to": ["x@evil.io"], "body": "secret"}, ctx())
    span = exp.get_finished_spans()[0]
    ev = [e for e in span.events if e.name == "governance.decision"][0]
    assert ev.attributes["decision"] == "deny" and ev.attributes["reason"] == "external_recipient"
    assert span.attributes["governance.denied"] is True
    assert "x@evil.io" not in str(ev.attributes) and "secret" not in str(ev.attributes)  # allowlist holds in traces


def test_workspace_agent_governance_budget_and_recipient_enforcement(p):
    # Verify policy enforcement applies identically to workspace_agent
    c_ws = ctx(agent="workspace_agent")
    # 1. Deny external recipient on draft
    res = before(p, tool("create_draft"), {"to": ["hacker@external.io"], "subject": "s", "body": "b"}, c_ws)
    assert res["status"] == "blocked" and res["reason"] == "external_recipient"

    # 2. Allow internal recipient on draft
    res_ok = before(p, tool("create_draft"), {"to": ["colleague@retail-group.com"], "subject": "s", "body": "b"}, c_ws)
    assert res_ok is None

    # 3. Allow Google Workspace tools in allowlist
    assert before(p, tool("search_mail"), {"query": "garnier"}, c_ws) is None
    assert before(p, tool("list_events"), {"start_iso": "x", "end_iso": "y"}, c_ws) is None
    assert before(p, tool("search_files"), {"query": "pricing"}, c_ws) is None

    # 4. Deny unknown tool
    assert before(p, tool("unknown_hack_tool"), {}, c_ws)["reason"] == "tool_not_allowed"

