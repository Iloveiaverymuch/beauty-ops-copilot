"""Cloud Run S2S auth, offline: Google calls are faked; we test OUR decisions (audience, allowlist, caching)."""
import asyncio

import pytest

import workspace_mcp.server as srv
from copilot.workspace_agent import GoogleIdTokenHeader

SA = "copilot-sa@proj.iam.gserviceaccount.com"


@pytest.fixture
def verifier():
    return srv.GoogleIdTokenVerifier(audience="https://workspace-mcp-123.europe-west1.run.app",
                                     caller_scopes={SA: ["Mail.Read", "Mail.ReadWrite"]})


def _fake_verify(claims=None, exc=None):
    def f(token, request, audience):
        f.audience = audience
        if exc:
            raise exc
        return claims
    return f


def test_known_caller_gets_its_scopes(verifier, monkeypatch):
    fake = _fake_verify({"email": SA, "email_verified": True, "exp": 9999999999, "sub": "1"})
    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", fake)
    tok = asyncio.run(verifier.verify_token("t"))
    assert tok.client_id == SA and tok.scopes == ["Mail.Read", "Mail.ReadWrite"]
    assert fake.audience == "https://workspace-mcp-123.europe-west1.run.app"  # audience is OUR url, always checked


def test_unknown_caller_is_rejected_even_with_valid_google_token(verifier, monkeypatch):
    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token",
                        _fake_verify({"email": "someone-else@proj.iam.gserviceaccount.com", "email_verified": True}))
    assert asyncio.run(verifier.verify_token("t")) is None


def test_bad_signature_or_wrong_audience_is_401(verifier, monkeypatch):
    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", _fake_verify(exc=ValueError("wrong aud")))
    assert asyncio.run(verifier.verify_token("t")) is None


def test_unverified_email_rejected(verifier, monkeypatch):
    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", _fake_verify({"email": SA, "email_verified": False}))
    assert asyncio.run(verifier.verify_token("t")) is None


def test_header_provider_caches_token_until_near_expiry(monkeypatch):
    import time
    calls = []
    monkeypatch.setattr("google.oauth2.id_token.fetch_id_token", lambda req, aud: calls.append(aud) or f"tok{len(calls)}")
    exp = {"v": time.time() + 3600}
    monkeypatch.setattr("google.auth.jwt.decode", lambda t, verify=False: {"exp": exp["v"]})
    h = GoogleIdTokenHeader("https://aud")
    assert h(None) == {"Authorization": "Bearer tok1"} and h(None) == {"Authorization": "Bearer tok1"}
    assert calls == ["https://aud"]                          # cached: one fetch for two calls
    h._exp = time.time() + 60                                 # within 5 min of expiry -> refresh
    assert h(None) == {"Authorization": "Bearer tok2"}
