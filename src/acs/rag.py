"""Dense retrieval over the Markdown knowledge base.

MVP scope, deliberately minimal:

    Markdown -> section-level chunk -> Qwen3 embedding -> FAISS IndexFlatIP -> top-k

Explicitly NOT here (each is a later phase, and adding one now would mean
tuning it against nothing):
    BM25, hybrid fusion, RRF, reranker, query rewrite, parent-child chunks.

Chunking was revised twice after looking at actual retrieval output, and the
reasoning is recorded because the failure mode is invisible without inspecting
scores:

* v1 packed sections greedily up to `chunk_size`. With `chunk_size=600` that
  produced **15 chunks for 8 documents**, so one chunk held "7-day no-reason
  returns" *and* "15-day quality returns".
* v2 did the same but with an overlap prefix that truncated the buffer, which
  was worse: **1 chunk per document**, i.e. the whole policy in one vector. The
  top hit for "无线耳机退货期限" was the *headphone product page*, because a
  single 2400-character vector's nearest neighbour is dominated by whatever
  topic the document spends most words on.
* v3 (this version) treats a **section as the chunk**. `chunk_size` is now a
  ceiling for oversized sections only, not a packing target. This matches how
  the documents are actually organised -- one heading, one question -- and the
  retrieved text is the passage a human would quote.

Vectors are normalised and the index is inner-product, so the score IS cosine
similarity. Scores are shown in the UI, and a raw L2 distance would be
uninterpretable to anyone reading the trace.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .config import Settings, get_settings

INDEX_FILE = "kb.faiss"
META_FILE = "kb_meta.json"

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Chunk:
    """One retrievable unit, carrying the provenance the tool result must show."""

    chunk_id: str
    document_id: str
    title: str
    category: str
    content: str
    score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["score"] = round(self.score, 4)
        return d


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Minimal `key: value` frontmatter parser.

    Hand-rolled rather than pulling in PyYAML for a flat map of strings. If the
    metadata ever grows nested structures this should switch to a real parser.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, text[match.end() :]


def _split_sections(body: str) -> list[str]:
    """Split into heading-delimited sections, keeping each heading with its body."""
    return [b.strip() for b in re.split(r"\n(?=#{1,3}\s)", body) if b.strip()]


def _split_oversized(section: str, chunk_size: int) -> list[str]:
    """Break an over-long section on paragraph boundaries."""
    if len(section) <= chunk_size:
        return [section]
    parts: list[str] = []
    buffer = ""
    for para in (p.strip() for p in re.split(r"\n\s*\n", section)):
        if not para:
            continue
        if buffer and len(buffer) + len(para) + 2 > chunk_size:
            parts.append(buffer)
            buffer = para
        else:
            buffer = f"{buffer}\n\n{para}" if buffer else para
    if buffer:
        parts.append(buffer)
    return parts


def chunk_text(
    body: str,
    *,
    document_id: str,
    title: str,
    category: str,
    chunk_size: int,
    chunk_overlap: int,  # unused at section level; kept for interface stability
) -> list[Chunk]:
    """One chunk per section; sections are never merged across headings."""
    texts: list[str] = []
    for section in _split_sections(body):
        texts.extend(_split_oversized(section, chunk_size))

    return [
        Chunk(
            chunk_id=f"{document_id}#{i}",
            document_id=document_id,
            title=title,
            category=category,
            content=text.strip(),
        )
        for i, text in enumerate(texts, start=1)
        if text.strip()
    ]


class KnowledgeBase:
    """Loads documents, builds a FAISS index, and answers similarity queries."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        llm: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm
        self._index: Any = None
        self._chunks: list[Chunk] = []
        self._dim: int | None = None

    # ------------------------------------------------------------------ #
    @property
    def llm(self) -> Any:
        """Lazily construct the client so importing this module needs no key."""
        if self._llm is None:
            from .llm import LLMClient

            self._llm = LLMClient(settings=self.settings)
        return self._llm

    # ------------------------------------------------------------------ #
    def load_documents(self) -> list[Chunk]:
        """Read every `.md` under `docs_dir` and return all chunks."""
        docs_dir = self.settings.docs_dir
        if not docs_dir.exists():
            raise FileNotFoundError(f"knowledge docs directory not found: {docs_dir}")

        chunks: list[Chunk] = []
        for path in sorted(docs_dir.glob("*.md")):
            meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            chunks.extend(
                chunk_text(
                    body,
                    document_id=meta.get("document_id", path.stem),
                    title=meta.get("title", path.stem),
                    category=meta.get("category", "unknown"),
                    chunk_size=self.settings.chunk_size,
                    chunk_overlap=self.settings.chunk_overlap,
                )
            )
        if not chunks:
            raise ValueError(f"no chunks produced from {docs_dir}")
        return chunks

    # ------------------------------------------------------------------ #
    def build(self) -> dict[str, Any]:
        """Embed all chunks and persist the index next to its metadata."""
        chunks = self.load_documents()
        # Prefix the title so a chunk that never repeats the product name is
        # still findable by it ("保修期多久" -> warranty_policy sections; but
        # "充电器保修" needs the product context to win the ranking).
        vectors = self.llm.embed(
            [f"{c.title}\n{c.content}" for c in chunks], stage="kb_index"
        )
        matrix = self._normalise(np.asarray(vectors, dtype="float32"))
        self._dim = int(matrix.shape[1])

        import faiss

        index = faiss.IndexFlatIP(self._dim)
        index.add(matrix)

        self.settings.index_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(self.settings.index_dir / INDEX_FILE))
        (self.settings.index_dir / META_FILE).write_text(
            json.dumps(
                {
                    "dim": self._dim,
                    "model": self.settings.embedding_model,
                    "chunk_size": self.settings.chunk_size,
                    "chunk_overlap": self.settings.chunk_overlap,
                    "chunks": [c.as_dict() for c in chunks],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self._index, self._chunks = index, chunks
        return {
            "chunks": len(chunks),
            "dim": self._dim,
            "documents": len({c.document_id for c in chunks}),
        }

    # ------------------------------------------------------------------ #
    def load(self) -> None:
        """Load a previously built index, verifying the embedding model matches.

        The model check matters: loading a 4096-dim index built by one model and
        querying it with another would either raise on shape or, worse, return
        plausible-looking nonsense.
        """
        index_path = self.settings.index_dir / INDEX_FILE
        meta_path = self.settings.index_dir / META_FILE
        if not index_path.exists() or not meta_path.exists():
            raise FileNotFoundError(
                "index not built yet; run: uv run python scripts/build_index.py"
            )

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        built_with = meta.get("model")
        if built_with != self.settings.embedding_model:
            raise RuntimeError(
                f"index was built with {built_with!r} but settings use "
                f"{self.settings.embedding_model!r}; rebuild with scripts/build_index.py"
            )

        import faiss

        self._index = faiss.read_index(str(index_path))
        self._dim = int(meta["dim"])
        self._chunks = [Chunk(**c) for c in meta["chunks"]]

    # ------------------------------------------------------------------ #
    def search(self, query: str, top_k: int | None = None) -> list[Chunk]:
        """Return the top-k most similar chunks. Empty list if nothing to search."""
        if self._index is None:
            self.load()
        k = top_k or self.settings.top_k
        if not self._chunks or not query.strip():
            return []

        vector = self.llm.embed([query], stage="kb_search")
        matrix = self._normalise(np.asarray(vector, dtype="float32"))
        scores, indices = self._index.search(matrix, min(k, len(self._chunks)))

        results: list[Chunk] = []
        for score, idx in zip(scores[0], indices[0], strict=True):
            if idx < 0:
                continue
            chunk = self._chunks[int(idx)]
            results.append(
                Chunk(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    title=chunk.title,
                    category=chunk.category,
                    content=chunk.content,
                    score=float(score),
                )
            )
        return results

    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalise(matrix: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms
