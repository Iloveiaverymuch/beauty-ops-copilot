"""Entry points used by the chatbot tools, the back office (reviewer) and the Pub/Sub worker."""
from __future__ import annotations

import hashlib
import time

from langgraph.types import Command

from claims.bus import DECISIONS_TOPIC, EVENTS_TOPIC, bus
from claims.graph import RefundError, _now, graph

STATUS_TEXT = {  # what the customer-facing assistant may say about each state
    "pending_review": "Dossier transmis à un conseiller: réponse sous 24 h ouvrées.",
    "approved": "Remboursement approuvé, en cours de traitement.",
    "refunded": "Remboursement effectué.",
    "refund_failed": "Remboursement approuvé, paiement en cours (nouvel essai automatique).",
    "rejected": "Demande non éligible selon notre politique de retours.",
    "rejected_by_reviewer": "Demande refusée après examen par un conseiller.",
    "needs_info": "Il manque une information pour traiter la demande.",
}


def _cfg(claim_id: str) -> dict:
    return {"configurable": {"thread_id": claim_id}}


def status(claim_id: str) -> dict:
    """Status is DERIVED from the checkpoint: which node the graph is paused at, and the last values."""
    snap = graph().get_state(_cfg(claim_id))
    if not snap.values:
        return {"status": "unknown_claim", "claim_id": claim_id}
    v = snap.values
    st = v.get("status", "open")
    if snap.next == ("refund",):            # a refund node failed and is waiting to be re-run
        st = "refund_failed"
    elif snap.next == ("human_review",):
        st = "pending_review"
    return {"claim_id": claim_id, "status": st, "message": STATUS_TEXT.get(st, st), "rule": v.get("policy", {}).get("rule"),
            "amount_eur": v.get("amount_eur"), "refund_id": (v.get("refund") or {}).get("refund_id"),
            "next": list(snap.next), "history": v.get("history", [])}


def _run(claim_id: str, payload) -> dict:
    try:
        graph().invoke(payload, _cfg(claim_id))
    except RefundError as e:   # durable failure: checkpoint stays at "refund"; the worker/broker retries later
        bus().publish(EVENTS_TOPIC, {"type": "claim.refund_failed", "claim_id": claim_id, "error": str(e)}, claim_id=claim_id)
    return status(claim_id)


def open_claim(order_id: str, customer_message: str, opened: bool, skin_reaction: bool,
               photo_provided: bool = False, sku: str = "") -> dict:
    claim_id = "CLM-" + hashlib.sha256(f"{order_id}|{sku}|{time.time_ns()}".encode()).hexdigest()[:6].upper()
    st = _run(claim_id, {"claim_id": claim_id, "order_id": order_id, "sku": sku, "customer_message": customer_message,
                         "opened": opened, "skin_reaction": skin_reaction, "photo_provided": photo_provided,
                         "history": [{"ts": _now(), "step": "opened"}]})
    if st["status"] == "pending_review":
        bus().publish(EVENTS_TOPIC, {"type": "claim.pending_review", "claim_id": claim_id}, claim_id=claim_id)
    return st


def submit_decision(claim_id: str, decision: str, reviewer: str) -> str:
    """Back office -> Pub/Sub. The reviewer's click doesn't run the workflow: it publishes an event, the worker
    resumes the graph. Decoupled: the back office stays fast and the decision survives a worker outage."""
    decision_id = hashlib.sha256(f"{claim_id}|{decision}|{reviewer}".encode()).hexdigest()[:12]
    return bus().publish(DECISIONS_TOPIC, {"claim_id": claim_id, "decision": decision, "reviewer": reviewer,
                                           "decision_id": decision_id}, claim_id=claim_id)


def handle_decision(msg: dict) -> tuple[str, dict]:
    """Idempotent event handler (Pub/Sub = at-least-once). Returns (outcome, status). Outcomes:
      resumed   : graph was waiting for a human -> resume with the decision
      retried   : refund had failed -> re-run it (same Idempotency-Key, cannot pay twice)
      duplicate : already past this point -> do nothing (ack)
      unknown_claim : no such claim -> ack + alert (never resume a random thread)
    """
    st = status(msg["claim_id"])
    if st["status"] == "unknown_claim":
        return "unknown_claim", st          # ack and alert: a decision for a claim that doesn't exist
    if st["status"] == "pending_review":
        return "resumed", _run(msg["claim_id"], Command(resume={"decision": msg["decision"], "reviewer": msg["reviewer"],
                                                                "decision_id": msg.get("decision_id")}))
    if st["status"] == "refund_failed":
        return "retried", _run(msg["claim_id"], None)
    return "duplicate", st


def retry(claim_id: str) -> dict:
    """Re-run a failed refund (ops CLI / scheduled job): invoke(None) resumes from the failed node."""
    return _run(claim_id, None)


def pending() -> list[dict]:
    """Claims waiting for a human (scan of the checkpoint DB: fine for a demo; production = a claims table)."""
    import sqlite3
    from pathlib import Path
    import os
    db = Path(os.getenv("BACKEND_DATA_DIR", ".data")) / "claims.db"
    if not db.exists():
        return []
    ids = [r[0] for r in sqlite3.connect(str(db)).execute("SELECT DISTINCT thread_id FROM checkpoints")]
    return [s for s in map(status, ids) if s["status"] in ("pending_review", "refund_failed")]
