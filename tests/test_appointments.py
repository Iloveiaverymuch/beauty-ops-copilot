"""Offline regression tests for W2 (SPEC-011): in-store advisor appointment booking,
two-phase hold/confirm idempotency, ADK native HITL confirmation, strict retry policy,
state-only identity injection, and appointment.confirmed domain event emission."""
from __future__ import annotations

import asyncio
import importlib
import inspect
from types import SimpleNamespace
from typing import AsyncGenerator

import httpx
import pytest
from fastapi.testclient import TestClient
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.adk.tools import FunctionTool
from google.genai import types

import advisor.appointments as appts
import mock_api.stock_api as stock_api


@pytest.fixture()
def appt_env(monkeypatch):
    monkeypatch.setenv("CLAIMS_BUS", "local")
    import claims.bus

    importlib.reload(claims.bus)
    stock_api.APPOINTMENT_HOLDS.clear()
    stock_api.APPOINTMENTS.clear()
    stock_api.FAULT["mode"] = "none"
    monkeypatch.setattr(appts, "_http", TestClient(stock_api.app))
    monkeypatch.setattr(appts.time, "sleep", lambda s: None)
    yield claims.bus.bus()
    stock_api.FAULT["mode"] = "none"


def _ctx(user_id: str = "cust-alice", store: str = "lille-centre", contact: str = "alice@example.com", sess_id: str = "sess-42"):
    return SimpleNamespace(
        session=SimpleNamespace(id=sess_id),
        invocation_id="inv-1",
        state={"user:id": user_id, "user:store": store, "user:contact": contact},
    )


def test_list_slots_and_double_call_produces_one_booking_and_one_event(appt_env):
    bus = appt_env
    ctx = _ctx()

    slots = appts.list_appointment_slots("2026-11-25", tool_context=ctx)
    assert slots["status"] == "ok"
    assert slots["store"] == "lille-centre"
    assert len(slots["slots"]) == 5

    slot_id = slots["slots"][0]["slot_id"]
    first = appts.book_advisor_appointment(slot_id, topic="routine peau sensible", tool_context=ctx)
    second = appts.book_advisor_appointment(slot_id, topic="routine peau sensible", tool_context=ctx)

    assert first["status"] == "confirmed" and first["duplicate"] is False
    assert second["status"] == "confirmed" and second["duplicate"] is True
    assert first["appointment_id"] == second["appointment_id"]
    assert len(stock_api.APPOINTMENTS) == 1
    assert "alice@example.com" not in str(first)  # zero PII echo
    assert ctx.state["temp:last_appointment_id"] == first["appointment_id"]

    # Exactly ONE appointment.confirmed domain event emitted
    events = [m[1] for m in bus.messages if m[0] == appts.APPOINTMENTS_TOPIC]
    assert len(events) == 1
    assert events[0]["type"] == "appointment.confirmed"
    assert events[0]["appointment_id"] == first["appointment_id"]
    assert events[0]["store"] == "lille-centre"


def test_hitl_confirmation_refused_writes_nothing_and_approved_writes_once(appt_env):
    bus = appt_env

    class _BookingFakeLlm(BaseLlm):
        model: str = "fake-booking"

        async def generate_content_async(self, llm_request, stream=False) -> AsyncGenerator[LlmResponse, None]:
            if any(p.function_response for p in (llm_request.contents[-1].parts or [])):
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="done")]))
            else:
                yield LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(
                                    name="book_advisor_appointment",
                                    args={"slot_id": "2026-11-26T14:00", "topic": "diagnostic"},
                                )
                            )
                        ],
                    )
                )

    agent = Agent(
        name="booking_agent",
        model=_BookingFakeLlm(),
        instruction="book",
        tools=[FunctionTool(func=appts.book_advisor_appointment, require_confirmation=True)],
    )
    runner = InMemoryRunner(app=App(name="booking_test", root_agent=agent))

    async def run_hitl(session_id: str, confirm: bool) -> str | None:
        await runner.session_service.create_session(
            app_name="booking_test",
            user_id="cust-alice",
            session_id=session_id,
            state={"user:id": "cust-alice", "user:store": "lille-centre", "user:contact": "alice@example.com"},
        )
        confirm_call_id = None
        async for ev in runner.run_async(
            user_id="cust-alice",
            session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part(text="réserve 14h")]),
        ):
            for fc in ev.get_function_calls() or []:
                if fc.name == "adk_request_confirmation":
                    confirm_call_id = fc.id
        assert confirm_call_id is not None

        # Resume with user decision
        resume_msg = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=confirm_call_id,
                        name="adk_request_confirmation",
                        response={"confirmed": confirm},
                    )
                )
            ],
        )
        async for _ in runner.run_async(user_id="cust-alice", session_id=session_id, new_message=resume_msg):
            pass
        return confirm_call_id

    # 1. Refused confirmation -> NOTHING written, ZERO events emitted
    asyncio.run(run_hitl("sess-refuse", confirm=False))
    assert len(stock_api.APPOINTMENTS) == 0
    assert len(stock_api.APPOINTMENT_HOLDS) == 0
    assert [m for m in bus.messages if m[0] == appts.APPOINTMENTS_TOPIC] == []

    # 2. Approved confirmation -> 1 booking written, 1 event emitted
    asyncio.run(run_hitl("sess-approve", confirm=True))
    assert len(stock_api.APPOINTMENTS) == 1
    events = [m[1] for m in bus.messages if m[0] == appts.APPOINTMENTS_TOPIC]
    assert len(events) == 1 and events[0]["type"] == "appointment.confirmed"


@pytest.mark.parametrize("status_code", [401, 403])
def test_retry_policy_never_retries_401_or_403(monkeypatch, status_code):
    attempts = []

    def fake_request(method, url, **kwargs):
        attempts.append((method, url))
        return httpx.Response(status_code, json={"detail": "denied"})

    monkeypatch.setattr(appts._http, "request", fake_request)
    monkeypatch.setattr(appts.time, "sleep", lambda s: None)

    res = appts.book_advisor_appointment("2026-11-25T10:00", tool_context=_ctx())
    assert res["status"] == "error"
    assert res["reason"] == f"http_{status_code}"
    assert len(attempts) == 1  # NEVER retried


@pytest.mark.parametrize("failure_kind", ["429", "503", "timeout"])
def test_retry_policy_retries_429_503_and_timeout_and_recovers(monkeypatch, failure_kind):
    attempts = []

    def flaky_request(method, url, **kwargs):
        attempts.append(url)
        if len(attempts) == 1:
            if failure_kind == "timeout":
                raise httpx.ReadTimeout("slow")
            return httpx.Response(int(failure_kind), json={"detail": "transient"})
        return httpx.Response(200, json={"store": "lille-centre", "date": "2026-11-25", "slots": []})

    monkeypatch.setattr(appts._http, "request", flaky_request)
    monkeypatch.setattr(appts.time, "sleep", lambda s: None)

    res = appts.list_appointment_slots("2026-11-25", tool_context=_ctx())
    assert res["status"] == "ok"
    assert len(attempts) == 2  # Retried once and recovered


def test_identity_store_and_contact_come_strictly_from_state():
    params = set(inspect.signature(appts.book_advisor_appointment).parameters)
    assert not (params & {"customer_id", "user_id", "store", "contact"})

    missing_ctx = SimpleNamespace(session=SimpleNamespace(id="s1"), state={"user:id": "cust-alice"})
    res = appts.book_advisor_appointment("2026-11-25T10:00", tool_context=missing_ctx)
    assert res["status"] == "error" and res["error_code"] == "missing_user_context"
    assert "user:store" in res["message"] and "user:contact" in res["message"]


def test_slot_conflict_returns_conflict_without_retry(appt_env):
    # Alice books the slot first
    r1 = appts.book_advisor_appointment("2026-11-25T11:00", tool_context=_ctx(user_id="cust-alice", sess_id="s-a"))
    assert r1["status"] == "confirmed"

    # Bob tries the same slot -> conflict, zero double booking
    r2 = appts.book_advisor_appointment("2026-11-25T11:00", tool_context=_ctx(user_id="cust-bob", sess_id="s-b"))
    assert r2["status"] == "conflict"
    assert len(stock_api.APPOINTMENTS) == 1


def test_beauty_advisor_registers_booking_tool_with_require_confirmation():
    from beauty_advisor.agent import root_agent

    booking_tools = [
        t for t in root_agent.tools
        if isinstance(t, FunctionTool) and getattr(t.func, "__name__", "") == "book_advisor_appointment"
    ]
    assert len(booking_tools) == 1
    assert booking_tools[0]._require_confirmation is True
