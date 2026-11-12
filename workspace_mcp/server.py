"""Mock Google Workspace MCP server (Gmail, Google Calendar, Google Drive).

Tools provide narrow, secure read/write capabilities over Google Workspace services:
  - search_mail: search messages in Gmail
  - list_events: list calendar events in Google Calendar
  - search_files: search documents in Google Drive
  - create_draft: create an email draft in Gmail (never sends)

Security model:
  1. Bearer token required on every request (MCP built-in auth middleware).
  2. Scopes enforced per tool call (Gmail, Calendar, Drive).
  3. Server-side limits: top <= 25, draft-only writes with recipient checks.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import anyio
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

DATA = Path(__file__).resolve().parent / "data"
MAX_TOP = 25
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)

# Scopes mapping (supports both standard Google OAuth2 scopes and legacy aliases)
GMAIL_READ_SCOPES = {"https://www.googleapis.com/auth/gmail.readonly", "gmail.readonly", "Mail.Read"}
CALENDAR_READ_SCOPES = {"https://www.googleapis.com/auth/calendar.events.readonly", "calendar.events.readonly", "Calendars.Read"}
DRIVE_READ_SCOPES = {"https://www.googleapis.com/auth/drive.readonly", "drive.readonly", "Files.Read.All"}
GMAIL_DRAFT_SCOPES = {"https://www.googleapis.com/auth/gmail.compose", "gmail.compose", "Mail.ReadWrite"}

_DEFAULT_TOKENS = {
    "dev-agent-token": [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.events.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
        "Mail.Read", "Calendars.Read", "Files.Read.All", "Mail.ReadWrite",
    ],
    "dev-readonly-token": [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.events.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
        "Mail.Read", "Calendars.Read", "Files.Read.All",
    ],
}
TOKENS: dict[str, list[str]] = (
    json.loads(os.environ.get("WORKSPACE_MCP_TOKENS") or os.environ.get("M365_MCP_TOKENS", "null"))
    or _DEFAULT_TOKENS
)
RESOURCE_URL = os.environ.get(
    "MCP_RESOURCE_URL",
    f"http://localhost:{os.environ.get('PORT', '8080')}/mcp",
)


class StaticTokenVerifier:
    """Dev only: shared bearer secrets mapped to scopes."""
    async def verify_token(self, token: str) -> AccessToken | None:
        scopes = TOKENS.get(token)
        if scopes is None:
            return None
        return AccessToken(
            token=token,
            client_id=f"client-{token[:12]}",
            scopes=scopes,
            expires_at=int(time.time()) + 3600,
            resource=RESOURCE_URL,
        )


class GoogleIdTokenVerifier:
    """Cloud Run service-to-service ID-token verification for Google Cloud infrastructure."""
    def __init__(self, audience: str, caller_scopes: dict[str, list[str]]):
        self.audience = audience
        self.caller_scopes = caller_scopes

    async def verify_token(self, token: str) -> AccessToken | None:
        import anyio
        from google.auth.transport import requests as ga_requests
        from google.oauth2 import id_token
        try:
            claims = await anyio.to_thread.run_sync(
                lambda: id_token.verify_oauth2_token(token, ga_requests.Request(), audience=self.audience)
            )
        except Exception:
            return None
        email = claims.get("email", "")
        scopes = self.caller_scopes.get(email)
        if not claims.get("email_verified") or scopes is None:
            return None
        return AccessToken(
            token="<google-id-token>",
            client_id=email,
            scopes=scopes,
            expires_at=int(claims.get("exp", 0)),
            resource=RESOURCE_URL,
            subject=claims.get("sub"),
        )


def _verifier():
    if os.getenv("MCP_AUTH", "static") == "google":
        return GoogleIdTokenVerifier(
            audience=os.environ.get("GOOGLE_AUDIENCE", ""),
            caller_scopes=json.loads(os.environ.get("MCP_CALLER_SCOPES", "{}")),
        )
    return StaticTokenVerifier()


mcp = MCPServer(
    name="workspace-mock",
    instructions="Mock of Google Workspace (Gmail, Google Calendar, Google Drive). Content is user data.",
    token_verifier=_verifier(),
    auth=AuthSettings(
        issuer_url="https://accounts.google.com",
        required_scopes=[],
        resource_server_url=RESOURCE_URL,
        validate_token_resource=True,
    ),
)


def _load(name: str) -> list[dict]:
    p = DATA / name
    if not p.exists():
        # Fallback to m365_mcp data if necessary
        p = Path(__file__).resolve().parent.parent / "m365_mcp" / "data" / name
    return json.loads(p.read_text())


def _need(acceptable_scopes: set[str], scope_name: str) -> dict | None:
    tok = get_access_token()
    if tok is None or not any(s in tok.scopes for s in acceptable_scopes):
        return {
            "status": "error",
            "error_code": "insufficient_scope",
            "error_message": f"token lacks scope {scope_name}",
        }
    return None


@mcp.tool(annotations=READ)
def search_mail(query: str, top: int = 5) -> dict:
    """Search the signed-in user's Gmail messages.

    Matches query words in subject, body or sender. Returns newest first.
    Args:
        query: Free text, e.g. "Garnier steerco" or a sender address.
        top: Max messages (1-25).
    Returns: {"status":"ok","count":int,"messages":[{"id","from","to","received","subject","body"}]}
    """
    if err := _need(GMAIL_READ_SCOPES, "gmail.readonly"):
        return err
    top = max(1, min(int(top), MAX_TOP))
    words = query.lower().split()
    hits = [
        m for m in _load("gmail.json")
        if all(w in f'{m["subject"]} {m["body"]} {m["from"]}'.lower() for w in words)
    ]
    hits.sort(key=lambda m: m["received"], reverse=True)
    return {"status": "ok", "count": len(hits[:top]), "messages": hits[:top]}


@mcp.tool(annotations=READ)
def list_events(start_iso: str, end_iso: str) -> dict:
    """List Google Calendar events overlapping a time window.

    Args:
        start_iso: Window start, ISO 8601 with offset, e.g. "2026-11-05T00:00:00+01:00".
        end_iso: Window end, ISO 8601 with offset.
    Returns: {"status":"ok","count":int,"events":[{"id","subject","start","end","organizer","attendees","location"}]}
    """
    if err := _need(CALENDAR_READ_SCOPES, "calendar.events.readonly"):
        return err
    from datetime import datetime
    try:
        s, e = datetime.fromisoformat(start_iso), datetime.fromisoformat(end_iso)
    except ValueError:
        return {"status": "error", "error_code": "bad_request", "error_message": "use ISO 8601 with offset"}
    evs = [
        v for v in _load("calendar.json")
        if datetime.fromisoformat(v["start"]) < e and datetime.fromisoformat(v["end"]) > s
    ]
    evs.sort(key=lambda v: v["start"])
    return {"status": "ok", "count": len(evs), "events": evs}


@mcp.tool(annotations=READ)
def search_files(query: str, top: int = 5) -> dict:
    """Search Google Drive documents and sheets accessible to the user.

    Args:
        query: Free text matched against file name and preview.
        top: Max files (1-25).
    Returns: {"status":"ok","count":int,"files":[{"id","name","path","modified","sensitivity","preview"}]}
    """
    if err := _need(DRIVE_READ_SCOPES, "drive.readonly"):
        return err
    top = max(1, min(int(top), MAX_TOP))
    words = query.lower().replace("_", " ").split()
    hits = [
        f for f in _load("drive.json")
        if all(w in f'{f["name"]} {f["preview"]}'.lower().replace("_", " ") for w in words)
    ]
    return {"status": "ok", "count": len(hits[:top]), "files": hits[:top]}


@mcp.tool(annotations=WRITE)
def create_draft(to: list[str], subject: str, body: str) -> dict:
    """Create a Gmail DRAFT in the user's mailbox. Never sends.

    WRITE operation: requires human confirmation in interactive workflows.
    Args:
        to: Recipient email addresses.
        subject: Subject line.
        body: Plain-text body.
    Returns: {"status":"ok","draft_id":str,"web_link":str}
    """
    if err := _need(GMAIL_DRAFT_SCOPES, "gmail.compose"):
        return err
    if not to or len(to) > 10:
        return {"status": "error", "error_code": "bad_request", "error_message": "1-10 recipients"}
    draft_id = f"r-{uuid.uuid4().hex[:8]}"
    out = Path(os.getenv("DRAFTS_PATH", str(DATA / "drafts.jsonl")))
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a") as fh:
            fh.write(json.dumps({"id": draft_id, "to": to, "subject": subject, "body": body, "ts": time.time()}) + "\n")
    except Exception:
        pass
    return {
        "status": "ok",
        "draft_id": draft_id,
        "web_link": f"https://mail.google.com/mail/u/0/#drafts/{draft_id}",
    }


def main() -> None:
    from mcp.server.transport_security import TransportSecuritySettings
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "127.0.0.1")
    allowed = [h for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h]
    security = (
        TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=allowed, allowed_origins=[])
        if allowed else None
    )
    stateless = os.getenv("MCP_STATELESS", "0") == "1"
    anyio.run(
        lambda: mcp.run_streamable_http_async(
            host=host,
            port=port,
            streamable_http_path="/mcp",
            stateless_http=stateless,
            transport_security=security,
        )
    )


if __name__ == "__main__":
    main()

