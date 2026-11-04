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
_ALIASES = {"retinoid": "retinol", "retinoids": "retinol", "vit": "vitamin", "spf": "filters", "hyaluronic": "hyaluronic"}


@lru_cache(maxsize=1)
def _products() -> list[dict]:
    return json.loads((_DATA / "products.json").read_text())


@lru_cache(maxsize=1)
def _campaigns() -> list[dict]:
    return json.loads((_DATA / "campaigns.json").read_text())


def search_products(
    query: str,
    brand: str | None = None,
    category: str | None = None,
    max_price_eur: float | None = None,
    limit: int = 5,
) -> dict:
    """Search the product catalog. ONE call answers the question: put every constraint in the arguments.

    Matches all words of `query` (case-insensitive) against product name, category and ingredients.
    Filters are applied by the tool, exactly; do not re-filter or re-query to verify.

    Args:
        query: Free text such as an ingredient ("niacinamide", "retinol").
        brand: Optional exact brand name ("Garnier", "La Roche-Posay", "CeraVe", "L'Oréal Paris").
        category: Optional exact category: "serum", "cleanser", "moisturizer" or "sunscreen".
        max_price_eur: Optional price ceiling in EUR, inclusive.
        limit: Max items to return (1-20). Keep the default unless the user asks for more.

    Returns:
        {"status": "ok", "count": int, "total_matches": int, "truncated": bool,
         "items": [{"sku", "name", "brand", "category", "price_eur", "ingredients"}],
         "near_miss": {...}  # only when filters excluded everything: mention it to the user
        } or {"status": "error", "error_message": str}.
    """
    if not query or not query.strip():
        return {"status": "error", "error_message": "query must not be empty"}
    limit = max(1, min(int(limit), MAX_LIMIT))
    q = " ".join(_ALIASES.get(t, t) for t in query.lower().split())
    items = []
    for p in _products():
        if brand and p["brand"].lower() != brand.lower():
            continue
        if category and p["category"] != category.lower():
            continue
        if max_price_eur is not None and p["price_eur"] > max_price_eur:
            continue
        haystack = " ".join([p["name"], p["category"], *p["ingredients"]]).lower()
        if all(tok in haystack for tok in q.split()):
            items.append(p)
    result = {"status": "ok", "count": len(items[:limit]), "total_matches": len(items),
              "truncated": len(items) > limit, "items": items[:limit]}
    if not items and (brand or category or max_price_eur is not None):
        # Near-miss hint: exact filters must not hide what exists just outside them.
        relaxed = search_products(query, limit=MAX_LIMIT)["items"]
        if relaxed:
            cheapest = min(relaxed, key=lambda p: p["price_eur"])
            result["near_miss"] = {"matches_without_filters": len(relaxed),
                                   "cheapest": {k: cheapest[k] for k in ("sku", "name", "brand", "price_eur")}}
    return result


def get_campaign(campaign_id: str | None = None, brand: str | None = None, quarter: str | None = None) -> dict:
    """Get marketing campaigns by id, or list them filtered by brand and/or quarter.

    Args:
        campaign_id: Exact id like "CMP-2026-Q4-GAR". If given, other filters are ignored.
        brand: Optional brand name filter.
        quarter: Optional quarter filter in the form "YYYY-QN", e.g. "2026-Q4".
            ALWAYS pass it when the user names a quarter. If the year is not given, assume the current
            year (2026) and say so in the answer.

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
