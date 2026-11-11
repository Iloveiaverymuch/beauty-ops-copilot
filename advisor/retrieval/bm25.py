"""BM25 lexical ranking, implemented in our code (~40 lines, no dependency).

Why in Python and not in BigQuery: BigQuery's SEARCH() function only tells you IF a row matches (a filter);
it returns no relevance score, so it cannot rank. BM25 needs term statistics, which we compute here over the
customer-visible chunks loaded from BigQuery. At 59 chunks this is microseconds; at millions of documents
you move lexical ranking to an engine that does it natively (Vertex AI Search, Elasticsearch/OpenSearch, or
the sparse half of a hybrid Vertex AI Vector Search index) - see docs/RAG_DECISIONS.md.

Formula, for query terms t and document d:
    score(d) = sum_t  IDF(t) * tf(t,d) * (k1 + 1) / (tf(t,d) + k1 * (1 - b + b * |d| / avgdl))
    IDF(t)   = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))        (Lucene's variant: never negative)
k1 = 1.5 controls term-frequency saturation (the 5th "rétinol" adds little); b = 0.75 normalizes by length
(a long doc mentioning a term once is weaker evidence than a short one).
"""
from __future__ import annotations

import math
from collections import Counter

from advisor.text import analyze


class BM25Index:
    def __init__(self, docs: list[tuple[str, str]], k1: float = 1.5, b: float = 0.75):
        """docs = [(id, text)]. Text goes through the SAME analyzer as queries (advisor.text.analyze)."""
        self.k1, self.b = k1, b
        self.ids = [i for i, _ in docs]
        self.tfs = [Counter(analyze(t)) for _, t in docs]                   # term frequencies per doc
        self.lens = [sum(tf.values()) for tf in self.tfs]
        self.avgdl = sum(self.lens) / max(len(self.lens), 1)
        df = Counter(term for tf in self.tfs for term in tf)                # document frequency per term
        n = len(docs)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        terms = analyze(query)
        scored = []
        for doc_id, tf, dl in zip(self.ids, self.tfs, self.lens):
            s = 0.0
            for t in terms:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if s > 0:
                scored.append((doc_id, s))
        return sorted(scored, key=lambda x: -x[1])[:k]
