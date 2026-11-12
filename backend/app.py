"""Chatbot backend: FastAPI + Server-Sent Events, engine-agnostic.

    uv run uvicorn mock_api.stock_api:app --port 8090                         # terminal 1: retail API
    uv run --env-file copilot/.env uvicorn backend.app:app --port 8000        # terminal 2: this backend
    open http://localhost:8000                                                # chat UI

Endpoints
  POST /chat       {"message", "thread_id"?} -> text/event-stream:
                   meta, progress, text (chunks), cards, comparison, estimate, availability, delivery, sources,
                   handoff, suggestions, done (latency, time to first text, tokens, tools)
  POST /feedback   {"thread_id", "turn_id", "rating": "up"|"down", "comment"?} -> KPI log
  POST /chat/sync  same input as /chat -> one JSON response (for evaluations; same execution path)
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

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT / "copilot" / ".env")

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry import trace
from pydantic import BaseModel, Field

from backend.telemetry import setup_tracing

setup_tracing()   # BEFORE the agent is imported (engines import it lazily)

from backend.engines import DATA_DIR, AdkEngine  # noqa: E402
from backend.ui_events import TOOL_LABELS, suggestions, ui_event_for_tool_result  # noqa: E402
from guardrails.model_armor import sanitize_prompt  # noqa: E402

log = logging.getLogger("advisor.backend")
tracer = trace.get_tracer("advisor.backend")
STATIC = Path(__file__).resolve().parent / "static"
ESCALATION_TOOL = "request_human_advisor"
EUR_PER_M_IN = float(os.getenv("PRICE_EUR_PER_M_INPUT", "0"))      # set from the model's price list to log EUR/turn
EUR_PER_M_OUT = float(os.getenv("PRICE_EUR_PER_M_OUTPUT", "0"))

app = FastAPI(title="Beauty Advisor API", version="1.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _init_engines() -> dict[str, AdkEngine]:
    from beauty_advisor.agent import app as advisor_app
    from copilot.agent import app as copilot_app
    adv = AdkEngine(app=advisor_app, name="advisor")
    cop = AdkEngine(app=copilot_app, name="copilot")
    return {"advisor": adv, "copilot": cop, "adk": adv}


ENGINES = _init_engines()
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)   # one turn at a time per conversation
_turns: dict[str, int] = defaultdict(int)                       # turn counter per conversation (chips, KPIs)


class ChatRequest(BaseModel):
    """Validated at the edge: bounded size (cost + abuse), thread ids are opaque tokens we generate."""
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    engine: str = "adk"


class ConfirmRequest(BaseModel):
    """Human-in-the-loop review confirmation."""
    thread_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    call_id: str
    confirmed: bool
    engine: str = "copilot"


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
        reply = []
        tool_t0: list[float] = []
        error = ""
        yield sse("meta", {"thread_id": thread_id, "turn_id": turn_id, "engine": engine.name})
        async with _locks[thread_id]:
            _turns[thread_id] += 1
            with tracer.start_as_current_span("chat.turn") as span:
                span.set_attributes({"chat.turn_id": turn_id, "chat.engine": engine.name})
                # Defense-in-depth Layer 1: Google Cloud Model Armor prompt sanitization
                armor = sanitize_prompt(req.message)
                if not armor["safe"]:
                    span.set_attributes({"guardrail.model_armor.blocked": True, "guardrail.model_armor.filter": armor["filter_type"]})
                    yield sse("governance_blocked", {"status": "blocked", "reason": armor["message"], "filter": armor["filter_type"]})
                    yield sse("text", {"delta": armor["message"]})
                    ms = round((time.perf_counter() - t0) * 1000)
                    record = {"kind": "turn", "thread_id": thread_id, "turn_id": turn_id, "engine": engine.name,
                              "latency_ms": ms, "ttft_ms": ms, "tokens_in": 0, "tokens_out": 0, "tokens_thinking": 0,
                              "model_calls": 0, "tools_ms": 0, "model_ms": 0, "text_chunks": 1,
                              "cost_eur": 0.0, "tools": [], "kb_searches": 0, "kb_abstained": False,
                              "api_unavailable": False, "escalated": False, "error": "", "failed": False,
                              "model_armor_blocked": True}
                    kpi(record)
                    yield sse("done", {k: record[k] for k in ("turn_id", "latency_ms", "ttft_ms", "tokens_in", "tokens_out",
                                                              "tokens_thinking", "model_ms", "tools_ms", "tools", "failed")})
                    return
                try:
                    async for ev in engine.stream(thread_id, req.message):
                        if ev["type"] == "text":
                            ttft = ttft or time.perf_counter() - t0
                            chunks += 1
                            reply.append(ev["delta"])
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
                        elif ev["type"] == "confirmation_required":
                            yield sse("confirmation_required", {
                                "call_id": ev["call_id"],
                                "tool": ev["tool"],
                                "args": ev["args"],
                            })
                        elif ev["type"] == "usage":
                            tok_in += ev["tokens_in"]
                            tok_out += ev["tokens_out"]
                            tok_think += ev.get("tokens_thinking", 0)
                            model_calls += 1
                except Exception as e:  # noqa: BLE001 - the customer gets a message, the logs get the reason
                    error = f"{type(e).__name__}: {str(e)[:200]}"
                    log.exception("turn failed")
                    yield sse("text", {"delta": "Désolée, je rencontre un problème technique. Pouvez-vous réessayer ?"})
                chips = suggestions(results, first_turn=_turns[thread_id] == 1 and not req.thread_id, reply_text="".join(reply))
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
                          "escalated": ESCALATION_TOOL in tools, "error": error, "failed": bool(error)}
                span.set_attributes({k: v for k, v in record.items() if isinstance(v, (int, float, bool, str)) and v is not None})
                kpi(record)
                yield sse("done", {k: record[k] for k in ("turn_id", "latency_ms", "ttft_ms", "tokens_in", "tokens_out",
                                                          "tokens_thinking", "model_ms", "tools_ms", "tools", "failed")})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/chat/confirm")
async def chat_confirm(req: ConfirmRequest):
    if req.engine not in ENGINES:
        return StreamingResponse(iter([sse("error", {"message": f"engine {req.engine} not available"})]),
                                 media_type="text/event-stream")
    thread_id = req.thread_id
    turn_id = uuid.uuid4().hex[:12]
    engine = ENGINES[req.engine]

    async def gen():
        t0 = time.perf_counter()
        ttft = None
        tools: list[str] = []
        results: list[tuple[str, dict]] = []
        tok_in = tok_out = tok_think = chunks = model_calls = 0
        tool_ms = 0.0
        reply = []
        tool_t0: list[float] = []
        error = ""
        yield sse("meta", {"thread_id": thread_id, "turn_id": turn_id, "engine": engine.name})
        async with _locks[thread_id]:
            _turns[thread_id] += 1
            with tracer.start_as_current_span("chat.confirm") as span:
                span.set_attributes({"chat.turn_id": turn_id, "chat.engine": engine.name, "chat.confirmed": req.confirmed})
                try:
                    async for ev in engine.resume_confirmation(thread_id, req.call_id, req.confirmed):
                        if ev["type"] == "text":
                            ttft = ttft or time.perf_counter() - t0
                            chunks += 1
                            reply.append(ev["delta"])
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
                        elif ev["type"] == "confirmation_required":
                            yield sse("confirmation_required", {
                                "call_id": ev["call_id"],
                                "tool": ev["tool"],
                                "args": ev["args"],
                            })
                        elif ev["type"] == "usage":
                            tok_in += ev["tokens_in"]
                            tok_out += ev["tokens_out"]
                            tok_think += ev.get("tokens_thinking", 0)
                            model_calls += 1
                except Exception as e:
                    error = f"{type(e).__name__}: {str(e)[:200]}"
                    log.exception("confirm turn failed")
                    yield sse("text", {"delta": "Erreur lors de la confirmation de l'action."})
                chips = suggestions(results, first_turn=False, reply_text="".join(reply))
                if chips:
                    yield sse("suggestions", {"chips": chips})
                ms = round((time.perf_counter() - t0) * 1000)
                record = {"kind": "turn_confirm", "thread_id": thread_id, "turn_id": turn_id, "engine": engine.name,
                          "latency_ms": ms, "ttft_ms": round(ttft * 1000) if ttft else None,
                          "tokens_in": tok_in, "tokens_out": tok_out, "tokens_thinking": tok_think,
                          "model_calls": model_calls, "tools_ms": round(tool_ms * 1000),
                          "model_ms": ms - round(tool_ms * 1000), "text_chunks": chunks,
                          "cost_eur": round(tok_in / 1e6 * EUR_PER_M_IN + tok_out / 1e6 * EUR_PER_M_OUT, 6),
                          "tools": tools, "confirmed": req.confirmed, "error": error, "failed": bool(error)}
                span.set_attributes({k: v for k, v in record.items() if isinstance(v, (int, float, bool, str)) and v is not None})
                kpi(record)
                yield sse("done", {k: record[k] for k in ("turn_id", "latency_ms", "ttft_ms", "tokens_in", "tokens_out",
                                                          "tokens_thinking", "model_ms", "tools_ms", "tools", "failed")})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/chat/sync")
async def chat_sync(req: ChatRequest):
    """Buffer the existing chat stream for HTTP evaluation clients. One turn, one session update, one KPI record.

    These are complete frames yielded by our own generator, not arbitrary network chunks. Retaining structured
    events lets evaluations check actual cards/prices/tools rather than guessing them from generated prose.
    `status=error` distinguishes a technical fallback from a successful answer (HTTP 200 alone is insufficient).
    """
    response = await chat(req)
    out = {"status": "error", "thread_id": req.thread_id, "turn_id": None,
           "text": "", "events": [], "metrics": {}}
    async for frame in response.body_iterator:
        if isinstance(frame, bytes):
            frame = frame.decode("utf-8")
        event_line, data_line = frame.strip().split("\n", 1)
        event = event_line.removeprefix("event: ")
        data = json.loads(data_line.removeprefix("data: "))
        out["events"].append({"event": event, "data": data})
        if event == "meta":
            out.update(thread_id=data["thread_id"], turn_id=data["turn_id"])
        elif event == "text":
            out["text"] += data["delta"]
        elif event == "done":
            out["metrics"] = data
            out["status"] = "error" if data["failed"] else "ok"
    return out


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


class DevisPdfRequest(BaseModel):
    sku: str = "SKU-PROV"
    name: str = "Produit sélectionné"
    days: int = 30
    units: int = 1
    size_ml: int | float = 50
    total_eur: float = 0.0
    savings_eur: float = 0.0
    usage_ml_per_day: float = 1.0


@app.post("/devis/pdf")
async def devis_pdf(req: DevisPdfRequest):
    """Generate and return a provisional quote PDF with mandatory legal mentions."""
    from advisor.devis_pdf import generate_devis_pdf
    pdf_path = generate_devis_pdf(req.model_dump())
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"Devis_Provisoire_{req.days}j.pdf",
    )


@app.get("/devis/pdf")
async def devis_pdf_get(
    sku: str = "SKU-PROV",
    name: str = "Produit sélectionné",
    days: int = 30,
    units: int = 1,
    size_ml: float = 50.0,
    total_eur: float = 0.0,
    savings_eur: float = 0.0,
    usage_ml_per_day: float = 1.0,
):
    """Direct link / GET download for provisional quote PDF."""
    from advisor.devis_pdf import generate_devis_pdf
    pdf_path = generate_devis_pdf({
        "sku": sku, "name": name, "days": days, "units": units,
        "size_ml": size_ml, "total_eur": total_eur, "savings_eur": savings_eur,
        "usage_ml_per_day": usage_ml_per_day,
    })
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"Devis_Provisoire_{days}j.pdf",
    )
