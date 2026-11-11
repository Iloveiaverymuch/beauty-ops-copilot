"""Offline tests (no GCP calls): chunking, analyzer, BM25 and the confidential-data guarantees at ingest."""
import json
from pathlib import Path

from advisor.chunking import MAX_WORDS, chunk_doc, load_kb, product_text, split_long
from advisor.retrieval.bm25 import BM25Index
from advisor.text import analyze

DATA = Path(__file__).resolve().parent.parent / "advisor" / "data"
CANARIES = json.loads((DATA / "canaries.json").read_text())


def test_chunk_ids_deterministic_and_unique():
    a, b = load_kb(DATA / "kb"), load_kb(DATA / "kb")
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]   # re-ingest = same ids = no duplicate rows
    assert len({c.chunk_id for c in a}) == len(a)


def test_every_doc_chunked_with_context_prefix_and_cap():
    chunks = load_kb(DATA / "kb")
    assert len({c.doc_id for c in chunks}) == len(list((DATA / "kb").glob("*.md")))
    for c in chunks:
        assert c.embed_text.startswith(f"{c.title} > {c.section}\n")
        assert len(c.text.split()) <= MAX_WORDS


def test_long_section_split_with_one_sentence_overlap():
    p1 = " ".join(["mot"] * 150) + ". Phrase de liaison importante."
    p2 = " ".join(["autre"] * 150) + "."
    pieces = split_long(f"{p1}\n\n{p2}")
    assert len(pieces) == 2
    assert pieces[1].startswith("Phrase de liaison importante.")   # overlap carries the boundary sentence


def test_analyzer_folds_accents_plurals_elisions_and_keeps_numbers():
    assert analyze("Les Rétinols de l'été") == ["retinol", "ete"]
    assert analyze("Peaux sensibles") == ["peau", "sensible"]
    assert "0,3" in analyze("rétinol 0,3%")
    assert analyze("sans parfum") == ["san", "parfum"]   # "sans" is kept (product attribute), stemmed like any word


def test_bm25_ranks_the_obvious_doc_first():
    chunks = [c for c in load_kb(DATA / "kb") if c.visibility == "public"]
    idx = BM25Index([(c.chunk_id, c.embed_text) for c in chunks])
    by_id = {c.chunk_id: c for c in chunks}
    top = by_id[idx.search("livraison express 24 heures", k=1)[0][0]]
    assert top.doc_id == "pol-livraison" and top.section == "Livraison express"
    top = by_id[idx.search("retourner un produit ouvert", k=1)[0][0]]
    assert top.doc_id == "pol-retours"


def test_internal_docs_flagged_and_canaries_only_in_internal_data():
    chunks = load_kb(DATA / "kb")
    internal = {c.doc_id for c in chunks if c.visibility == "internal"}
    assert internal == {"int-plan-black-friday", "int-consignes-conseillers"}
    public_text = " ".join(c.text for c in chunks if c.visibility == "public")
    for s in CANARIES["internal_docs"]:
        assert s not in public_text, f"canary {s!r} found in a PUBLIC doc"


def test_product_text_never_contains_internal_fields():
    for p in json.loads((DATA / "products.json").read_text()):
        t = product_text(p)
        for s in CANARIES["internal_product_fields"]:
            assert s not in t


def test_catalog_planted_facts():
    p = {r["sku"]: r for r in json.loads((DATA / "products.json").read_text())}
    assert len(p) == 40
    eff = lambda r: r["promo_price_eur"] or r["price_eur"]  # noqa: E731
    hits = sorted(s for s, r in p.items() if r["category"] == "sérum" and "grasse" in r["skin_types"]
                  and "imperfections" in r["concerns"] and r["fragrance_free"] and eff(r) < 15)
    assert hits == ["BOT-SER-003", "LUM-SER-001", "LUM-SER-011"]   # demo prompt "peau grasse, < 15 EUR, sans parfum"


def test_golden_labels_point_to_real_public_sections():
    """A renamed section must break this test, not silently zero the eval."""
    from evals.advisor.golden import GOLDEN, POISONED
    valid = {f"{c.doc_id}#{c.section}" for c in load_kb(DATA / "kb") if c.visibility == "public"}
    assert POISONED in valid
    for g in GOLDEN:
        for e in g["expect"]:
            assert e in valid, (g["id"], e)


def test_advice_index_filter_excludes_internal_and_reviews():
    """Mirror of ingest.ADVICE_INDEX_FILTER applied to the local chunks: what search_knowledge can ever see."""
    from advisor.ingest import ADVICE_INDEX_FILTER
    assert "visibility = 'public'" in ADVICE_INDEX_FILTER and "doc_type != 'avis'" in ADVICE_INDEX_FILTER
    advice = [c for c in load_kb(DATA / "kb") if c.visibility == "public" and c.doc_type != "avis"]
    text = " ".join(c.text for c in advice)
    for group in CANARIES.values():
        for s in group:
            assert s not in text, s     # incl. BIENVENUE100 (injected review)


def test_product_text_contains_sku_and_bm25_finds_it():
    from advisor.retrieval.bm25 import BM25Index
    P = json.loads((DATA / "products.json").read_text())
    idx = BM25Index([(p["sku"], product_text(p)) for p in P])
    for sku in ("LUM-SER-011", "DER-SER-008", "BOT-SPF-005"):
        assert idx.search(sku, 1)[0][0] == sku
