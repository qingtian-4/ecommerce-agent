"""RAG: chunking, index build/search, and the model-mismatch guard."""

from __future__ import annotations

import json

import pytest

from acs.rag import KnowledgeBase, chunk_text


# --------------------------------------------------------------------------- #
# chunking
# --------------------------------------------------------------------------- #
def test_chunking_splits_on_sections_not_on_a_size_target() -> None:
    """A section is the unit. Regression guard for the packing bug.

    The first implementation packed sections up to `chunk_size`, which collapsed
    a whole policy document into a single vector and made retrieval useless: the
    top hit for a returns question became the headphone product page. If this
    test fails, chunking has regressed to packing.
    """
    body = (
        "## 一、七天无理由退货\n\n内容甲。\n\n"
        "## 二、质量问题退货\n\n内容乙。\n\n"
        "## 三、不予退货\n\n内容丙。"
    )
    chunks = chunk_text(
        body, document_id="d", title="t", category="policy", chunk_size=700, chunk_overlap=0
    )
    assert len(chunks) == 3, f"expected one chunk per section, got {len(chunks)}"
    assert chunks[0].chunk_id == "d#1"
    assert "七天无理由" in chunks[0].content
    assert "质量问题" not in chunks[0].content


def test_oversized_section_is_split_on_paragraphs() -> None:
    body = "## 标题\n\n" + "\n\n".join(f"段落{i}" + "字" * 100 for i in range(10))
    chunks = chunk_text(
        body, document_id="d", title="t", category="policy", chunk_size=300, chunk_overlap=0
    )
    assert len(chunks) > 1
    assert all(len(c.content) <= 400 for c in chunks), "chunks must respect the ceiling"


def test_frontmatter_is_not_part_of_any_chunk(settings) -> None:
    """Metadata must not leak into retrievable text, or it pollutes matching."""
    kb = KnowledgeBase(settings)
    for chunk in kb.load_documents():
        assert "document_id:" not in chunk.content
        assert "effective_date:" not in chunk.content


def test_every_document_produces_chunks(settings) -> None:
    kb = KnowledgeBase(settings)
    chunks = kb.load_documents()
    documents = {c.document_id for c in chunks}
    assert len(documents) >= 8, f"expected >=8 documents, got {sorted(documents)}"
    # Section-level chunking must give clearly more chunks than documents.
    assert len(chunks) > len(documents)
    for chunk in chunks:
        assert chunk.title and chunk.document_id and chunk.chunk_id


# --------------------------------------------------------------------------- #
# index
# --------------------------------------------------------------------------- #
@pytest.fixture()
def built_kb(settings, fake_embedder):
    kb = KnowledgeBase(settings, llm=fake_embedder)
    kb.build()
    return kb


def test_build_writes_index_and_metadata(settings, built_kb, fake_embedder) -> None:
    assert (settings.index_dir / "kb.faiss").exists()
    meta = json.loads((settings.index_dir / "kb_meta.json").read_text(encoding="utf-8"))
    assert meta["model"] == settings.embedding_model
    assert meta["chunks"], "metadata must record the chunks"
    assert meta["dim"] == fake_embedder.dim


def test_search_returns_top_k_with_normalised_scores(settings, built_kb) -> None:
    results = built_kb.search("退货", top_k=3)
    assert results, "expected at least one hit"
    assert len(results) <= 3
    for chunk in results:
        assert -1.0001 <= chunk.score <= 1.0001, "cosine score must be normalised"
        assert chunk.content


def test_search_scores_are_descending(settings, built_kb) -> None:
    scores = [c.score for c in built_kb.search("保修", top_k=5)]
    assert scores == sorted(scores, reverse=True)


def test_search_on_fresh_index_reloads_from_disk(settings, built_kb) -> None:
    """A second instance must be able to serve queries without rebuilding."""
    reloaded = KnowledgeBase(settings, llm=built_kb._llm)  # noqa: SLF001
    assert reloaded.search("退货") != []


def test_reload_rejects_a_different_embedding_model(settings, built_kb) -> None:
    """A model mismatch must fail loudly rather than return plausible nonsense."""
    mismatched = type(settings)(
        _env_file=None,
        db_path=settings.db_path,
        docs_dir=settings.docs_dir,
        index_dir=settings.index_dir,
        embedding_model="some-other-model",
        embedding_dim=8,
    )
    kb = KnowledgeBase(mismatched, llm=built_kb._llm)  # noqa: SLF001
    with pytest.raises(RuntimeError, match="rebuild"):
        kb.load()


def test_search_without_index_raises_actionable_error(settings, fake_embedder) -> None:
    kb = KnowledgeBase(settings, llm=fake_embedder)
    with pytest.raises(FileNotFoundError, match="build_index"):
        kb.search("退货")


def test_empty_query_returns_nothing(settings, built_kb) -> None:
    assert built_kb.search("   ") == []
