"""Server contract tests for Google Workspace MCP server: scopes, limits, filters."""
import pytest
from mcp.server.auth.provider import AccessToken

import workspace_mcp.server as srv


def _as(monkeypatch, scopes):
    tok = AccessToken(token="t", client_id="c", scopes=scopes)
    monkeypatch.setattr(srv, "get_access_token", lambda: tok)


@pytest.fixture
def google_full(monkeypatch):
    _as(monkeypatch, [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.events.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
    ])


@pytest.fixture
def legacy_full(monkeypatch):
    _as(monkeypatch, ["Mail.Read", "Calendars.Read", "Files.Read.All", "Mail.ReadWrite"])


def test_google_scopes_enforced_per_tool(monkeypatch):
    _as(monkeypatch, ["https://www.googleapis.com/auth/gmail.readonly"])
    assert srv.search_mail("garnier")["status"] == "ok"
    assert srv.search_files("pricing")["error_code"] == "insufficient_scope"
    assert srv.create_draft(["colleague@retail-group.com"], "s", "b")["error_code"] == "insufficient_scope"


def test_legacy_scopes_backwards_compatibility(monkeypatch):
    _as(monkeypatch, ["Mail.Read"])
    assert srv.search_mail("garnier")["status"] == "ok"
    assert srv.search_files("pricing")["error_code"] == "insufficient_scope"


def test_no_token_means_no_data(monkeypatch):
    monkeypatch.setattr(srv, "get_access_token", lambda: None)
    assert srv.search_mail("garnier")["status"] == "error"
    assert srv.search_mail("garnier")["error_code"] == "insufficient_scope"


def test_top_is_capped_server_side(google_full):
    assert srv.search_mail("retail", top=500)["count"] <= srv.MAX_TOP
    assert srv.search_files("brief", top=100)["count"] <= srv.MAX_TOP


def test_prompt_injection_returned_as_untrusted_data(google_full):
    msg = srv.search_mail("packaging")["messages"][0]
    assert "SYSTEM NOTICE TO AI ASSISTANT" in msg["body"]


def test_calendar_window_and_bad_dates(google_full):
    r = srv.list_events("2026-11-06T00:00:00+01:00", "2026-11-06T23:59:00+01:00")
    assert {e["id"] for e in r["events"]} == {"evt-002", "evt-004"}
    assert srv.list_events("invalid-start", "invalid-end")["error_code"] == "bad_request"


def test_draft_recipient_bounds(google_full):
    assert srv.create_draft([], "s", "b")["error_code"] == "bad_request"
    too_many = [f"user{i}@retail-group.com" for i in range(11)]
    assert srv.create_draft(too_many, "s", "b")["error_code"] == "bad_request"


def test_draft_creation_success(google_full):
    res = srv.create_draft(["sophie.martin@retail-group.com"], "Hello", "Meeting follow-up")
    assert res["status"] == "ok"
    assert res["draft_id"].startswith("r-")
    assert "mail.google.com" in res["web_link"]

