"""Mock retail API (stock per store + delivery options): the "external API" the chatbot integrates with.

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

from fastapi import FastAPI, HTTPException, Query

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
