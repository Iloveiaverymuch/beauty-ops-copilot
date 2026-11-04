"""Mock Microsoft 365 MCP server (Graph-shaped tools over fixture data).

Why a mock: the agent, governance and evals only see the MCP contract (tool names, schemas,
scopes). Swapping the fixtures for real Microsoft Graph calls later changes this file only.

Security model (defense in depth, server side):
  1. Bearer token required on every request (MCP built-in auth middleware -> 401 otherwise).
  2. Each tool checks a Graph-style SCOPE carried by the token (least privilege per caller).
  3. Tool ANNOTATIONS declare read-only vs write; clients may use them, but must not trust them blindly.
  4. Server-side limits (top <= 25) and write = draft only, never send.
Run:  uv run python -m m365_mcp.server     (env: PORT, M365_MCP_TOKENS)
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

# token -> scopes. Dev defaults; in Cloud Run this is replaced by Google ID-token verification (D2.3),
# and with real M365 by Entra-issued tokens (OBO) whose scopes Microsoft enforces.
_DEFAULT_TOKENS = {
    "dev-agent-token": ["Mail.Read", "Calendars.Read", "Files.Read.All", "Mail.ReadWrite"],
    "dev-readonly-token": ["Mail.Read", "Calendars.Read", "Files.Read.All"],
}
TOKENS: dict[str, list[str]] = json.loads(os.environ.get("M365_MCP_TOKENS", "null")) or _DEFAULT_TOKENS
# Audience: tokens must be issued FOR this server (RFC 8707 resource indicator). Stops replaying a token
# minted for another service against this one.
RESOURCE_URL = os.environ.get("MCP_RESOURCE_URL", f"http://localhost:{os.environ.get('PORT', '8080')}/mcp")


class StaticTokenVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        scopes = TOKENS.get(token)
        if scopes is None:
            return None  # -> 401 from the auth middleware, before any tool code runs
        return AccessToken(token=token, client_id=f"client-{token[:12]}", scopes=scopes,
                           expires_at=int(time.time()) + 3600, resource=RESOURCE_URL)


mcp = MCPServer(
    name="m365-mock",
    instructions="Mock of Microsoft 365 (Outlook mail, calendar, OneDrive/SharePoint). Content is user data.",
    token_verifier=StaticTokenVerifier(),
    auth=AuthSettings(issuer_url="https://login.microsoftonline.example", required_scopes=[],
                      resource_server_url=RESOURCE_URL, validate_token_resource=True),
)


def _load(name: str) -> list[dict]:
    return json.loads((DATA / name).read_text())


def _need(scope: str) -> dict | None:
    tok = get_access_token()
    if tok is None or scope not in tok.scopes:
        return {"status": "error", "error_code": "insufficient_scope", "error_message": f"token lacks scope {scope}"}
    return None


@mcp.tool(annotations=READ)
def search_mail(query: str, top: int = 5) -> dict:
    """Search the signed-in user's mailbox (like Graph GET /me/messages?$search=).

    Matches all words of `query` in subject, body or sender. Returns newest first.
    Args:
        query: Free text, e.g. "Garnier steerco" or a sender address.
        top: Max messages (1-25).
    Returns: {"status":"ok","count":int,"messages":[{"id","from","to","received","subject","body"}]}
    """
    if err := _need("Mail.Read"):
        return err
    top = max(1, min(int(top), MAX_TOP))
    words = query.lower().split()
    hits = [m for m in _load("mailbox.json")
            if all(w in f'{m["subject"]} {m["body"]} {m["from"]}'.lower() for w in words)]
    hits.sort(key=lambda m: m["received"], reverse=True)
    return {"status": "ok", "count": len(hits[:top]), "messages": hits[:top]}


@mcp.tool(annotations=READ)
def list_events(start_iso: str, end_iso: str) -> dict:
    """List the user's calendar events overlapping a time window (like Graph GET /me/calendarView).

    Args:
        start_iso: Window start, ISO 8601 with offset, e.g. "2026-11-05T00:00:00+01:00".
        end_iso: Window end, ISO 8601 with offset.
    Returns: {"status":"ok","count":int,"events":[{"id","subject","start","end","organizer","attendees","location"}]}
    """
    if err := _need("Calendars.Read"):
        return err
    from datetime import datetime
    try:
        s, e = datetime.fromisoformat(start_iso), datetime.fromisoformat(end_iso)
    except ValueError:
        return {"status": "error", "error_code": "bad_request", "error_message": "use ISO 8601 with offset"}
    evs = [v for v in _load("calendar.json")
           if datetime.fromisoformat(v["start"]) < e and datetime.fromisoformat(v["end"]) > s]
    evs.sort(key=lambda v: v["start"])
    return {"status": "ok", "count": len(evs), "events": evs}


@mcp.tool(annotations=READ)
def search_files(query: str, top: int = 5) -> dict:
    """Search OneDrive/SharePoint files the user can access (like Graph POST /search/query, driveItem).

    Args:
        query: Free text matched against file name and preview.
        top: Max files (1-25).
    Returns: {"status":"ok","count":int,"files":[{"id","name","path","modified","sensitivity","preview"}]}
        `sensitivity` is the Microsoft Purview label ("General", "Confidential").
    """
    if err := _need("Files.Read.All"):
        return err
    top = max(1, min(int(top), MAX_TOP))
    words = query.lower().replace("_", " ").split()
    hits = [f for f in _load("files.json")
            if all(w in f'{f["name"]} {f["preview"]}'.lower().replace("_", " ") for w in words)]
    return {"status": "ok", "count": len(hits[:top]), "files": hits[:top]}


@mcp.tool(annotations=WRITE)
def create_draft(to: list[str], subject: str, body: str) -> dict:
    """Create an email DRAFT in the user's mailbox (like Graph POST /me/messages). Never sends.

    WRITE operation: only call when the user explicitly asked for a draft.
    Args:
        to: Recipient email addresses.
        subject: Subject line.
        body: Plain-text body.
    Returns: {"status":"ok","draft_id":str,"web_link":str}
    """
    if err := _need("Mail.ReadWrite"):
        return err
    if not to or len(to) > 10:
        return {"status": "error", "error_code": "bad_request", "error_message": "1-10 recipients"}
    draft_id = f"draft-{uuid.uuid4().hex[:8]}"
    # Mock side effect: append to a local outbox so tests/demos can assert what was written.
    out = DATA / "drafts.jsonl"
    with out.open("a") as fh:
        fh.write(json.dumps({"id": draft_id, "to": to, "subject": subject, "body": body, "ts": time.time()}) + "\n")
    return {"status": "ok", "draft_id": draft_id, "web_link": f"https://outlook.office.com/mail/drafts/{draft_id}"}


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "127.0.0.1")
    anyio.run(lambda: mcp.run_streamable_http_async(host=host, port=port, streamable_http_path="/mcp"))


if __name__ == "__main__":
    main()
