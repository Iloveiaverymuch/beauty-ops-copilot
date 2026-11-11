"""Turn neutral engine events into what the chat UI renders. Pure functions: unit-tested, engine-agnostic.

Design rule: PRODUCT CARDS, prices, stock and dates are rendered from TOOL RESULTS, never parsed from the
model's prose. The model writes the sentence around them; it cannot put a wrong price on screen.
"""
from __future__ import annotations

TOOL_LABELS = {  # progress line shown while a tool runs ("Recherche dans le catalogue...")
    "search_products": "Recherche dans le catalogue", "compare_products": "Comparaison des produits",
    "estimate_quantity": "Calcul des quantités", "search_knowledge": "Recherche dans nos guides",
    "check_availability": "Vérification du stock en magasin", "delivery_options": "Calcul des options de livraison",
    "request_human_advisor": "Transfert vers un conseiller",
    "open_return_claim": "Ouverture du dossier de retour", "get_claim_status": "Recherche du dossier",
}


def ui_event_for_tool_result(name: str, resp: dict) -> dict | None:
    status = resp.get("status")
    if name == "search_products" and status == "ok" and resp.get("products"):
        return {"event": "cards", "data": {"products": resp["products"]}}
    if name == "compare_products" and status == "ok":
        return {"event": "comparison", "data": {"products": resp["products"], "differences": resp["differences"]}}
    if name == "estimate_quantity" and status == "ok":
        return {"event": "estimate", "data": resp}
    if name in ("check_availability", "delivery_options"):
        return {"event": "availability" if name == "check_availability" else "delivery", "data": resp}
    if name == "search_knowledge":
        return {"event": "sources", "data": {"status": status, "passages": [
            {"citation": p["citation"], "title": p["title"], "section": p["section"]} for p in resp.get("passages", [])]}}
    if name in ("open_return_claim", "get_claim_status") and resp.get("claim_id"):
        return {"event": "claim", "data": resp}
    if name == "request_human_advisor" and status == "ok":
        return {"event": "handoff", "data": {"ticket_id": resp["ticket_id"], "message": resp["message"]}}
    return None


def suggestions(tool_results: list[tuple[str, dict]], has_products_in_thread: bool) -> list[str]:
    """Next-best-action chips, decided by the backend from what just happened (deterministic, testable)."""
    last = tool_results[-1] if tool_results else (None, {})
    name, resp = last
    if name == "search_products" and resp.get("products"):
        return ["Comparer les deux premiers", "Disponible à Lyon Part-Dieu ?", "Livraison à Nanterre (92000) ?"]
    if name == "compare_products":
        return ["Pour 3 mois avec le premier, combien ?", "Disponible à Paris Rivoli ?"]
    if name == "estimate_quantity":
        return ["Livraison à Nanterre (92000) ?", "Je peux l'utiliser avec mon rétinol ?"]
    if name == "search_knowledge" and resp.get("status") == "no_answer":
        return ["Parler à un conseiller"]
    if name in ("check_availability", "delivery_options") and resp.get("status") == "unavailable":
        return ["Réessayer", "Parler à un conseiller"]
    if not tool_results and not has_products_in_thread:
        return ["Peau grasse", "Peau sèche", "Peau sensible"]
    return []
