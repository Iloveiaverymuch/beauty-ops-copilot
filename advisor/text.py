"""French text analysis for LEXICAL search (BM25) and for measuring chunk size.

Why this module exists
----------------------
There are two kinds of "tokenization" in this project and they must not be confused:

1. The EMBEDDING model's tokenizer (SentencePiece, inside Vertex AI). We never run it ourselves: we send raw
   text and Vertex tokenizes server-side. We only need to stay under the model's input limit (2,048 tokens
   for gemini-embedding-001); our chunks are ~60 words, far below.
2. The LEXICAL analyzer below, which WE own because BM25 runs in our code. BM25 matches words, so the same
   word must always produce the same token: "Rétinol", "retinol" and "rétinols" -> "retinol".

Pipeline: lowercase -> strip accents (NFKD) -> split on non-letters -> drop French stopwords -> light plural
stemming. This is a deliberately small, dependency-free version of what Elasticsearch's `french` analyzer or a
Snowball stemmer does. Known limits (documented in docs/RAG_DECISIONS.md): no elision handling beyond
"l'"/"d'", no conjugation stemming ("hydrate" vs "hydratant" stay different).
"""
from __future__ import annotations

import re
import unicodedata

# Function words that carry no retrieval signal. Short on purpose: an over-long list removes useful terms
# ("sans" must stay: "sans parfum" is a product attribute).
STOPWORDS = frozenset("""
a au aux avec ce ces cet cette dans de des du elle en est et etre il ils je la le les leur lui ma mais me mes mon
ne nos notre nous on ou par pas pour qu que qui sa se ses son sur ta te tes ton tu un une vos votre vous y
quel quelle quels quelles est-ce peut puis-je comment combien
""".split())

_ELISION = re.compile(r"\b[ldjmnstc]'", re.I)   # l'acide -> acide, d'achat -> achat
_WORD = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)?")  # keeps "0,3" and "2.5" as one token (concentrations matter)


def fold(text: str) -> str:
    """Lowercase + remove accents: 'Rétinol Éclat' -> 'retinol eclat'. NFKD splits 'é' into 'e' + accent mark."""
    text = unicodedata.normalize("NFKD", text.lower().replace("’", "'"))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def stem(token: str) -> str:
    """Light French plural stemming: 'serums' -> 'serum', 'peaux' -> 'peau'. Numbers and short words untouched."""
    if len(token) > 3 and not token[0].isdigit():
        if token.endswith("aux") and len(token) > 4:
            return token[:-1]          # peaux -> peau (not 'pea')
        if token[-1] in "sx":
            return token[:-1]
    return token


def analyze(text: str) -> list[str]:
    """Text -> list of normalized tokens for BM25. Same function at index time and query time (mandatory)."""
    text = _ELISION.sub("", fold(text))
    return [stem(t) for t in _WORD.findall(text) if t not in STOPWORDS]


def word_count(text: str) -> int:
    """Chunk-size measure. Words, not model tokens: French averages ~1.3-1.5 model tokens per word, so a
    220-word cap stays well below any embedding input limit without calling a tokenizer API."""
    return len(text.split())
