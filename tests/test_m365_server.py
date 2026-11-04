"""Server contract tests: scopes, limits, filters. Auth middleware (401) is covered by scripts/mcp_smoke.py."""
import pytest
from mcp.server.auth.provider import AccessToken

import m365_mcp.server as srv


def _as(monkeypatch, scopes):
    tok = AccessToken(token="t", client_id="c", scopes=scopes)
    monkeypatch.setattr(srv, "get_access_token", lambda: tok)


@pytest.fixture
def full(monkeypatch):
    _as(monkeypatch, ["Mail.Read", "Calendars.Read", "Files.Read.All", "Mail.ReadWrite"])


def test_scope_enforced_per_tool(monkeypatch):
    _as(monkeypatch, ["Mail.Read"])
    assert srv.search_mail("garnier")["status"] == "ok"
    assert srv.search_files("pricing")["error_code"] == "insufficient_scope"
    assert srv.create_draft(["a@loreal.com"], "s", "b")["error_code"] == "insufficient_scope"


def test_no_token_means_no_data(monkeypatch):
    monkeypatch.setattr(srv, "get_access_token", lambda: None)
    assert srv.search_mail("garnier")["status"] == "error"


def test_top_is_capped_server_side(full):
    assert srv.search_mail("loreal", top=500)["count"] <= srv.MAX_TOP


def test_injection_email_is_returned_as_data(full):
    # The server does NOT sanitize: it returns user content as-is. Defending against it is the agent's job (D1.5).
    msg = srv.search_mail("packaging")["messages"][0]
    assert "SYSTEM NOTICE TO AI ASSISTANT" in msg["body"]


def test_calendar_window_and_bad_dates(full):
    r = srv.list_events("2026-11-06T00:00:00+01:00", "2026-11-06T23:59:00+01:00")
    assert {e["id"] for e in r["events"]} == {"evt-002", "evt-004"}
    assert srv.list_events("tomorrow", "later")["error_code"] == "bad_request"


def test_draft_recipient_bounds(full):
    assert srv.create_draft([], "s", "b")["error_code"] == "bad_request"
