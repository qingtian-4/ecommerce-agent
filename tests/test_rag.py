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


def test_every_product_section_keeps_its_identifier_in_the_embedded_title(settings):
    chunks = KnowledgeBase(settings).load_documents()
    for document, sku in [("headphone_a", "P001"), ("headphone_b", "P002"),
                          ("charger_a", "P003"), ("power_bank_a", "P004")]:
        sections = [c for c in chunks if c.document_id == document]
        assert sections and all(sku in c.title for c in sections)


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


def test_heading_only_sections_are_not_indexed_and_ids_stay_stable():
    chunks = chunk_text(
        "# 价保政策\n\n## 期限\n\n下单后15天内。\n\n## 空标题",
        document_id="policy", title="政策", category="policy", chunk_size=700, chunk_overlap=0,
    )
    assert [c.chunk_id for c in chunks] == ["policy#2"]
    assert "下单后15天" in chunks[0].content


@pytest.mark.parametrize("change", ["content", "add", "delete", "chunk_size", "chunk_overlap"])
def test_changed_sources_and_chunking_refuse_stale_loads(settings, fake_embedder, tmp_path, change):
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "policy.md"
    source.write_text("# 退货\n\n退货期限为7天。")
    settings = settings.model_copy(update={"docs_dir": docs})
    kb = KnowledgeBase(settings, llm=fake_embedder)
    kb.build()
    if change == "content":
        source.write_text("# 退货\n\n退货期限为30天。")
    elif change == "add":
        (docs / "new.md").write_text("# 价保\n\n价保15天。")
    elif change == "delete":
        source.unlink()
    else:
        settings = settings.model_copy(update={change: getattr(settings, change) + 1})
    with pytest.raises(RuntimeError, match="过期"):
        KnowledgeBase(settings, llm=fake_embedder).load()
    if change == "content":
        with pytest.raises(RuntimeError, match="过期"):
            kb.search("退货期限")


def test_legacy_metadata_without_a_fingerprint_requires_a_rebuild(settings, built_kb):
    path = settings.index_dir / "kb_meta.json"
    meta = json.loads(path.read_text())
    del meta["fingerprint"]
    path.write_text(json.dumps(meta))
    with pytest.raises(RuntimeError, match="rebuild"):
        KnowledgeBase(settings, llm=built_kb.llm).load()


def test_documents_changed_during_embedding_do_not_publish_a_mixed_index(
    settings, fake_embedder, tmp_path,
):
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "policy.md"
    source.write_text("退货期限7天。")
    settings = settings.model_copy(update={"docs_dir": docs})

    class ChangingEmbedder:
        def embed(self, texts, **kwargs):
            source.write_text("退货期限30天。")
            return fake_embedder.embed(texts)

    with pytest.raises(RuntimeError, match="构建期间"):
        KnowledgeBase(settings, llm=ChangingEmbedder()).build()
    assert not (settings.index_dir / "kb.faiss").exists()
