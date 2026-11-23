"""Orchestration engine behind ONE streaming contract, so the API, the UI, the KPIs and the evals don't care
about internal details. Built on Google ADK 2.13 implementing `stream()`.

Neutral events yielded by every engine:
    {"type": "text", "delta": str}                       model text, streamed in chunks
    {"type": "tool_call", "name": str, "args": dict}
    {"type": "tool_result", "name": str, "response": dict}
    {"type": "usage", "tokens_in": int, "tokens_out": int}
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncIterator, Protocol

from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, InMemorySessionService
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

DATA_DIR = Path(os.getenv("BACKEND_DATA_DIR", ".data"))


class Engine(Protocol):
    name: str

    def stream(self, thread_id: str, message: str, user_id: str = "anonymous") -> AsyncIterator[dict]: ...


CONFIRM = "adk_request_confirmation"


def build_session_service(
    app_name: str,
    mode: str | None = None,
    db_url: str | None = None,
) -> BaseSessionService:
    """Create the configured ADK session service.

    Modes:
      - `database`: ADK DatabaseSessionService (PostgreSQL on Cloud SQL / Docker, or sqlite+aiosqlite for tests)
      - `sqlite`: local file SqliteSessionService (single-instance dev default)
      - `memory`: InMemorySessionService (ephemeral unit tests)
    """
    resolved_mode = (mode or os.getenv("SESSIONS", "sqlite")).strip().lower()
    if resolved_mode == "database":
        url = (db_url or os.getenv("DATABASE_URL", "")).strip()
        if not url:
            raise ValueError("DATABASE_URL is required when SESSIONS=database")
        from google.adk.sessions import DatabaseSessionService

        return DatabaseSessionService(db_url=url)
    if resolved_mode == "sqlite":
        DATA_DIR.mkdir(exist_ok=True)
        return SqliteSessionService(str(DATA_DIR / f"sessions_{app_name}.db"))
    if resolved_mode == "memory":
        return InMemorySessionService()
    raise ValueError(f"Unsupported SESSIONS mode: {resolved_mode!r}")


class AdkEngine:
    """ADK Runner + session service.

    Supports DatabaseSessionService (Cloud SQL PostgreSQL / local Docker Postgres) for multi-instance
    deployments, SqliteSessionService for single-process local dev, and InMemorySessionService for tests.
    """

    def __init__(
        self,
        app=None,
        name: str = "adk",
        session_service: BaseSessionService | None = None,
        db_url: str | None = None,
    ):
        if app is None:
            from beauty_advisor.agent import app
        self.name = name
        self.app = app
        self.app_name = app.name
        sessions = session_service or build_session_service(
            app_name=self.app_name,
            mode="database" if db_url else None,
            db_url=db_url,
        )
        self.runner = Runner(app=app, session_service=sessions)
        self.run_config = RunConfig(streaming_mode=StreamingMode.SSE)

    async def _ensure_session(self, thread_id: str, user_id: str) -> None:
        s = self.runner.session_service
        existing = await s.get_session(app_name=self.app_name, user_id=user_id, session_id=thread_id)
        if existing is None:
            await s.create_session(
                app_name=self.app_name,
                user_id=user_id,
                session_id=thread_id,
                state={"user:id": user_id},
            )

    async def _handle_events(self, event_stream) -> AsyncIterator[dict]:
        streamed_partial = False
        async for ev in event_stream:
            if ev.author == "user":
                continue
            text = "".join(p.text or "" for p in (ev.content.parts if ev.content and ev.content.parts else [])
                           if not getattr(p, "thought", False))
            if ev.partial:
                if text:
                    streamed_partial = True
                    yield {"type": "text", "delta": text}
                continue
            # Non-partial event = aggregated final version: don't duplicate
            if text and not streamed_partial:
                yield {"type": "text", "delta": text}
            streamed_partial = False
            if ev.usage_metadata:
                yield {"type": "usage", "tokens_in": ev.usage_metadata.prompt_token_count or 0,
                       "tokens_out": ev.usage_metadata.candidates_token_count or 0,
                       "tokens_thinking": ev.usage_metadata.thoughts_token_count or 0}
            for fc in ev.get_function_calls() or []:
                if fc.name == CONFIRM:
                    orig = (fc.args or {}).get("originalFunctionCall", {}) or {}
                    yield {
                        "type": "confirmation_required",
                        "call_id": fc.id,
                        "tool": orig.get("name", "action"),
                        "args": orig.get("args", {}),
                    }
                else:
                    yield {"type": "tool_call", "name": fc.name, "args": dict(fc.args or {})}
            for fr in ev.get_function_responses() or []:
                yield {"type": "tool_result", "name": fr.name, "response": fr.response or {}}

    async def stream(self, thread_id: str, message: str, user_id: str = "anonymous") -> AsyncIterator[dict]:
        await self._ensure_session(thread_id, user_id=user_id)
        msg = types.Content(role="user", parts=[types.Part(text=message)])
        stream = self.runner.run_async(user_id=user_id, session_id=thread_id, new_message=msg,
                                       run_config=self.run_config)
        async for event in self._handle_events(stream):
            yield event

    async def resume_confirmation(
        self,
        thread_id: str,
        call_id: str,
        confirmed: bool,
        user_id: str = "anonymous",
    ) -> AsyncIterator[dict]:
        await self._ensure_session(thread_id, user_id=user_id)
        parts = [types.Part(function_response=types.FunctionResponse(
            id=call_id, name=CONFIRM, response={"confirmed": confirmed}
        ))]
        msg = types.Content(role="user", parts=parts)
        stream = self.runner.run_async(user_id=user_id, session_id=thread_id, new_message=msg,
                                       run_config=self.run_config)
        async for event in self._handle_events(stream):
            yield event
