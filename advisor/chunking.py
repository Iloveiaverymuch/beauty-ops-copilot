"""Turn knowledge-base Markdown files into retrieval chunks.

How it fits: ingest.py calls `load_kb()` -> list[Chunk] -> embeds `Chunk.embed_text` -> stores in BigQuery.
At query time (R2) BM25 and vector search both return chunk_ids; answers cite `doc_id` + `section`.

Strategy (justified in docs/RAG_DECISIONS.md):
- STRUCTURE-AWARE: one chunk per `## ` section. Our docs are written as "one question / one rule per section",
  so a section is already a self-contained unit of meaning. A fixed sliding window would cut a rule in half.
- CAP of MAX_WORDS: a section longer than the cap is split on paragraph boundaries, and the next piece starts
  with the LAST SENTENCE of the previous one (OVERLAP_SENTENCES). Overlap only exists where we cut inside a
  section; between sections there is nothing to bridge, so no overlap there.
- CONTEXTUAL PREFIX: "Title > Section" is prepended to the text that gets EMBEDDED (not to the text we show).
  A bare chunk like "Oui, mais pas au même moment." means nothing on its own; with its title it becomes
  findable by "rétinol vitamine C".
- DETERMINISTIC IDS: chunk_id = sha256(doc_id, section, text). Re-running ingest produces the same ids, so a
  re-run can never duplicate rows, and an unchanged chunk keeps its cached embedding.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from advisor.text import word_count

MAX_WORDS = 220          # ~300 model tokens: precise enough to cite, big enough to hold one complete rule
OVERLAP_SENTENCES = 1    # carried over only when a section is split


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    title: str
    doc_type: str        # guide | compatibilite | routine | faq | politique | avis | interne
    visibility: str      # public | internal  -> internal chunks never reach the customer-facing index
    section: str
    position: int        # order inside the document (to re-assemble neighbours if ever needed)
    text: str            # what we SHOW and cite

    @property
    def embed_text(self) -> str:
        """What we EMBED: context prefix + text (see module docstring)."""
        return f"{self.title} > {self.section}\n{self.text}"


def _sha(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]   # \x1f = unit separator, avoids "a|b"+"c" == "a"+"b|c"


def parse_doc(raw: str) -> tuple[dict, str]:
    """Split YAML-like front matter (doc_id, title, type, visibility) from the Markdown body."""
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if not m:
        raise ValueError("missing front matter")
    meta = dict(line.split(": ", 1) for line in m.group(1).splitlines())
    return meta, m.group(2).strip()


def _last_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return parts[-1] if parts else ""


def split_long(text: str, max_words: int = MAX_WORDS) -> list[str]:
    """Split an over-long section on paragraph boundaries, with a one-sentence overlap between pieces."""
    if word_count(text) <= max_words:
        return [text]
    pieces, cur = [], []
    for para in text.split("\n\n"):
        if cur and word_count(" ".join(cur + [para])) > max_words:
            pieces.append("\n\n".join(cur))
            cur = [_last_sentence(pieces[-1])] if OVERLAP_SENTENCES else []
        cur.append(para)
    if cur:
        pieces.append("\n\n".join(cur))
    return pieces


def chunk_doc(raw: str) -> list[Chunk]:
    meta, body = parse_doc(raw)
    chunks, pos = [], 0
    for block in re.split(r"^## ", body, flags=re.M):      # each block = "Section title\nsection text"
        block = block.strip()
        if not block:
            continue
        section, _, text = block.partition("\n")
        for piece in split_long(text.strip()):
            chunks.append(Chunk(_sha(meta["doc_id"], section, piece), meta["doc_id"], meta["title"], meta["type"],
                                meta.get("visibility", "public"), section.strip(), pos, piece))
            pos += 1
    return chunks


def load_kb(kb_dir: Path) -> list[Chunk]:
    return [c for p in sorted(kb_dir.glob("*.md")) for c in chunk_doc(p.read_text())]


def product_text(p: dict) -> str:
    """The text that represents a product for retrieval (embedded AND BM25-indexed).

    Built ONLY from customer-visible fields: cost, supplier and internal notes are deliberately excluded, so
    they cannot leak through search results even if the model is tricked into "showing everything it found".
    """
    actives = ", ".join(f"{a['name']} {a['pct']}%" if a["pct"] is not None else a["name"] for a in p["actives"])
    return (f"{p['name']} ({p['brand']}). Catégorie : {p['category']}. Préoccupations : {', '.join(p['concerns']) or 'toutes'}. "
            f"Types de peau : {', '.join(p['skin_types'])}. Actifs : {actives or 'aucun'}. "
            f"{'Sans parfum' if p['fragrance_free'] else 'Parfumé'}. Moment : {p['routine_moment']}. {p['description']}")
