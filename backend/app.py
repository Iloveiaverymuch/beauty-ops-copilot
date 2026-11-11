"""Chatbot backend: FastAPI + Server-Sent Events, engine-agnostic.

    uv run uvicorn mock_api.stock_api:app --port 8090                         # terminal 1: retail API
    uv run --env-file copilot/.env uvicorn backend.app:app --port 8000        # terminal 2: this backend
    open http://localhost:8000                                                # chat UI

Endpoints
  POST /chat       {"message", "thread_id"?} -> text/event-stream:
                   meta, progress, text (chunks), cards, comparison, estimate, availability, delivery, sources,
                   handoff, suggestions, done (latency, time to first text, tokens, tools)
  POST /feedback   {"thread_id", "turn_id", "rating": "up"|"down", "comment"?} -> KPI log
  GET  /health

Why SSE and not WebSocket: one-way server -> client stream over plain HTTP (works through proxies, load balancers
and Cloud Run, auto-reconnect in browsers); the client only sends one message per turn, so a full-duplex channel
buys nothing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry import trace
from pydantic import BaseModel, Field

from backend.telemetry import setup_tracing

setup_tracing()   # BEFORE the agent is imported (engines import it lazily)

from backend.engines import DATA_DIR, AdkEngine  # noqa: E402
from backend.ui_events import TOOL_LABELS, suggestions, ui_event_for_tool_result  # noqa: E402

log = logging.getLogger("advisor.backend")
tracer = trace.get_tracer("advisor.backend")
STATIC = Path(__file__).resolve().parent / "static"
ESCALATION_TOOL = "request_human_advisor"
EUR_PER_M_IN = float(os.getenv("PRICE_EUR_PER_M_INPUT", "0"))      # set from the model's price list to log EUR/turn
EUR_PER_M_OUT = float(os.getenv("PRICE_EUR_PER_M_OUTPUT", "0"))

app = FastAPI(title="Beauty Advisor API", version="1.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
ENGINES = {"adk": AdkEngine()}
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)   # one turn at a time per conversation


class ChatRequest(BaseModel):
    """Validated at the edge: bounded size (cost + abuse), thread ids are opaque tokens we generate."""
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    engine: Literal["adk", "langgraph"] = "adk"


class FeedbackRequest(BaseModel):
    thread_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    turn_id: str = Field(pattern=r"^[a-f0-9]{12}$")
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=500)


def kpi(record: dict) -> None:
    """One JSON line per turn / feedback: the raw material for dashboards (latency, cost, abstention, escalation,
    satisfaction). Structured, no message text (personal data), joinable with traces via turn_id."""
    line = json.dumps({"ts": round(time.time(), 3), **record}, ensure_ascii=False)
    log.warning("KPI %s", line)
    DATA_DIR.mkdir(exist_ok=True)
    with (DATA_DIR / "kpi.jsonl").open("a") as fh:
        fh.write(line + "\n")


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "engines": list(ENGINES)}


@app.post("/chat")
async def chat(req: ChatRequest):
    if req.engine not in ENGINES:
        return StreamingResponse(iter([sse("error", {"message": f"engine {req.engine} not available"})]),
                                 media_type="text/event-stream")
    thread_id = req.thread_id or uuid.uuid4().hex
    turn_id = uuid.uuid4().hex[:12]
    engine = ENGINES[req.engine]

    async def gen():
        t0 = time.perf_counter()
        ttft = None
        tools: list[str] = []
        results: list[tuple[str, dict]] = []
        tok_in = tok_out = tok_think = chunks = model_calls = 0
        tool_ms = 0.0
        tool_t0: list[float] = []
        error = ""
        yield sse("meta", {"thread_id": thread_id, "turn_id": turn_id, "engine": engine.name})
        async with _locks[thread_id]:
            with tracer.start_as_current_span("chat.turn") as span:
                span.set_attributes({"chat.turn_id": turn_id, "chat.engine": engine.name})
                try:
                    async for ev in engine.stream(thread_id, req.message):
                        if ev["type"] == "text":
                            ttft = ttft or time.perf_counter() - t0
                            chunks += 1
                            yield sse("text", {"delta": ev["delta"]})
                        elif ev["type"] == "tool_call":
                            tools.append(ev["name"])
                            tool_t0.append(time.perf_counter())
                            yield sse("progress", {"label": TOOL_LABELS.get(ev["name"], ev["name"])})
                        elif ev["type"] == "tool_result":
                            if tool_t0:
                                tool_ms += time.perf_counter() - tool_t0.pop(0)
                            results.append((ev["name"], ev["response"]))
                            ui = ui_event_for_tool_result(ev["name"], ev["response"])
                            if ui:
                                yield sse(ui["event"], ui["data"])
                        elif ev["type"] == "usage":
                            tok_in += ev["tokens_in"]
                            tok_out += ev["tokens_out"]
                            tok_think += ev.get("tokens_thinking", 0)
                            model_calls += 1
                except Exception as e:  # noqa: BLE001 - the customer gets a message, the logs get the reason
                    error = f"{type(e).__name__}: {str(e)[:200]}"
                    log.exception("turn failed")
                    yield sse("text", {"delta": "Désolée, je rencontre un problème technique. Pouvez-vous réessayer ?"})
                chips = suggestions(results, has_products_in_thread=any(n == "search_products" for n, _ in results))
                if chips:
                    yield sse("suggestions", {"chips": chips})
                kb = [r for n, r in results if n == "search_knowledge"]
                ms = round((time.perf_counter() - t0) * 1000)
                record = {"kind": "turn", "thread_id": thread_id, "turn_id": turn_id, "engine": engine.name,
                          "latency_ms": ms, "ttft_ms": round(ttft * 1000) if ttft else None,
                          "tokens_in": tok_in, "tokens_out": tok_out, "tokens_thinking": tok_think,
                          "model_calls": model_calls, "tools_ms": round(tool_ms * 1000),
                          "model_ms": ms - round(tool_ms * 1000), "text_chunks": chunks,
                          "cost_eur": round(tok_in / 1e6 * EUR_PER_M_IN + tok_out / 1e6 * EUR_PER_M_OUT, 6),
                          "tools": tools, "kb_searches": len(kb),
                          "kb_abstained": any(r.get("status") == "no_answer" for r in kb),
                          "api_unavailable": any(r.get("status") == "unavailable" for _, r in results),
                          "escalated": ESCALATION_TOOL in tools, "error": error}
                span.set_attributes({k: v for k, v in record.items() if isinstance(v, (int, float, bool, str)) and v is not None})
                kpi(record)
                yield sse("done", {k: record[k] for k in ("turn_id", "latency_ms", "ttft_ms", "tokens_in", "tokens_out",
                                                          "tokens_thinking", "model_ms", "tools_ms", "tools")})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reviewer: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")


@app.get("/claims/pending")
def claims_pending():
    """Back office: claims waiting for a human or for a refund retry (no auth here: in production behind IAP/SSO)."""
    from claims.service import pending
    return [{k: c[k] for k in ("claim_id", "status", "amount_eur", "rule")} for c in pending()]


@app.post("/claims/{claim_id}/decision")
def claims_decide(claim_id: str, req: DecisionRequest):
    """Reviewer decision -> published as an event (Pub/Sub with CLAIMS_BUS=pubsub; the worker resumes the graph).
    With the local bus there is no worker, so the event is handled in-process (same idempotent handler)."""
    from fastapi import HTTPException

    from claims.bus import LocalBus, bus
    from claims.service import handle_decision, status, submit_decision
    current = status(claim_id)["status"]
    if current == "unknown_claim":
        raise HTTPException(404, f"unknown claim {claim_id}")
    if current not in ("pending_review", "refund_failed"):
        raise HTTPException(409, f"claim {claim_id} is '{current}', not waiting for a decision")
    msg_id = submit_decision(claim_id, req.decision, req.reviewer)
    kpi({"kind": "claim_decision", "claim_id": claim_id, "decision": req.decision, "reviewer": req.reviewer})
    if isinstance(bus(), LocalBus):
        outcome, st = handle_decision({"claim_id": claim_id, "decision": req.decision, "reviewer": req.reviewer})
        return {"message_id": msg_id, "outcome": outcome, "status": st["status"]}
    return {"message_id": msg_id, "outcome": "published"}


@app.post("/feedback")
def feedback(req: FeedbackRequest):
    kpi({"kind": "feedback", **req.model_dump()})
    return {"status": "ok"}
