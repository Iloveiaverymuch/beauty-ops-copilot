"""Business tools for the catalog agent.

Contract for every tool (the model reads it, so keep it stable):
  success -> {"status": "ok", ...payload}
  failure -> {"status": "error", "error_message": "..."}   # never raise: the model can recover from text, not from a stack trace
The docstring + type hints ARE the function-calling schema ADK sends to Gemini.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data"
MAX_LIMIT = 20


@lru_cache(maxsize=1)
def _products() -> list[dict]:
    return json.loads((_DATA / "products.json").read_text())


@lru_cache(maxsize=1)
def _campaigns() -> list[dict]:
    return json.loads((_DATA / "campaigns.json").read_text())


def search_products(query: str, brand: str | None = None, limit: int = 5) -> dict:
    """Search the product catalog.

    Matches `query` (case-insensitive) against product name, category and ingredients.
    Use `brand` to restrict to one brand (e.g. "Garnier", "La Roche-Posay", "CeraVe").

    Args:
        query: Free text such as an ingredient ("niacinamide") or a category ("serum").
        brand: Optional exact brand name.
        limit: Max number of items to return (1-20).

    Returns:
        {"status": "ok", "count": int, "items": [{"sku", "name", "brand", "category", "price_eur", "ingredients"}]}
        or {"status": "error", "error_message": str}.
    """
    if not query or not query.strip():
        return {"status": "error", "error_message": "query must not be empty"}
    limit = max(1, min(int(limit), MAX_LIMIT))
    q = query.lower().strip()
    items = []
    for p in _products():
        if brand and p["brand"].lower() != brand.lower():
            continue
        haystack = " ".join([p["name"], p["category"], *p["ingredients"]]).lower()
        if all(tok in haystack for tok in q.split()):
            items.append(p)
    return {"status": "ok", "count": len(items[:limit]), "items": items[:limit]}


def get_campaign(campaign_id: str | None = None, brand: str | None = None, quarter: str | None = None) -> dict:
    """Get marketing campaigns by id, or list them filtered by brand and/or quarter.

    Args:
        campaign_id: Exact id like "CMP-2026-Q4-GAR". If given, other filters are ignored.
        brand: Optional brand name filter.
        quarter: Optional quarter filter in the form "YYYY-QN", e.g. "2026-Q4".

    Returns:
        {"status": "ok", "count": int, "campaigns": [{"id","name","brand","quarter","channels","budget_eur","hero_skus","status","owner"}]}
        or {"status": "error", "error_message": str}.
    """
    camps = _campaigns()
    if campaign_id:
        hit = [c for c in camps if c["id"] == campaign_id]
        if not hit:
            return {"status": "error", "error_message": f"campaign {campaign_id} not found"}
        return {"status": "ok", "count": 1, "campaigns": hit}
    if not brand and not quarter:
        return {"status": "error", "error_message": "give campaign_id, brand or quarter"}
    out = [c for c in camps
           if (not brand or c["brand"].lower() == brand.lower())
           and (not quarter or c["quarter"] == quarter)]
    return {"status": "ok", "count": len(out), "campaigns": out}
