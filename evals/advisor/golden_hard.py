"""HARD set: the query types where the research says lexical search and reranking pay off, and that the base
golden set (clean paraphrases written by the corpus author) does not contain.

Written AFTER the base eval saturated (dense R@5 = 1.00) - on purpose, to discriminate between techniques.
Predictions written BEFORE running (to check, not to tune):
  - exact identifiers / numbers / jargon (sku, brand, number, jargon): BM25 and hybrid >= dense
  - typos and missing accents (typo): dense degrades less than BM25 (subword tokenizer vs exact tokens)
  - English queries on French docs (english): dense >> BM25 (no shared tokens)
  - very short / ambiguous queries (short): rerank improves top-1
"""

# Products: expect = any of these SKUs counts as a hit (several products can legitimately answer).
PRODUCT_HARD = [
    {"id": "pq1", "type": "sku", "q": "LUM-SER-011", "expect": ["LUM-SER-011"]},
    {"id": "pq2", "type": "sku", "q": "DER-SER-008", "expect": ["DER-SER-008"]},
    {"id": "pq3", "type": "brand", "q": "Botanéa bleuet", "expect": ["BOT-NET-003", "BOT-YEU-003"]},
    {"id": "pq4", "type": "brand", "q": "Aqualis céramides", "expect": ["AQU-CRE-002", "AQU-NET-002"]},
    {"id": "pq5", "type": "number", "q": "vitamine C 15", "expect": ["LUM-SER-005"]},
    {"id": "pq6", "type": "number", "q": "retinol 0,5", "expect": ["DER-SER-008"]},
    {"id": "pq7", "type": "typo", "q": "niacinamid serum", "expect": ["LUM-SER-001", "DER-SER-002"]},
    {"id": "pq8", "type": "typo", "q": "acide azelaique", "expect": ["LUM-SER-011"]},
    {"id": "pq9", "type": "colloquial", "q": "un truc contre les points noirs", "expect": ["BOT-SER-003", "DER-NET-004", "NEO-EXF-003", "BOT-MAS-001"]},
    {"id": "pq10", "type": "colloquial", "q": "une crème pour ma peau qui tiraille", "expect": ["AQU-CRE-002", "AQU-MAS-002"]},
    {"id": "pq11", "type": "english", "q": "eye cream for dark circles", "expect": ["AQU-YEU-001", "BOT-YEU-003"]},
    {"id": "pq12", "type": "english", "q": "oil cleanser to remove makeup", "expect": ["NEO-NET-005", "SOL-NET-006"]},
    {"id": "pq13", "type": "short", "q": "stick solaire", "expect": ["BOT-SPF-005"]},
    {"id": "pq14", "type": "short", "q": "teinté anti-taches", "expect": ["LUM-SPF-006"]},
]

# Knowledge base: section-level labels, like the base set.
KB_HARD = [
    {"id": "k1", "type": "short", "q": "PAO ?", "expect": ["faq-conservation#Le symbole PAO"]},
    {"id": "k2", "type": "short", "q": "BHA", "expect": ["ing-aha-bha#AHA ou BHA ?"]},
    {"id": "k3", "type": "jargon", "q": "rétinisation", "expect": ["ing-retinol#Effets indésirables fréquents"]},
    {"id": "k4", "type": "jargon", "q": "cica", "expect": ["ing-apaisants#La centella asiatica"]},
    {"id": "k5", "type": "number", "q": "2 mg par cm²", "expect": ["ing-protection-solaire#Quelle quantité ?"]},
    {"id": "k6", "type": "number", "q": "livraison 13 h", "expect": ["pol-livraison#Livraison express"]},
    {"id": "k7", "type": "typo", "q": "livraizon expresse prix", "expect": ["pol-livraison#Livraison express"]},
    {"id": "k8", "type": "typo", "q": "retinol enceinte", "expect": ["comp-grossesse#Actifs à éviter"]},
    {"id": "k9", "type": "english", "q": "Can I return an opened product?", "expect": ["pol-retours#Produits ouverts"]},
    {"id": "k10", "type": "english", "q": "how often should I exfoliate", "expect": ["ing-aha-bha#Fréquence"]},
    {"id": "k11", "type": "colloquial", "q": "vous livrez le dimanche ?", "expect": ["pol-livraison#Livraison express"]},
    {"id": "k12", "type": "colloquial", "q": "mon bon d'achat il expire quand ?", "expect": ["pol-fidelite#Gagner des points"]},
]
