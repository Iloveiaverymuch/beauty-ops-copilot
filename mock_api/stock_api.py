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
_ORDERS = {  # order_id: (days_ago, [(sku, qty, unit_price_paid)])
    "CMD-1001": (5, [("LUM-SER-001", 1, 9.90)]),
    "CMD-1002": (10, [("DER-SER-008", 2, 19.90)]),
    "CMD-1003": (40, [("AQU-CRE-002", 1, 15.90)]),
    "CMD-1004": (3, [("SOL-SPF-002", 1, 14.90)]),
    "CMD-1005": (12, [("AQU-CRE-007", 1, 29.90), ("DER-SER-008", 1, 19.90)]),
    "CMD-12345": (5, [("DER-SER-008", 2, 19.90)]),
}
REFUNDS: dict[str, dict] = {}   # idempotency_key -> refund (in memory: the demo's "payment system")


@app.get("/orders/{order_id}")
async def order(order_id: str):
    await _fault()
    if order_id not in _ORDERS:
        raise HTTPException(404, "unknown order")
    days, lines = _ORDERS[order_id]
    return {"order_id": order_id, "purchase_date": (date.today() - timedelta(days=days)).isoformat(),
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
