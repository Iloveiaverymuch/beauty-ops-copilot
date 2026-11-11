"""Offline tests for the deterministic parts of the advisor tools (no GCP)."""
from advisor.retrieval.modes import rrf
from advisor.tools import card, differences, estimate

SERUM = {"sku": "LUM-SER-001", "name": "Sérum Niacinamide", "brand": "Lumiderm", "category": "sérum",
         "price_eur": 12.90, "promo_price_eur": 9.90, "size_ml": 30, "usage_ml_per_day": 0.5,
         "routine_moment": "matin et soir", "skin_types": ["grasse", "mixte"], "concerns": ["imperfections"],
         "fragrance_free": True, "actives": [{"name": "niacinamide", "pct": 10}], "image": "x"}


def test_estimate_three_months_rounds_up_and_applies_promo():
    e = estimate(SERUM, 90)                 # 90 d x 0.5 ml = 45 ml -> 2 x 30 ml
    assert (e["units"], e["total_eur"], e["savings_eur"], e["days_per_unit"]) == (2, 19.8, 6.0, 60)


def test_estimate_exact_multiple_does_not_add_a_unit():
    assert estimate(SERUM, 60)["units"] == 1   # 30 ml exactly: float noise must not round up to 2


def test_rrf_rewards_agreement_over_a_single_first_place():
    fused = [d for d, _ in rrf([["a", "b", "c"], ["b", "c", "a"]])]
    assert fused[0] == "b"                     # 2nd + 1st beats 1st + 3rd


def test_differences_only_lists_attributes_that_differ_in_priority_order():
    other = {**SERUM, "sku": "DER-SER-002", "brand": "Dermapure", "price_eur": 11.50, "promo_price_eur": None,
             "fragrance_free": False}
    diff = differences([card(SERUM), card(other)])
    attrs = [d["attribute"] for d in diff]
    assert attrs[:2] == ["price_paid_eur", "price_per_100ml"] and "fragrance_free" in attrs and "brand" in attrs
    assert "actives" not in attrs and "skin_types" not in attrs   # identical -> not a difference
