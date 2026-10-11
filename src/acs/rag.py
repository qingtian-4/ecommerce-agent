"""Section retrieval over Markdown: dense, BM25, or RRF hybrid.

All modes share the same chunks. Hybrid candidates are verified against their
text; the existing cosine calibration applies only to dense scores.

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

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from .config import Settings, get_settings

INDEX_FILE = "kb.faiss"
META_FILE = "kb_meta.json"
CHUNKER_VERSION = 5

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
    score_type: str = "cosine"
    retrieval: dict[str, Any] = field(default_factory=dict)

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
        if re.sub(r"(?m)^#{1,6}\s.*$", "", text).strip()
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
        self._fingerprint: str | None = None
        self._min_score: float | None = None
        self._uncertain_until: float | None = None
        self._lexical: Any = None

    def _current_fingerprint(self) -> str:
        source = {
            "chunker_version": CHUNKER_VERSION,
            "chunk_size": self.settings.chunk_size,
            "chunk_overlap": self.settings.chunk_overlap,
            "documents": [
                (p.name, hashlib.sha256(p.read_bytes()).hexdigest())
                for p in sorted(self.settings.docs_dir.glob("*.md"))
            ],
        }
        return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()

    def _load_relevance_calibration(self) -> None:
        path = self.settings.relevance_calibration_path
        if path is None:
            self._min_score = None
            self._uncertain_until = None
            return
        calibration = json.loads(path.read_text())
        knowledge_hash = hashlib.sha256(b"".join(
            p.name.encode() + b"\0" + p.read_bytes()
            for p in sorted(self.settings.docs_dir.glob("*.md"))
        )).hexdigest()
        if (calibration["model"] != self.settings.embedding_model
                or calibration["knowledge_sha256"] != knowledge_hash
                or calibration["chunker_version"] != CHUNKER_VERSION):
            raise RuntimeError(
                "相关性标定与当前模型、知识库或切分版本不匹配；请重新采样标定，不能沿用旧阈值"
            )
        threshold = float(calibration["threshold"])
        upper = float(calibration.get("uncertain_until", threshold))
        if not -1 <= threshold <= upper <= 1:
            raise ValueError("相关性边界必须满足 -1 <= 下界 <= 上界 <= 1")
        self._min_score = threshold
        self._uncertain_until = upper

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
            title = meta.get("title", path.stem)
            if meta.get("product_id"):
                title = f"{title}（{meta['product_id']}）"
            chunks.extend(
                chunk_text(
                    body,
                    document_id=meta.get("document_id", path.stem),
                    title=title,
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
        fingerprint = self._current_fingerprint()
        chunks = self.load_documents()
        self._load_relevance_calibration()
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

        if self._current_fingerprint() != fingerprint:
            raise RuntimeError("索引构建期间源文档变更，请重新运行 scripts/build_index.py")

        self.settings.index_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(self.settings.index_dir / INDEX_FILE))
        (self.settings.index_dir / META_FILE).write_text(
            json.dumps(
                {
                    "dim": self._dim,
                    "model": self.settings.embedding_model,
                    "chunk_size": self.settings.chunk_size,
                    "chunk_overlap": self.settings.chunk_overlap,
                    "fingerprint": fingerprint,
                    "chunker_version": CHUNKER_VERSION,
                    "relevance_min_score": self._min_score,
                    "relevance_uncertain_until": self._uncertain_until,
                    "chunks": [c.as_dict() for c in chunks],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self._index, self._chunks = index, chunks
        self._lexical = None
        self._fingerprint = fingerprint
        return {
            "chunks": len(chunks),
            "dim": self._dim,
            "documents": len({c.document_id for c in chunks}),
        }

    # ------------------------------------------------------------------ #
    def load(self) -> None:
        """Load an index only when its model, documents and chunking still match.

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
        fingerprint = self._current_fingerprint()
        if meta.get("fingerprint") != fingerprint:
            raise RuntimeError(
                "知识索引过期：文档或切分配置已变更；rebuild with scripts/build_index.py"
            )
        self._load_relevance_calibration()

        import faiss

        self._index = faiss.read_index(str(index_path))
        self._dim = int(meta["dim"])
        self._chunks = [Chunk(**c) for c in meta["chunks"]]
        self._lexical = None
        self._fingerprint = fingerprint

    # ------------------------------------------------------------------ #
    def search(self, query: str, top_k: int | None = None) -> list[Chunk]:
        """Return at most top-k verified pieces of evidence."""
        k = top_k or self.settings.top_k
        results = self.retrieve(query, top_k=k)
        return self._filter_evidence(query, results)

    def retrieve(
        self, query: str, *, mode: str | None = None, top_k: int | None = None,
        query_vector: list[float] | None = None,
    ) -> list[Chunk]:
        """Rank candidates before rejection; a frozen vector supports paired evaluation."""
        if self._index is None:
            self.load()
        elif self._fingerprint != self._current_fingerprint():
            raise RuntimeError("知识索引过期：源文档已变更；rebuild with scripts/build_index.py")
        mode = mode or self.settings.retrieval_mode
        if mode not in {"dense", "bm25", "hybrid"}:
            raise ValueError(f"unknown retrieval mode: {mode}")
        k = top_k or self.settings.top_k
        if not self._chunks or not query.strip():
            return []

        from .retrieval import LexicalIndex, reciprocal_rank_fusion

        candidate_k = max(k, self.settings.retrieval_candidate_k) if mode == "hybrid" else k
        lexical = []
        if mode != "dense":
            if self._lexical is None:
                self._lexical = LexicalIndex(self._chunks)
            lexical = self._lexical.search(query, candidate_k)
        if mode == "bm25":
            return lexical

        vector = [query_vector] if query_vector is not None else self.llm.embed(
            [query], stage="kb_search",
        )
        matrix = self._normalise(np.asarray(vector, dtype="float32"))
        scores, indices = self._index.search(matrix, min(candidate_k, len(self._chunks)))

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
        if mode == "hybrid":
            return reciprocal_rank_fusion(results, lexical)[:k]
        return results

    def _filter_evidence(self, query: str, results: list[Chunk]) -> list[Chunk]:
        if self._min_score is None or not results:
            return results
        if results[0].score_type != "cosine":
            # RRF and BM25 scores cannot use the measured cosine boundaries.
            return self._verify_evidence(query, results)
        if results[0].score < self._min_score:
            return []
        if results[0].score <= self._uncertain_until:
            return self._verify_evidence(query, results)
        return [c for c in results if c.score >= self._min_score]

    def _verify_evidence(self, query: str, candidates: list[Chunk]) -> list[Chunk]:
        """Resolve the measured overlap in scores using the actual candidate text."""
        response = self.llm.complete(
            json.dumps({"question": query, "candidates": [
                {"index": i, "title": c.title, "content": c.content}
                for i, c in enumerate(candidates)
            ]}, ensure_ascii=False),
            system=(
                "判断给定知识片段能否直接支撑问题的具体事实或规则。"
                "只根据提供的正文，不用常识补全，不把同一商品、相似主题当作答案依据。"
                "商品编号须与片段标题对应；未提供的事实不可推断。"
                '只输出 JSON {"indices": [0]}，索引从0开始；没有依据则输出 {"indices": []}。'
            ),
            stage="kb_relevance", temperature=0.0, thinking=False,
            max_tokens=128, response_format_json=True,
        )
        indices = json.loads(response.text).get("indices")
        if not isinstance(indices, list) or any(
            type(i) is not int or not 0 <= i < len(candidates) for i in indices
        ):
            raise ValueError("相关性核验未返回合法的候选索引")
        return [chunk for i, chunk in enumerate(candidates) if i in indices]

    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalise(matrix: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms
