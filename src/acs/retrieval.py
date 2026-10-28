"""Chinese lexical retrieval and rank fusion over the existing section chunks."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

import jieba
from rank_bm25 import BM25Okapi

if TYPE_CHECKING:
    from .rag import Chunk

_TOKENS = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*|[\u4e00-\u9fff]+", re.IGNORECASE)
TOKENIZER_VERSION = 1
RRF_K = 60


def tokenize(text: str) -> list[str]:
    """Segment Chinese; keep identifiers, ports and decimal units intact."""
    words = []
    # Normalise `37 Wh` and `37Wh` consistently without joining unrelated words.
    text = re.sub(r"(\d)\s+(mAh|Wh|W|V|mm|ms|g)\b", r"\1\2", text, flags=re.IGNORECASE)
    for token in _TOKENS.findall(text.lower()):
        if "\u4e00" <= token[0] <= "\u9fff":
            words.extend(jieba.lcut_for_search(token, HMM=False))
        else:
            words.append(token)
    return words


class LexicalIndex:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self.index = BM25Okapi([tokenize(f"{c.title}\n{c.content}") for c in chunks])

    def search(self, query: str, k: int, *, document_id: str | None = None) -> list[Chunk]:
        scores = self.index.get_scores(tokenize(query))
        eligible = [i for i in range(len(scores))
                    if document_id is None or self.chunks[i].document_id == document_id]
        indices = sorted(eligible, key=lambda i: (-scores[i], self.chunks[i].chunk_id))
        return [
            replace(self.chunks[i], score=float(scores[i]), score_type="bm25")
            for i in indices[:k] if scores[i] > 0
        ]


def reciprocal_rank_fusion(dense: list[Chunk], lexical: list[Chunk]) -> list[Chunk]:
    """Fuse ranks, deduplicate by chunk ID, and retain each channel's evidence."""
    chunks = {}
    provenance: dict[str, dict] = {}
    scores: dict[str, float] = {}
    for channel, ranked in (("dense", dense), ("bm25", lexical)):
        seen = set()
        for rank, chunk in enumerate(ranked, start=1):
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            chunks[chunk.chunk_id] = chunk
            provenance.setdefault(chunk.chunk_id, {})[channel] = {
                "rank": rank, "score": chunk.score,
            }
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1 / (RRF_K + rank)
    ids = sorted(scores, key=lambda key: (-scores[key], key))
    return [replace(chunks[key], score=scores[key], score_type="rrf",
                    retrieval=provenance[key]) for key in ids]
