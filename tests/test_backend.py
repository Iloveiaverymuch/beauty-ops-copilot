"""Offline tests for the chatbot backend pieces that don't need a model: UI event mapping, chips, request
validation, the retail API client's timeout/retry/fallback, and the mock retail API itself."""
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import advisor.tools as tools
from backend.ui_events import suggestions, ui_event_for_tool_result
from mock_api.stock_api import FAULT
from mock_api.stock_api import app as retail_app


def test_cards_come_from_tool_results_not_from_text():
    ev = ui_event_for_tool_result("search_products", {"status": "ok", "products": [{"sku": "LUM-SER-001"}]})
    assert ev == {"event": "cards", "data": {"products": [{"sku": "LUM-SER-001"}]}}
    assert ui_event_for_tool_result("search_products", {"status": "ok", "products": []}) is None


def test_suggestions_follow_what_just_happened():
    assert suggestions([("search_knowledge", {"status": "no_answer"})], False) == ["Parler à un conseiller"]
    assert "Réessayer" in suggestions([("check_availability", {"status": "unavailable"})], True)
    assert suggestions([], True, "Quel est votre type de peau ?") == ["Peau grasse", "Peau sèche", "Peau sensible"]
    assert suggestions([], True, "Avez-vous une photo de la réaction ?") == []   # regression: chips on a refund request
    assert suggestions([], False) == []          # regression: skin chips appeared mid-conversation


def test_chat_request_validation():
    from pydantic import BaseModel, Field  # mirror of backend.app.ChatRequest without importing the app (loads the agent)
    class ChatRequest(BaseModel):
        message: str = Field(min_length=1, max_length=2000)
        thread_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    with pytest.raises(ValidationError):
        ChatRequest(message="")
    with pytest.raises(ValidationError):
        ChatRequest(message="x" * 2001)
    with pytest.raises(ValidationError):
        ChatRequest(message="ok", thread_id="../../etc/passwd")


def test_retail_client_retries_then_falls_back(monkeypatch):
    calls = []

    def boom(url, params):
        calls.append(url)
        raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(tools._http, "get", boom)
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    r = tools.check_availability("LUM-SER-001", "nanterre")
    assert r["status"] == "unavailable" and r["reason"] == "timeout"
    assert len(calls) == tools.API_RETRIES + 1          # bounded retries, then a sayable fallback


def test_retail_client_does_not_retry_4xx(monkeypatch):
    calls = []

    def not_found(url, params):
        calls.append(url)
        return httpx.Response(404, json={"detail": "unknown sku"})
    monkeypatch.setattr(tools._http, "get", not_found)
    assert tools.check_availability("XXX-YYY-000", "nanterre")["status"] == "unavailable"
    assert len(calls) == 1


def test_mock_api_is_deterministic_and_fault_switch_works():
    c = TestClient(retail_app)
    a = c.get("/stock/LUM-SER-001", params={"store": "lyon-part-dieu"}).json()
    assert a == c.get("/stock/LUM-SER-001", params={"store": "lyon-part-dieu"}).json()
    assert c.get("/delivery", params={"sku": "LUM-SER-001", "postcode": "20000"}).json()["deliverable"] is False
    c.post("/admin/fault", params={"mode": "down"})
    try:
        assert c.get("/stores").status_code == 503
    finally:
        FAULT["mode"] = "none"


def test_chat_stream_contract_with_a_fake_engine(monkeypatch, tmp_path):
    """End-to-end SSE contract without a model: meta first, text chunks, cards from the tool result, chips, done."""
    monkeypatch.setenv("SESSIONS", "memory")
    monkeypatch.setenv("BACKEND_DATA_DIR", str(tmp_path))
    import backend.app as api

    class Fake:
        name = "fake"

        async def stream(self, thread_id, message):
            yield {"type": "tool_call", "name": "search_products", "args": {}}
            yield {"type": "tool_result", "name": "search_products", "response": {"status": "ok", "products": [{"sku": "S1"}]}}
            yield {"type": "text", "delta": "Voici "}
            yield {"type": "text", "delta": "trois sérums."}
            yield {"type": "usage", "tokens_in": 100, "tokens_out": 20}

    monkeypatch.setitem(api.ENGINES, "adk", Fake())
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    body = TestClient(api.app).post("/chat", json={"message": "sérum"}).text
    events = [blk.split("\n")[0].removeprefix("event: ") for blk in body.strip().split("\n\n")]
    assert events == ["meta", "progress", "cards", "text", "text", "suggestions", "done"]
    assert '"tokens_in": 100' in body and (tmp_path / "kpi.jsonl").exists()
