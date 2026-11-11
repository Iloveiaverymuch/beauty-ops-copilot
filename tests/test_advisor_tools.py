"""Offline tests for the deterministic parts of the advisor tools (no GCP)."""
from advisor.retrieval.modes import rrf
from advisor.tools import card, differences, estimate

SERUM = {"sku": "LUM-SER-001", "name": "Sérum Niacinamide", "brand": "Lumiderm", "category": "sérum",
         "price_eur": 12.90, "promo_price_eur": 9.90, "size_ml": 30, "usage_ml_per_day": 0.5,
         "routine_moment": "matin et soir", "skin_types": ["grasse", "mixte"], "concerns": ["imperfections"],
         "fragrance_free": True, "actives": [{"name": "niacinamide", "pct": 10}], "image": "x",
         "description": "Réduit les imperfections et resserre les pores."}


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


def test_store_lazy_init_does_not_deadlock(monkeypatch):
    """Regression: public_chunks() held a non-reentrant lock and called bq(), which took the same lock -> hang."""
    import threading

    import advisor.retrieval.store as st

    class Fake:
        def query(self, *a, **k):
            return type("R", (), {"result": lambda self: []})()

    monkeypatch.setattr(st.bigquery, "Client", lambda **k: Fake())
    monkeypatch.setattr(st, "_bq", None)
    monkeypatch.setattr(st, "_chunks", None)
    monkeypatch.setattr(st, "_products", None)
    done = threading.Event()
    threading.Thread(target=lambda: (st.public_chunks(), st.public_products(), done.set()), daemon=True).start()
    assert done.wait(3), "deadlock in store lazy init"


def test_memory_search_matches_cosine_order(monkeypatch):
    import advisor.retrieval.store as st
    monkeypatch.setattr(st, "_vectors", [("a", [1.0, 0.0]), ("b", [0.6, 0.8]), ("c", [0.0, 1.0])])
    assert [cid for cid, _ in st.memory_search_chunks([0.8, 0.6], k=3)] == ["b", "a", "c"]


def test_adk_adapter_caps_knowledge_searches_per_turn(monkeypatch):
    """Regression: 7 search_knowledge calls in one turn on an uncovered question. Cap = 2, then a stop message."""
    import beauty_advisor.agent as ag

    monkeypatch.setattr(ag.core, "search_knowledge", lambda q: {"status": "ok", "passages": []})
    ctx = type("Ctx", (), {"state": {}})()
    statuses = [ag.search_knowledge("q", ctx)["status"] for _ in range(4)]
    assert statuses == ["ok", "ok", "budget_exhausted", "budget_exhausted"]


def test_tool_removed_from_model_request_once_budget_spent():
    """The soft 'budget_exhausted' message can be ignored by the model; removing the declaration can't."""
    from google.adk.models.llm_request import LlmRequest
    from google.adk.tools import FunctionTool

    import beauty_advisor.agent as ag

    req = LlmRequest()
    req.append_tools([FunctionTool(ag.search_knowledge), FunctionTool(ag.search_products)])
    ctx = type("Ctx", (), {"state": {"temp:kb_searches": ag.MAX_KB_SEARCHES_PER_TURN}})()
    ag.drop_exhausted_tools(ctx, req)
    names = [d.name for t in req.config.tools for d in (t.function_declarations or [])]
    assert names == ["search_products"] and "search_knowledge" not in req.tools_dict
    ctx.state["temp:kb_searches"] = 0
    req2 = LlmRequest(); req2.append_tools([FunctionTool(ag.search_knowledge)])
    ag.drop_exhausted_tools(ctx, req2)
    assert "search_knowledge" in req2.tools_dict          # under budget: untouched


def test_wilson_and_mcnemar():
    from evals.advisor.stats import mcnemar, wilson
    lo, hi = wilson(24, 25)
    assert 0.79 < lo < 0.81 and 0.99 < hi <= 1.0
    assert mcnemar([True] * 6 + [False] * 4, [False] * 6 + [False] * 4)[:2] == (6, 0)
    assert round(mcnemar([True] * 6 + [False] * 4, [False] * 10)[2], 3) == 0.031


def test_hard_set_labels_are_real():
    import json
    from pathlib import Path

    from advisor.chunking import load_kb
    from evals.advisor.golden_hard import KB_HARD, PRODUCT_HARD
    data = Path(__file__).resolve().parent.parent / "advisor" / "data"
    skus = {p["sku"] for p in json.loads((data / "products.json").read_text())}
    sections = {f"{c.doc_id}#{c.section}" for c in load_kb(data / "kb") if c.visibility == "public" and c.doc_type != "avis"}
    assert all(e in skus for g in PRODUCT_HARD for e in g["expect"])
    assert all(e in sections for g in KB_HARD for e in g["expect"])


def test_compact_history_shrinks_old_tool_results_only_and_never_mutates_session():
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

    import beauty_advisor.agent as ag

    big = {"status": "ok", "products": [{"sku": f"S{i}", "name": f"P{i}", "description": "x" * 300} for i in range(3)]}
    old_fr = types.Part(function_response=types.FunctionResponse(id="1", name="search_products", response=big))
    new_fr = types.Part(function_response=types.FunctionResponse(id="2", name="search_products", response=big))
    req = LlmRequest(contents=[
        types.Content(role="user", parts=[types.Part(text="sérum")]),
        types.Content(role="user", parts=[old_fr]),
        types.Content(role="model", parts=[types.Part(text="voici")]),
        types.Content(role="user", parts=[types.Part(text="et le deuxième ?")]),
        types.Content(role="user", parts=[new_fr]),
    ])
    saved = ag.compact_history(req)
    assert saved > 800
    assert req.contents[1].parts[0].function_response.response == {
        "status": "ok", "products": [{"sku": "S0", "name": "P0"}, {"sku": "S1", "name": "P1"}, {"sku": "S2", "name": "P2"}]}
    assert req.contents[4].parts[0].function_response.response == big          # current turn: full result
    assert old_fr.function_response.response == big                            # stored event object untouched


def test_assemble_context_expands_split_sections_dedups_and_respects_budget():
    from advisor.tools import assemble_context
    corpus = [{"chunk_id": "a1", "doc_id": "d", "section": "S", "position": 0, "text": "Règle partie un."},
              {"chunk_id": "a2", "doc_id": "d", "section": "S", "position": 1, "text": "Sauf exception partie deux."},
              {"chunk_id": "b", "doc_id": "e", "section": "T", "position": 0, "text": " ".join(["mot"] * 50)},
              {"chunk_id": "c", "doc_id": "f", "section": "U", "position": 0, "text": "court"}]
    hit = lambda cid: {**next(c for c in corpus if c["chunk_id"] == cid)}  # noqa: E731
    out = assemble_context([hit("a2"), hit("a1"), hit("b"), hit("c")], corpus, budget_words=20)
    assert out[0]["text"] == "Règle partie un.\n\nSauf exception partie deux." and out[0]["expanded"]
    assert [o["chunk_id"] for o in out] == ["a2", "c"]          # a1 deduped (same section), b over budget, c fits
    assert assemble_context([hit("b")], corpus, budget_words=5)[0]["chunk_id"] == "b"   # top passage always kept
