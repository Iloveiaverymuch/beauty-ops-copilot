"""Return/refund policy as CODE (mirrors the published policy pol-retours, current version):
- unopened product: returnable within 30 days of delivery;
- opened product: only for a skin reaction reported within 14 days, with a photo;
- opened, no reaction: not returnable (hygiene).
Refunds up to AUTO_APPROVE_MAX_EUR without a reaction are approved automatically; anything above, and every skin
reaction, goes to a human. The LLM never decides eligibility: it only triages and summarises for the reviewer.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

RETURN_WINDOW_DAYS = 30
REACTION_WINDOW_DAYS = 14
AUTO_APPROVE_MAX_EUR = float(os.getenv("CLAIMS_AUTO_APPROVE_MAX_EUR", "30"))


@dataclass(frozen=True)
class Decision:
    eligible: bool
    route: str          # "auto" | "review" | "rejected" | "needs_info"
    rule: str           # which rule fired: shown to the customer and the reviewer, logged for audit


def evaluate(opened: bool, skin_reaction: bool, photo_provided: bool, days_since_purchase: int, amount_eur: float) -> Decision:
    if opened and not skin_reaction:
        return Decision(False, "rejected", "opened_product_not_returnable")
    if skin_reaction:
        if days_since_purchase > REACTION_WINDOW_DAYS:
            return Decision(False, "rejected", f"reaction_reported_after_{REACTION_WINDOW_DAYS}_days")
        if not photo_provided:
            return Decision(False, "needs_info", "reaction_requires_photo")
        return Decision(True, "review", "skin_reaction_always_reviewed_by_a_human")
    if days_since_purchase > RETURN_WINDOW_DAYS:
        return Decision(False, "rejected", f"unopened_return_after_{RETURN_WINDOW_DAYS}_days")
    if amount_eur > AUTO_APPROVE_MAX_EUR:
        return Decision(True, "review", f"amount_above_{AUTO_APPROVE_MAX_EUR:.0f}_eur")
    return Decision(True, "auto", "unopened_within_window_small_amount")
