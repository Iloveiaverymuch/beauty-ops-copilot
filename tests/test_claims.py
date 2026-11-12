"""Claims workflow end to end, offline: pure ADK claims engine + SQLite persistence + in-process bus +
the mock retail API through a test client. No model calls (rule triage)."""
import importlib

import pytest
from fastapi.testclient import TestClient

from claims import policy


@pytest.fixture()
def wf(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKEND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAIMS_TRIAGE", "rule")
    monkeypatch.setenv("CLAIMS_BUS", "local")
    import advisor.claims
    import claims.bus
    import claims.service
    import mock_api.stock_api as api
    importlib.reload(claims.bus)
    importlib.reload(advisor.claims)
    importlib.reload(claims.service)
    api.REFUNDS.clear()
    api.FAULT["mode"] = "none"
    monkeypatch.setattr(advisor.claims, "_http", TestClient(api.app))
    monkeypatch.setattr(advisor.claims.time, "sleep", lambda s: None)
    yield claims.service, api, claims.bus.bus()
    api.FAULT["mode"] = "none"


def test_policy_rules():
    assert policy.evaluate(True, False, False, 2, 10).rule == "opened_product_not_returnable"
    assert policy.evaluate(True, True, False, 5, 10).route == "needs_info"
    assert policy.evaluate(True, True, True, 20, 10).route == "rejected"
    assert policy.evaluate(True, True, True, 5, 10).route == "review"          # reactions always reviewed
    assert policy.evaluate(False, False, False, 31, 10).route == "rejected"
    assert policy.evaluate(False, False, False, 5, 39.8).route == "review"     # above auto-approve limit
    assert policy.evaluate(False, False, False, 5, 9.9).route == "auto"


def test_small_unopened_claim_is_refunded_automatically(wf):
    svc, api, bus = wf
    st = svc.open_claim("CMD-1001", "Je n'en veux plus", opened=False, skin_reaction=False)
    assert st["status"] == "refunded" and st["refund_id"] and len(api.REFUNDS) == 1
    assert [m[1]["type"] for m in bus.messages] == ["claim.closed"]


def test_opened_without_reaction_is_rejected_by_rule_not_by_llm(wf):
    svc, api, _ = wf
    st = svc.open_claim("CMD-1001", "ouvert, je n'aime pas", opened=True, skin_reaction=False)
    assert (st["status"], st["rule"]) == ("rejected", "opened_product_not_returnable") and not api.REFUNDS


def test_reaction_goes_to_human_then_resumes_from_event_exactly_once(wf):
    svc, api, bus = wf
    st = svc.open_claim("CMD-1002", "plaques rouges", opened=True, skin_reaction=True, photo_provided=True)
    assert st["status"] == "pending_review" and st["next"] == ["human_review"]
    cid = st["claim_id"]
    assert [s["claim_id"] for s in svc.pending()] == [cid]
    svc.submit_decision(cid, "approve", "alice")
    decision = [m[1] for m in bus.messages if m[0] == "claims-decisions"][0]
    outcome, st = svc.handle_decision(decision)
    assert (outcome, st["status"]) == ("resumed", "refunded") and st["amount_eur"] == 39.8
    outcome, st = svc.handle_decision(decision)                         # Pub/Sub redelivers the same message
    assert outcome == "duplicate" and len(api.REFUNDS) == 1             # paid exactly once
    steps = [h["step"] for h in st["history"]]
    assert steps[-4:] == ["triage", "human_review", "refund", "close"]


def test_refund_api_outage_is_resumable_and_idempotent(wf):
    svc, api, _ = wf
    cid = svc.open_claim("CMD-1002", "rougeurs", True, True, True)["claim_id"]
    api.FAULT["mode"] = "down"
    outcome, st = svc.handle_decision({"claim_id": cid, "decision": "approve", "reviewer": "bob"})
    assert (outcome, st["status"], st["next"]) == ("resumed", "refund_failed", ["refund"])
    api.FAULT["mode"] = "none"
    outcome, st = svc.handle_decision({"claim_id": cid, "decision": "approve", "reviewer": "bob"})   # redelivery
    assert (outcome, st["status"]) == ("retried", "refunded") and len(api.REFUNDS) == 1


def test_reviewer_rejection_and_missing_photo(wf):
    svc, api, _ = wf
    cid = svc.open_claim("CMD-1004", "brûlures", True, True, True)["claim_id"]
    _, st = svc.handle_decision({"claim_id": cid, "decision": "reject", "reviewer": "carol"})
    assert st["status"] == "rejected_by_reviewer" and not api.REFUNDS
    assert svc.open_claim("CMD-1004", "rougeurs", True, True, photo_provided=False)["status"] == "needs_info"
    assert svc.open_claim("CMD-9999", "?", False, False)["status"] == "rejected"


def test_unknown_claim_decision_is_not_a_duplicate(wf):
    svc, api, _ = wf
    outcome, st = svc.handle_decision({"claim_id": "CLM-XXXXXX", "decision": "approve", "reviewer": "x"})
    assert (outcome, st["status"]) == ("unknown_claim", "unknown_claim") and not api.REFUNDS


def test_instruction_routes_reactions_to_the_claim_tool_not_the_handoff():
    import beauty_advisor.agent as ag
    rule9 = ag.INSTRUCTION.split("9. Human advisor")[1].split("Safety and limits")[0]
    assert "NOT for returns, refunds or a skin reaction" in rule9


def test_operations_copilot_claims_review(wf):
    svc, api, bus = wf
    from copilot.tools.operations import list_pending_claims, review_claim

    # 1. Customer opens a claim requiring review (skin reaction)
    st = svc.open_claim("CMD-1002", "reaction allergique", opened=True, skin_reaction=True, photo_provided=True)
    cid = st["claim_id"]
    assert st["status"] == "pending_review"

    # 2. Operations team lists pending claims
    pending_list = list_pending_claims()
    assert pending_list["status"] == "ok"
    assert any(c["claim_id"] == cid for c in pending_list["claims"])

    # 3. Operations team approves claim through Copilot
    res = review_claim(cid, "approve", reason="Valid dermatological report")
    assert res["status"] == "refunded" and res["outcome"] == "resumed"
    assert len(api.REFUNDS) == 1

    # 4. Customer checks status
    assert svc.status(cid)["status"] == "refunded"


def test_guardrail_before_tool_callback():
    from beauty_advisor.agent import guardrail_before_tool
    from unittest.mock import MagicMock

    tool = MagicMock()
    tool.name = "open_return_claim"

    # Blocked by format check
    res = guardrail_before_tool(tool, {"order_id": "INVALID", "product_opened": False, "skin_reaction": False}, None)
    assert res == {"status": "rejected", "message": "Format de commande invalide. Le numéro de commande doit commencer par 'CMD-' (ex: CMD-1002)."}

    # Blocked by hygiene policy
    res2 = guardrail_before_tool(tool, {"order_id": "CMD-1001", "product_opened": True, "skin_reaction": False}, None)
    assert res2["status"] == "rejected" and res2["rule"] == "opened_product_not_returnable"

    # Allowed when valid
    res3 = guardrail_before_tool(tool, {"order_id": "CMD-1001", "product_opened": False, "skin_reaction": False}, None)
    assert res3 is None

