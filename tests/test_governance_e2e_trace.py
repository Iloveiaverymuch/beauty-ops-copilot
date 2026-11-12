"""End-to-end (no network): real ADK runner + fake LLM + our plugin + in-memory OTel exporter.
Proves the governance decision lands ON the `execute_tool <tool>` span (ADK runs plugin callbacks inside it),
with the privacy allowlist intact. Written after "I can't find governance.* in Cloud Trace" (D2.2)."""
import asyncio
from typing import AsyncGenerator

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from copilot.governance import GovernancePlugin

EXPORTER = InMemorySpanExporter()


class _FakeLlm(BaseLlm):
    model: str = "fake"

    async def generate_content_async(self, llm_request, stream=False) -> AsyncGenerator[LlmResponse, None]:
        if any(p.function_response for p in (llm_request.contents[-1].parts or [])):
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="blocked")]))
        else:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
                name="create_draft", args={"to": ["riadh.test@gmail.com"], "subject": "s", "body": "secret body"}))]))


def create_draft(to: list[str], subject: str, body: str) -> dict:
    """Create a draft."""
    raise AssertionError("must never run: policy blocks external recipients")


def test_denial_lands_on_execute_tool_span_through_real_runner():
    tp = trace.get_tracer_provider()
    if isinstance(tp, trace.ProxyTracerProvider):
        tp = TracerProvider()
        trace.set_tracer_provider(tp)
    tp.add_span_processor(SimpleSpanProcessor(EXPORTER))
    runner = InMemoryRunner(app=App(name="t", root_agent=Agent(name="workspace_agent", model=_FakeLlm(), instruction="x",
                                                               tools=[create_draft]), plugins=[GovernancePlugin()]))

    async def go():
        await runner.session_service.create_session(app_name="t", user_id="u", session_id="s")
        async for _ in runner.run_async(user_id="u", session_id="s", new_message=types.Content(
                role="user", parts=[types.Part(text="draft to gmail")])):
            pass
    asyncio.run(go())

    span = next(s for s in EXPORTER.get_finished_spans() if s.name == "execute_tool create_draft")
    assert span.attributes["governance.denied"] is True
    assert span.attributes["governance.reason"] == "external_recipient"
    ev = next(e for e in span.events if e.name == "governance.decision")
    assert ev.attributes["decision"] == "deny" and "gmail.com" in ev.attributes["external_domains"]
    assert "riadh.test" not in str(ev.attributes) and "secret body" not in str(ev.attributes)
