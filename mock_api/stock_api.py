"""Mock retail API (stock, delivery, orders, refunds): the "external APIs" the chatbot and the claims workflow use.

    uv run uvicorn mock_api.stock_api:app --port 8090

Deterministic data (hash of sku + store) so evals are reproducible. A fault switch lets the demo show what the
chatbot does when a dependency fails:
    curl -X POST "localhost:8090/admin/fault?mode=down"   # every call -> 503
    curl -X POST "localhost:8090/admin/fault?mode=slow"   # every call sleeps 3 s (> client timeout)
    curl -X POST "localhost:8090/admin/fault?mode=none"   # back to normal
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="Mock retail API (stock + delivery)")
SKUS = {p["sku"] for p in json.loads((Path(__file__).resolve().parents[1] / "advisor" / "data" / "products.json").read_text())}
STORES = {"paris-rivoli": "Paris Rivoli", "lyon-part-dieu": "Lyon Part-Dieu", "nanterre": "Nanterre",
          "lille-centre": "Lille Centre", "marseille-prado": "Marseille Prado"}
FAULT = {"mode": "none"}


def _h(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)


async def _fault() -> None:
    if FAULT["mode"] == "down":
        raise HTTPException(503, "service unavailable (simulated)")
    if FAULT["mode"] == "slow":
        await asyncio.sleep(3)


@app.post("/admin/fault")
def set_fault(mode: str = Query(pattern="^(none|down|slow)$")):
    FAULT["mode"] = mode
    return FAULT


@app.get("/stores")
async def stores():
    await _fault()
    return [{"id": k, "name": v} for k, v in STORES.items()]


@app.get("/stock/{sku}")
async def stock(sku: str, store: str = Query(...)):
    await _fault()
    if sku not in SKUS:
        raise HTTPException(404, "unknown sku")
    if store not in STORES:
        raise HTTPException(404, "unknown store")
    qty = _h(sku, store) % 9 - 2            # -2..6 -> ~1/3 out of stock
    qty = max(qty, 0)
    return {"sku": sku, "store": store, "store_name": STORES[store], "quantity": qty,
            "click_and_collect": "2 h" if qty > 0 else "3 à 5 jours ouvrés"}


@app.get("/delivery")
async def delivery(sku: str, postcode: str = Query(pattern=r"^\d{5}$")):
    await _fault()
    if sku not in SKUS:
        raise HTTPException(404, "unknown sku")
    if postcode.startswith(("97", "98", "20")):   # overseas + Corsica: not served (policy: mainland France only)
        return {"sku": sku, "postcode": postcode, "deliverable": False, "options": []}
    today = date.today()
    def business_days(n):  # noqa: E306
        d = today
        while n:
            d += timedelta(days=1)
            if d.weekday() < 5:
                n -= 1
        return d.isoformat()
    in_stock = _h(sku, "warehouse") % 10 != 0       # 10% warehouse out of stock
    opts = [{"type": "standard", "eta_from": business_days(3), "eta_to": business_days(5), "fee_eur": 4.90,
             "free_from_eur": 35.0}]
    if in_stock:
        opts.append({"type": "express", "eta": business_days(1), "fee_eur": 6.90, "order_before": "13:00"})
    return {"sku": sku, "postcode": postcode, "deliverable": True, "warehouse_in_stock": in_stock, "options": opts}


# ------------------------------------------------------------------------------------------------- orders + refunds
# Orders: fixed demo orders, dated relative to today so the return windows always behave the same way.
_ORDERS = {  # order_id: (days_ago, customer_id, [(sku, qty, unit_price_paid)])
    "CMD-1001": (5, "cust-alice", [("LUM-SER-001", 1, 9.90)]),
    "CMD-1002": (10, "cust-alice", [("DER-SER-008", 2, 19.90)]),
    "CMD-1003": (40, "cust-alice", [("AQU-CRE-002", 1, 15.90)]),
    "CMD-1004": (3, "cust-bob", [("SOL-SPF-002", 1, 14.90)]),
    "CMD-1005": (12, "cust-bob", [("AQU-CRE-007", 1, 29.90), ("DER-SER-008", 1, 19.90)]),
    "CMD-12345": (5, "cust-alice", [("DER-SER-008", 2, 19.90)]),
}
REFUNDS: dict[str, dict] = {}   # idempotency_key -> refund (in memory: the demo's "payment system")


@app.get("/orders/{order_id}")
async def order(order_id: str):
    await _fault()
    if order_id not in _ORDERS:
        raise HTTPException(404, "unknown order")
    days, customer_id, lines = _ORDERS[order_id]
    return {"order_id": order_id, "customer_id": customer_id,
            "purchase_date": (date.today() - timedelta(days=days)).isoformat(),
            "days_since_purchase": days,
            "lines": [{"sku": s, "qty": q, "unit_price_eur": u, "amount_eur": round(q * u, 2)} for s, q, u in lines]}


class RefundRequest(BaseModel):
    order_id: str
    amount_eur: float
    reason: str


@app.post("/refunds")
async def refund(req: RefundRequest, idempotency_key: str = Header(...)):
    """Exactly-once refunds: the caller sends an Idempotency-Key (the claim id). A retry or a duplicate event with
    the same key returns the ORIGINAL refund instead of paying twice."""
    await _fault()
    if idempotency_key in REFUNDS:
        return {**REFUNDS[idempotency_key], "duplicate": True}
    r = {"refund_id": "RF-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:8].upper(),
         "order_id": req.order_id, "amount_eur": req.amount_eur, "status": "issued", "duplicate": False}
    REFUNDS[idempotency_key] = r
    return r


# ------------------------------------------------------------------------------------------------- appointments (W2)
# Two-phase booking: hold_slot -> confirm_slot (with Idempotency-Key). Prevents double booking on retries.
APPOINTMENT_HOLDS: dict[str, dict] = {}        # hold_id -> {hold_id, store, slot_id, customer_id, topic}
APPOINTMENTS: dict[str, dict] = {}             # idempotency_key -> confirmed appointment
_SLOT_HOURS = ("10:00", "11:00", "14:00", "15:30", "17:00")


def _slot_taken(store: str, slot_id: str, exclude_customer: str | None = None) -> bool:
    for appt in APPOINTMENTS.values():
        if appt["store"] == store and appt["slot_id"] == slot_id:
            return True
    for hold in APPOINTMENT_HOLDS.values():
        if hold["store"] == store and hold["slot_id"] == slot_id:
            if exclude_customer is None or hold["customer_id"] != exclude_customer:
                return True
    return False


@app.get("/appointments/slots")
async def appointment_slots(store: str = Query(...), date_str: str = Query(..., alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$")):
    await _fault()
    if store not in STORES:
        raise HTTPException(404, "unknown store")
    slots = []
    for hour in _SLOT_HOURS:
        slot_id = f"{date_str}T{hour}"
        if not _slot_taken(store, slot_id):
            slots.append({"slot_id": slot_id, "store": store, "store_name": STORES[store], "duration_min": 30})
    return {"store": store, "date": date_str, "slots": slots}


class HoldSlotRequest(BaseModel):
    store: str
    slot_id: str
    customer_id: str
    topic: str = "consultation beauté"


@app.post("/appointments/hold")
async def hold_slot(req: HoldSlotRequest):
    await _fault()
    if req.store not in STORES:
        raise HTTPException(404, "unknown store")
    # If already confirmed for this store+slot, reject unless same customer is retrying via confirm
    for appt in APPOINTMENTS.values():
        if appt["store"] == req.store and appt["slot_id"] == req.slot_id:
            if appt["customer_id"] == req.customer_id:
                return {"hold_id": appt["hold_id"], "store": req.store, "slot_id": req.slot_id,
                        "status": "already_confirmed"}
            raise HTTPException(409, "slot already booked")
    # Re-use existing hold for the same customer+store+slot (idempotent hold)
    for hold in APPOINTMENT_HOLDS.values():
        if hold["store"] == req.store and hold["slot_id"] == req.slot_id:
            if hold["customer_id"] == req.customer_id:
                return {**hold, "status": "held"}
            raise HTTPException(409, "slot currently held by another customer")
    hold_id = "HLD-" + hashlib.sha256(f"{req.store}|{req.slot_id}|{req.customer_id}".encode()).hexdigest()[:8].upper()
    hold = {"hold_id": hold_id, "store": req.store, "slot_id": req.slot_id,
            "customer_id": req.customer_id, "topic": req.topic}
    APPOINTMENT_HOLDS[hold_id] = hold
    return {**hold, "status": "held"}


class ConfirmSlotRequest(BaseModel):
    hold_id: str
    store: str
    slot_id: str
    customer_id: str
    contact: str
    topic: str = "consultation beauté"


@app.post("/appointments/confirm")
async def confirm_slot(req: ConfirmSlotRequest, idempotency_key: str = Header(...)):
    """Idempotent appointment confirmation: repeating the same Idempotency-Key returns the original booking."""
    await _fault()
    if idempotency_key in APPOINTMENTS:
        return {**APPOINTMENTS[idempotency_key], "duplicate": True}
    hold = APPOINTMENT_HOLDS.get(req.hold_id)
    if hold is None:
        raise HTTPException(404, "unknown or expired hold")
    if hold["customer_id"] != req.customer_id or hold["store"] != req.store or hold["slot_id"] != req.slot_id:
        raise HTTPException(403, "hold ownership or slot mismatch")
    appt_id = "APT-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:8].upper()
    booking = {
        "appointment_id": appt_id,
        "hold_id": req.hold_id,
        "store": req.store,
        "store_name": STORES.get(req.store, req.store),
        "slot_id": req.slot_id,
        "customer_id": req.customer_id,
        "topic": req.topic,
        "status": "confirmed",
        "duplicate": False,
    }
    APPOINTMENTS[idempotency_key] = booking
    APPOINTMENT_HOLDS.pop(req.hold_id, None)
    return booking
