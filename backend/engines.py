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
from google.adk.sessions import InMemorySessionService
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

DATA_DIR = Path(os.getenv("BACKEND_DATA_DIR", ".data"))


class Engine(Protocol):
    name: str

    def stream(self, thread_id: str, message: str) -> AsyncIterator[dict]: ...


class AdkEngine:
    """ADK Runner + session service. Sessions persist in SQLite locally (a backend restart keeps conversations);
    in production: DatabaseSessionService on Cloud SQL, or VertexAiSessionService (Agent Platform Sessions)."""
    name = "adk"

    def __init__(self):
        from beauty_advisor.agent import app   # imported here: telemetry must be configured before the agent loads
        if os.getenv("SESSIONS", "sqlite") == "sqlite":
            DATA_DIR.mkdir(exist_ok=True)
            sessions = SqliteSessionService(str(DATA_DIR / "sessions.db"))
        else:
            sessions = InMemorySessionService()
        self.runner = Runner(app=app, session_service=sessions)
        self.app_name = app.name
        # SSE streaming: the model's text arrives in partial events -> the customer sees words within ~1-2 s
        # instead of waiting for the whole answer (time-to-first-token is the latency users perceive).
        self.run_config = RunConfig(streaming_mode=StreamingMode.SSE)

    async def _ensure_session(self, thread_id: str) -> None:
        s = self.runner.session_service
        if await s.get_session(app_name=self.app_name, user_id="web", session_id=thread_id) is None:
            await s.create_session(app_name=self.app_name, user_id="web", session_id=thread_id)

    async def stream(self, thread_id: str, message: str) -> AsyncIterator[dict]:
        await self._ensure_session(thread_id)
        streamed_partial = False
        msg = types.Content(role="user", parts=[types.Part(text=message)])
        async for ev in self.runner.run_async(user_id="web", session_id=thread_id, new_message=msg,
                                              run_config=self.run_config):
            if ev.author == "user":
                continue
            text = "".join(p.text or "" for p in (ev.content.parts if ev.content and ev.content.parts else [])
                           if not getattr(p, "thought", False))
            if ev.partial:
                if text:
                    streamed_partial = True
                    yield {"type": "text", "delta": text}
                continue
            # Non-partial event = the aggregated final version of what was just streamed: don't send it twice.
            if text and not streamed_partial:
                yield {"type": "text", "delta": text}
            streamed_partial = False
            if ev.usage_metadata:          # counted on final events only (partials repeat cumulative counts)
                yield {"type": "usage", "tokens_in": ev.usage_metadata.prompt_token_count or 0,
                       "tokens_out": ev.usage_metadata.candidates_token_count or 0,
                       "tokens_thinking": ev.usage_metadata.thoughts_token_count or 0}
            for fc in ev.get_function_calls() or []:
                yield {"type": "tool_call", "name": fc.name, "args": dict(fc.args or {})}
            for fr in ev.get_function_responses() or []:
                yield {"type": "tool_result", "name": fr.name, "response": fr.response or {}}
