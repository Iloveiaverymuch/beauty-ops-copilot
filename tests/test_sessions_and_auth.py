"""Offline tests for W1 (SPEC-010): DatabaseSessionService multi-instance continuity,
signed-token authentication, reviewer RBAC, and cross-customer ownership / anti-IDOR."""
from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.adk.apps import App
from google.adk.agents import LlmAgent
from google.adk.events import Event, EventActions
from google.genai import types

from backend.auth import issue_token, session_owners
from backend.engines import AdkEngine, build_session_service


def _dummy_app() -> App:
    agent = LlmAgent(name="dummy_agent", model="gemini-2.0-flash", instruction="test")
    return App(name="multi_instance_app", root_agent=agent)


def test_multi_instance_database_session_continuity(tmp_path):
    """Two independent AdkEngine / Runner instances sharing one DatabaseSessionService
    must seamlessly continue the same conversation and preserve user:id state."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'shared_sessions.db'}"
    app = _dummy_app()

    instance_a = AdkEngine(app=app, name="instance_a", db_url=db_url)
    instance_b = AdkEngine(app=app, name="instance_b", db_url=db_url)

    async def scenario():
        # Instance A creates session & appends turn 1 + state delta
        await instance_a._ensure_session("thread_multi_01", user_id="cust-alice")
        sess_a = await instance_a.runner.session_service.get_session(
            app_name=app.name, user_id="cust-alice", session_id="thread_multi_01"
        )
        assert sess_a is not None
        assert sess_a.state.get("user:id") == "cust-alice"

        ev1 = Event(
            invocation_id="inv-1",
            author="user",
            content=types.Content(role="user", parts=[types.Part(text="Bonjour, peau sèche")]),
            actions=EventActions(state_delta={"skin_type": "dry"}),
        )
        await instance_a.runner.session_service.append_event(sess_a, ev1)

        # Instance B (separate Runner & DatabaseSessionService) resumes the exact same session
        sess_b = await instance_b.runner.session_service.get_session(
            app_name=app.name, user_id="cust-alice", session_id="thread_multi_01"
        )
        assert sess_b is not None
        assert sess_b.state.get("user:id") == "cust-alice"
        assert sess_b.state.get("skin_type") == "dry"
        assert len(sess_b.events) == 1
        assert sess_b.events[0].content.parts[0].text == "Bonjour, peau sèche"

        # Another customer cannot see Alice's session even on Instance B
        sess_bob = await instance_b.runner.session_service.get_session(
            app_name=app.name, user_id="cust-bob", session_id="thread_multi_01"
        )
        assert sess_bob is None

        await instance_a.runner.session_service.close()
        await instance_b.runner.session_service.close()

    asyncio.run(scenario())


def test_database_session_mode_requires_database_url(monkeypatch):
    monkeypatch.setenv("SESSIONS", "database")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        build_session_service("test_app")


def test_unauthenticated_requests_return_401(monkeypatch):
    monkeypatch.setenv("SESSIONS", "memory")
    import backend.app as api

    client = TestClient(api.app)
    assert client.post("/chat", json={"message": "bonjour"}).status_code == 401
    assert client.post("/chat/sync", json={"message": "bonjour"}).status_code == 401
    assert client.post("/chat/confirm", json={
        "thread_id": "a" * 32, "call_id": "call_1", "confirmed": True
    }).status_code == 401
    assert client.get("/claims/pending").status_code == 401
    assert client.post("/claims/CLM-123456/decision", json={"decision": "approve"}).status_code == 401


def test_tampered_and_expired_tokens_return_401(monkeypatch):
    monkeypatch.setenv("SESSIONS", "memory")
    import backend.app as api

    client = TestClient(api.app)
    valid = issue_token("cust-alice", "customer")
    tampered = valid[:-4] + "AAAA"
    expired = issue_token("cust-alice", "customer", ttl_s=-10)

    assert client.post(
        "/chat/sync", json={"message": "bonjour"}, headers={"Authorization": f"Bearer {tampered}"}
    ).status_code == 401
    assert client.post(
        "/chat/sync", json={"message": "bonjour"}, headers={"Authorization": f"Bearer {expired}"}
    ).status_code == 401


def test_customer_cannot_resume_another_customers_session(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSIONS", "memory")
    import backend.app as api

    session_owners.clear_memory()

    class Fake:
        name = "fake"

        async def stream(self, thread_id, message, user_id="anonymous"):
            yield {"type": "text", "delta": f"ok:{user_id}"}

        async def resume_confirmation(self, thread_id, call_id, confirmed, user_id="anonymous"):
            yield {"type": "text", "delta": f"confirmed:{user_id}"}

    monkeypatch.setitem(api.ENGINES, "adk", Fake())
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    client = TestClient(api.app)

    alice_headers = {"Authorization": f"Bearer {issue_token('cust-alice', 'customer')}"}
    bob_headers = {"Authorization": f"Bearer {issue_token('cust-bob', 'customer')}"}

    r1 = client.post("/chat/sync", json={"message": "bonjour"}, headers=alice_headers)
    assert r1.status_code == 200
    alice_thread = r1.json()["thread_id"]

    # Bob tries to hijack Alice's thread via /chat, /chat/sync, and /chat/confirm
    assert client.post(
        "/chat", json={"message": "resume", "thread_id": alice_thread}, headers=bob_headers
    ).status_code == 403
    assert client.post(
        "/chat/sync", json={"message": "resume", "thread_id": alice_thread}, headers=bob_headers
    ).status_code == 403
    assert client.post(
        "/chat/confirm",
        json={"thread_id": alice_thread, "call_id": "call_1", "confirmed": True, "engine": "adk"},
        headers=bob_headers,
    ).status_code == 403


def test_reviewer_endpoints_enforce_role_and_verified_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSIONS", "memory")
    monkeypatch.setenv("BACKEND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAIMS_BUS", "local")

    import advisor.claims
    import backend.app as api
    import claims.bus
    import claims.service
    import mock_api.stock_api as stock_api

    importlib.reload(claims.bus)
    importlib.reload(advisor.claims)
    importlib.reload(claims.service)
    stock_api.REFUNDS.clear()
    monkeypatch.setattr(advisor.claims, "_http", TestClient(stock_api.app))
    monkeypatch.setattr(advisor.claims.time, "sleep", lambda s: None)
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)

    # Alice opens a pending-review claim
    st = claims.service.open_claim(
        "CMD-1002", "rougeurs", opened=True, skin_reaction=True, photo_provided=True, owner_id="cust-alice"
    )
    claim_id = st["claim_id"]

    client = TestClient(api.app)
    cust_headers = {"Authorization": f"Bearer {issue_token('cust-alice', 'customer')}"}
    rev_headers = {"Authorization": f"Bearer {issue_token('rev-marie', 'reviewer')}"}

    # Customer is rejected with 403
    assert client.get("/claims/pending", headers=cust_headers).status_code == 403
    assert client.post(
        f"/claims/{claim_id}/decision",
        json={"decision": "approve", "reviewer": "spoofed_admin"},
        headers=cust_headers,
    ).status_code == 403

    # Reviewer succeeds, and identity comes from token (not body)
    pending_res = client.get("/claims/pending", headers=rev_headers)
    assert pending_res.status_code == 200
    assert any(c["claim_id"] == claim_id for c in pending_res.json())

    dec_res = client.post(
        f"/claims/{claim_id}/decision",
        json={"decision": "approve", "reviewer": "spoofed_admin"},
        headers=rev_headers,
    )
    assert dec_res.status_code == 200
    assert dec_res.json()["status"] == "refunded"
    assert dec_res.json()["reviewer"] == "rev-marie"


def test_customer_cannot_open_claim_on_another_customers_order_or_read_their_claim(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKEND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLAIMS_BUS", "local")

    import advisor.claims
    import claims.bus
    import claims.service
    import claims.tools as ctools
    import mock_api.stock_api as stock_api

    importlib.reload(claims.bus)
    importlib.reload(advisor.claims)
    importlib.reload(claims.service)
    stock_api.REFUNDS.clear()
    monkeypatch.setattr(advisor.claims, "_http", TestClient(stock_api.app))
    monkeypatch.setattr(advisor.claims.time, "sleep", lambda s: None)

    alice_ctx = SimpleNamespace(state={"user:id": "cust-alice"})
    bob_ctx = SimpleNamespace(state={"user:id": "cust-bob"})

    # Bob tries to open a claim on Alice's order CMD-1001 -> forbidden, zero refunds
    bob_attempt = ctools.open_return_claim(
        "CMD-1001", "je veux un remboursement", product_opened=False, skin_reaction=False, tool_context=bob_ctx
    )
    assert bob_attempt["status"] == "forbidden"
    assert len(stock_api.REFUNDS) == 0

    # Alice opens a valid claim on her own order CMD-1001 -> refunded
    alice_claim = ctools.open_return_claim(
        "CMD-1001", "non ouvert", product_opened=False, skin_reaction=False, tool_context=alice_ctx
    )
    assert alice_claim["status"] == "refunded"
    cid = alice_claim["claim_id"]

    # Bob tries to read Alice's claim status -> forbidden, no amount or refund_id leaked
    bob_read = ctools.get_claim_status(cid, tool_context=bob_ctx)
    assert bob_read["status"] == "forbidden"
    assert bob_read.get("refund_id") is None
    assert bob_read.get("amount_eur") is None

    # Alice reads her own claim -> ok
    alice_read = ctools.get_claim_status(cid, tool_context=alice_ctx)
    assert alice_read["status"] == "refunded"
    assert alice_read["refund_id"] is not None
