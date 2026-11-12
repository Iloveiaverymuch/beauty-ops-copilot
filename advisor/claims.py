"""Durable, lightweight claims and returns engine for Google ADK.

Pure Python + SQLite persistence:
- Replaces LangGraph state machine with an explicit, deterministic state engine.
- Zero external graph framework dependencies.
- Handles customer returns, automatic refunds, and pending human reviews.
- Provides idempotent decision processing and failure retries.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from claims import policy
from claims.bus import DECISIONS_TOPIC, EVENTS_TOPIC, bus

RETAIL_API = os.getenv("RETAIL_API_URL", "http://127.0.0.1:8090")
_http = httpx.Client(timeout=float(os.getenv("RETAIL_API_TIMEOUT_S", "1.5")))
_db_lock = threading.RLock()

STATUS_TEXT = {
    "pending_review": "Dossier transmis à un conseiller: réponse sous 24 h ouvrées.",
    "approved": "Remboursement approuvé, en cours de traitement.",
    "refunded": "Remboursement effectué.",
    "refund_failed": "Remboursement approuvé, paiement en cours (nouvel essai automatique).",
    "rejected": "Demande non éligible selon notre politique de retours.",
    "rejected_by_reviewer": "Demande refusée après examen par un conseiller.",
    "needs_info": "Il manque une information pour traiter la demande.",
}


class RefundError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _get_db() -> sqlite3.Connection:
    backend_dir = Path(os.getenv("BACKEND_DATA_DIR", ".data"))
    backend_dir.mkdir(parents=True, exist_ok=True)
    db_path = backend_dir / "claims.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS claims (
                claim_id TEXT PRIMARY KEY,
                order_id TEXT,
                sku TEXT,
                customer_message TEXT,
                opened INTEGER,
                skin_reaction INTEGER,
                photo_provided INTEGER,
                amount_eur REAL,
                days_since_purchase INTEGER,
                status TEXT,
                rule TEXT,
                triage_json TEXT,
                decision_json TEXT,
                refund_json TEXT,
                history_json TEXT,
                next_json TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
    return conn


def _fetch_order(order_id: str, sku: str = "") -> dict[str, Any]:
    r = _http.get(f"{RETAIL_API}/orders/{order_id}")
    if r.status_code == 404:
        return {"found": False}
    r.raise_for_status()
    order = r.json()
    lines = order.get("lines", [])
    line = next((l for l in lines if l.get("sku") == sku), lines[0] if lines else {"sku": sku, "amount_eur": 0.0})
    return {
        "found": True,
        "order": order,
        "sku": line.get("sku", sku),
        "amount_eur": float(line.get("amount_eur", 0.0)),
        "days_since_purchase": int(order.get("days_since_purchase", 0)),
    }


def _execute_refund(claim_id: str, order_id: str, amount_eur: float, reason: str) -> dict[str, Any]:
    last = ""
    for attempt in range(3):
        try:
            r = _http.post(
                f"{RETAIL_API}/refunds",
                headers={"Idempotency-Key": claim_id},
                json={"order_id": order_id, "amount_eur": amount_eur, "reason": reason},
            )
            if r.status_code == 200:
                return r.json()
            last = f"http {r.status_code}"
        except httpx.HTTPError as e:
            last = type(e).__name__
        time.sleep(0.2 * (2**attempt))
    raise RefundError(f"refund API failed after 3 attempts: {last}")


def _rule_triage(skin_reaction: bool, amount_eur: float) -> dict[str, Any]:
    return {
        "category": "skin_reaction" if skin_reaction else "changed_mind",
        "severity": "medium" if skin_reaction else "low",
        "summary_for_reviewer": (
            f"Demande de remboursement de {amount_eur} EUR, "
            f"{'réaction cutanée signalée' if skin_reaction else 'produit non ouvert'}."
        ),
        "source": "rule",
    }


def status(claim_id: str) -> dict[str, Any]:
    with _db_lock, _get_db() as conn:
        row = conn.execute("SELECT * FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
        if not row:
            return {"status": "unknown_claim", "claim_id": claim_id}
        
        st = row["status"]
        history = json.loads(row["history_json"] or "[]")
        next_steps = json.loads(row["next_json"] or "[]")
        refund = json.loads(row["refund_json"] or "{}")

        return {
            "claim_id": claim_id,
            "status": st,
            "message": STATUS_TEXT.get(st, st),
            "rule": row["rule"],
            "amount_eur": row["amount_eur"],
            "refund_id": refund.get("refund_id"),
            "next": next_steps,
            "history": history,
        }


def open_claim(
    order_id: str,
    customer_message: str,
    opened: bool,
    skin_reaction: bool,
    photo_provided: bool = False,
    sku: str = "",
) -> dict[str, Any]:
    claim_id = "CLM-" + hashlib.sha256(f"{order_id}|{sku}|{time.time_ns()}".encode()).hexdigest()[:6].upper()
    now = _now()
    history = [{"ts": now, "step": "opened"}]

    order_info = _fetch_order(order_id, sku)
    if not order_info.get("found"):
        history.append({"ts": _now(), "step": "fetch_order", "result": "unknown_order"})
        history.append({"ts": _now(), "step": "close", "status": "rejected"})
        _save_claim(
            claim_id=claim_id,
            order_id=order_id,
            sku=sku,
            customer_message=customer_message,
            opened=opened,
            skin_reaction=skin_reaction,
            photo_provided=photo_provided,
            amount_eur=0.0,
            days_since_purchase=0,
            status="rejected",
            rule="unknown_order",
            triage={},
            decision={},
            refund={},
            history=history,
            next_steps=[],
        )
        return status(claim_id)

    amount_eur = order_info["amount_eur"]
    days_since_purchase = order_info["days_since_purchase"]
    sku = order_info["sku"]
    history.append({"ts": _now(), "step": "fetch_order", "sku": sku, "amount_eur": amount_eur})

    d = policy.evaluate(opened, skin_reaction, photo_provided, days_since_purchase, amount_eur)
    history.append({"ts": _now(), "step": "check_policy", "route": d.route, "rule": d.rule})

    triage_data = {}
    decision_data = {}
    refund_data = {}
    next_steps = []

    if d.route == "auto":
        try:
            rf = _execute_refund(claim_id, order_id, amount_eur, d.rule)
            refund_data = rf
            claim_status = "refunded"
            history.append({"ts": _now(), "step": "refund", "refund_id": rf["refund_id"], "duplicate": rf.get("duplicate", False), "attempt": 1})
            history.append({"ts": _now(), "step": "close", "status": "refunded"})
            bus().publish(EVENTS_TOPIC, {"type": "claim.closed", "claim_id": claim_id, "status": "refunded",
                                         "amount_eur": amount_eur, "rule": d.rule}, claim_id=claim_id)
        except RefundError as e:
            claim_status = "refund_failed"
            next_steps = ["refund"]
            bus().publish(EVENTS_TOPIC, {"type": "claim.refund_failed", "claim_id": claim_id, "error": str(e)}, claim_id=claim_id)
    elif d.route == "review":
        triage_data = _rule_triage(skin_reaction, amount_eur)
        history.append({"ts": _now(), "step": "triage", "source": triage_data["source"], "severity": triage_data["severity"], "ms": 0})
        claim_status = "pending_review"
        next_steps = ["human_review"]
        bus().publish(EVENTS_TOPIC, {"type": "claim.pending_review", "claim_id": claim_id}, claim_id=claim_id)
    else:  # rejected or needs_info
        claim_status = d.route
        history.append({"ts": _now(), "step": "close", "status": claim_status})
        bus().publish(EVENTS_TOPIC, {"type": "claim.closed", "claim_id": claim_id, "status": claim_status,
                                     "amount_eur": amount_eur, "rule": d.rule}, claim_id=claim_id)

    _save_claim(
        claim_id=claim_id,
        order_id=order_id,
        sku=sku,
        customer_message=customer_message,
        opened=opened,
        skin_reaction=skin_reaction,
        photo_provided=photo_provided,
        amount_eur=amount_eur,
        days_since_purchase=days_since_purchase,
        status=claim_status,
        rule=d.rule,
        triage=triage_data,
        decision=decision_data,
        refund=refund_data,
        history=history,
        next_steps=next_steps,
    )
    return status(claim_id)


def _save_claim(
    claim_id: str,
    order_id: str,
    sku: str,
    customer_message: str,
    opened: bool,
    skin_reaction: bool,
    photo_provided: bool,
    amount_eur: float,
    days_since_purchase: int,
    status: str,
    rule: str,
    triage: dict,
    decision: dict,
    refund: dict,
    history: list,
    next_steps: list,
) -> None:
    now = _now()
    with _db_lock, _get_db() as conn:
        conn.execute("""
            INSERT INTO claims (
                claim_id, order_id, sku, customer_message, opened, skin_reaction, photo_provided,
                amount_eur, days_since_purchase, status, rule, triage_json, decision_json,
                refund_json, history_json, next_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(claim_id) DO UPDATE SET
                status = excluded.status,
                rule = excluded.rule,
                triage_json = excluded.triage_json,
                decision_json = excluded.decision_json,
                refund_json = excluded.refund_json,
                history_json = excluded.history_json,
                next_json = excluded.next_json,
                updated_at = excluded.updated_at
        """, (
            claim_id, order_id, sku, customer_message, int(opened), int(skin_reaction), int(photo_provided),
            amount_eur, days_since_purchase, status, rule,
            json.dumps(triage, ensure_ascii=False),
            json.dumps(decision, ensure_ascii=False),
            json.dumps(refund, ensure_ascii=False),
            json.dumps(history, ensure_ascii=False),
            json.dumps(next_steps, ensure_ascii=False),
            now, now,
        ))


def submit_decision(claim_id: str, decision: str, reviewer: str) -> str:
    decision_id = hashlib.sha256(f"{claim_id}|{decision}|{reviewer}".encode()).hexdigest()[:12]
    return bus().publish(
        DECISIONS_TOPIC,
        {"claim_id": claim_id, "decision": decision, "reviewer": reviewer, "decision_id": decision_id},
        claim_id=claim_id,
    )


def handle_decision(msg: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    claim_id = msg.get("claim_id", "")
    decision = msg.get("decision", "")
    reviewer = msg.get("reviewer", "reviewer")

    st = status(claim_id)
    if st["status"] == "unknown_claim":
        return "unknown_claim", st

    # Fetch raw claim record
    with _db_lock, _get_db() as conn:
        row = conn.execute("SELECT * FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
        if not row:
            return "unknown_claim", st
        history = json.loads(row["history_json"] or "[]")
        order_id = row["order_id"]
        amount_eur = row["amount_eur"]
        rule = row["rule"]
        current_status = row["status"]
        triage_data = json.loads(row["triage_json"] or "{}")

    if current_status == "pending_review":
        history.append({"ts": _now(), "step": "human_review", "decision": decision, "reviewer": reviewer})
        decision_data = {"decision": decision, "reviewer": reviewer, "decided_at": _now()}

        if decision == "approve":
            try:
                rf = _execute_refund(claim_id, order_id, amount_eur, rule)
                history.append({"ts": _now(), "step": "refund", "refund_id": rf["refund_id"], "duplicate": rf.get("duplicate", False), "attempt": 1})
                history.append({"ts": _now(), "step": "close", "status": "refunded"})
                _save_claim(
                    claim_id=claim_id, order_id=order_id, sku=row["sku"], customer_message=row["customer_message"],
                    opened=bool(row["opened"]), skin_reaction=bool(row["skin_reaction"]), photo_provided=bool(row["photo_provided"]),
                    amount_eur=amount_eur, days_since_purchase=row["days_since_purchase"], status="refunded", rule=rule,
                    triage=triage_data, decision=decision_data, refund=rf, history=history, next_steps=[],
                )
                bus().publish(EVENTS_TOPIC, {"type": "claim.closed", "claim_id": claim_id, "status": "refunded",
                                             "amount_eur": amount_eur, "rule": rule}, claim_id=claim_id)
                return "resumed", status(claim_id)
            except RefundError as e:
                _save_claim(
                    claim_id=claim_id, order_id=order_id, sku=row["sku"], customer_message=row["customer_message"],
                    opened=bool(row["opened"]), skin_reaction=bool(row["skin_reaction"]), photo_provided=bool(row["photo_provided"]),
                    amount_eur=amount_eur, days_since_purchase=row["days_since_purchase"], status="refund_failed", rule=rule,
                    triage=triage_data, decision=decision_data, refund={}, history=history, next_steps=["refund"],
                )
                bus().publish(EVENTS_TOPIC, {"type": "claim.refund_failed", "claim_id": claim_id, "error": str(e)}, claim_id=claim_id)
                return "resumed", status(claim_id)
        else:  # reject
            history.append({"ts": _now(), "step": "close", "status": "rejected_by_reviewer"})
            _save_claim(
                claim_id=claim_id, order_id=order_id, sku=row["sku"], customer_message=row["customer_message"],
                opened=bool(row["opened"]), skin_reaction=bool(row["skin_reaction"]), photo_provided=bool(row["photo_provided"]),
                amount_eur=amount_eur, days_since_purchase=row["days_since_purchase"], status="rejected_by_reviewer", rule=rule,
                triage=triage_data, decision=decision_data, refund={}, history=history, next_steps=[],
            )
            bus().publish(EVENTS_TOPIC, {"type": "claim.closed", "claim_id": claim_id, "status": "rejected_by_reviewer",
                                         "amount_eur": amount_eur, "rule": rule}, claim_id=claim_id)
            return "resumed", status(claim_id)

    if current_status == "refund_failed":
        try:
            rf = _execute_refund(claim_id, order_id, amount_eur, rule)
            history.append({"ts": _now(), "step": "refund", "refund_id": rf["refund_id"], "duplicate": rf.get("duplicate", False), "attempt": 2})
            history.append({"ts": _now(), "step": "close", "status": "refunded"})
            _save_claim(
                claim_id=claim_id, order_id=order_id, sku=row["sku"], customer_message=row["customer_message"],
                opened=bool(row["opened"]), skin_reaction=bool(row["skin_reaction"]), photo_provided=bool(row["photo_provided"]),
                amount_eur=amount_eur, days_since_purchase=row["days_since_purchase"], status="refunded", rule=rule,
                triage=triage_data, decision=json.loads(row["decision_json"] or "{}"), refund=rf, history=history, next_steps=[],
            )
            bus().publish(EVENTS_TOPIC, {"type": "claim.closed", "claim_id": claim_id, "status": "refunded",
                                         "amount_eur": amount_eur, "rule": rule}, claim_id=claim_id)
            return "retried", status(claim_id)
        except RefundError as e:
            bus().publish(EVENTS_TOPIC, {"type": "claim.refund_failed", "claim_id": claim_id, "error": str(e)}, claim_id=claim_id)
            return "retried", status(claim_id)

    return "duplicate", st


def retry(claim_id: str) -> dict[str, Any]:
    outcome, st = handle_decision({"claim_id": claim_id, "decision": "approve", "reviewer": "retry"})
    return st


def pending() -> list[dict[str, Any]]:
    with _db_lock, _get_db() as conn:
        rows = conn.execute(
            "SELECT claim_id FROM claims WHERE status IN ('pending_review', 'refund_failed') ORDER BY created_at DESC"
        ).fetchall()
        return [status(r["claim_id"]) for r in rows]
