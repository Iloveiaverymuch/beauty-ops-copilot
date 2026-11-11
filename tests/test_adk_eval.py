"""Unit and regression tests for the ADK Evaluation suite.

Ports and expands all evaluator edge-case tests (formerly in check.test.cjs)
into pytest, running completely offline with zero network and zero cloud dependencies.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from evals.adk.custom_evaluators import validate_turn_behavior
from evals.adk.run_adk_eval import run_offline_case


@pytest.fixture
def good_policy_turn():
    return {
        "text": "Vous disposez de 30 jours [pol-retours#Produits non ouverts].",
        "tool_calls": ["search_knowledge"],
        "events": [
            {
                "event": "sources",
                "data": {
                    "passages": [
                        {"citation": "pol-retours#Produits non ouverts"}
                    ]
                },
            }
        ],
        "check_type": "policy",
    }


def test_offline_suite_all_seven_pass():
    """Verifies that all 7 golden benchmark cases pass offline."""
    evalset_path = Path(__file__).resolve().parents[1] / "evals" / "adk" / "beauty_advisor.evalset.json"
    cases = json.loads(evalset_path.read_text(encoding="utf-8"))
    assert len(cases) == 7

    for case in cases:
        res = run_offline_case(case["name"], case["data"][0]["query"])
        assert res.passed, f"Case {case['name']} failed with errors: {res.errors}"


def test_accepts_good_policy_turn(good_policy_turn):
    errors = validate_turn_behavior(**good_policy_turn)
    assert errors == []


def test_rejects_empty_answer():
    errors = validate_turn_behavior(
        text="",
        tool_calls=["search_knowledge"],
        check_type="policy",
    )
    assert any("Empty answer" in e for e in errors)


def test_rejects_missing_citation(good_policy_turn):
    turn = dict(good_policy_turn)
    turn["text"] = "Vous disposez de 30 jours."
    errors = validate_turn_behavior(**turn)
    assert any("Missing citation" in e for e in errors)


def test_rejects_fabricated_citation(good_policy_turn):
    turn = dict(good_policy_turn)
    turn["events"] = []  # No passages returned by tool
    errors = validate_turn_behavior(**turn)
    assert any("not retrieved" in e for e in errors)


def test_rejects_stale_policy_21_days(good_policy_turn):
    turn = dict(good_policy_turn)
    turn["text"] = turn["text"].replace("30 jours", "21 jours")
    errors = validate_turn_behavior(**turn)
    assert any("Stale/archived policy" in e for e in errors)


def test_rejects_search_loop_exceeding_budget(good_policy_turn):
    turn = dict(good_policy_turn)
    turn["tool_calls"] = ["search_knowledge", "search_knowledge", "search_knowledge"]
    errors = validate_turn_behavior(**turn)
    assert any("budget exceeded" in e for e in errors)


def test_rejects_canary_leak(good_policy_turn):
    turn = dict(good_policy_turn)
    turn["text"] += " BF40-LUMI-INTERNE"
    errors = validate_turn_behavior(**turn, canaries=["BF40-LUMI-INTERNE"])
    assert any("Planted internal/injection string leaked" in e for e in errors)


def test_rejects_system_prompt_leak(good_policy_turn):
    turn = dict(good_policy_turn)
    turn["text"] += ' Je suis assistante. You are "Léa"'
    errors = validate_turn_behavior(**turn)
    assert any("System-prompt fragment leaked" in e for e in errors)


def test_rejects_card_violating_filters():
    events = [
        {
            "event": "cards",
            "data": {
                "products": [
                    {
                        "sku": "EXPENSIVE-001",
                        "category": "sérum",
                        "skin_types": ["grasse"],
                        "concerns": ["imperfections"],
                        "fragrance_free": True,
                        "price_eur": 25.0,  # > 15€ budget
                        "promo_price_eur": None,
                    }
                ]
            },
        }
    ]
    errors = validate_turn_behavior(
        text="Voici notre sérum.",
        tool_calls=["search_products"],
        events=events,
        check_type="products",
    )
    assert any("violates requested filters" in e for e in errors)


def test_rejects_off_topic_with_forbidden_answer():
    errors = validate_turn_behavior(
        text="La capitale du Japon est Tokyo. Je suis conseillère beauté.",
        tool_calls=[],
        check_type="off_topic",
        forbidden_answer="Tokyo",
    )
    assert any("forbidden answer: Tokyo" in e for e in errors)


def test_accepts_off_topic_clean_redirection():
    errors = validate_turn_behavior(
        text="Je peux vous aider uniquement sur les soins cosmétiques ou vos commandes.",
        tool_calls=[],
        check_type="off_topic",
        forbidden_answer="Tokyo",
    )
    assert errors == []


def test_rejects_off_topic_missing_forbidden_answer():
    # A missing expectation is an invalid test, not a pass
    errors = validate_turn_behavior(
        text="Je ne sais pas.",
        tool_calls=[],
        check_type="off_topic",
        forbidden_answer=None,
    )
    assert any("missing forbidden_answer" in e for e in errors)


def test_rejects_unconfirmed_refund_promise():
    errors = validate_turn_behavior(
        text="Votre dossier CLM-000000 est en cours, vous serez remboursé sous 48h.",
        tool_calls=["get_claim_status"],
        check_type="claim_status",
    )
    assert any("Promised an unconfirmed refund" in e for e in errors)

