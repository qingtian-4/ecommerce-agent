"""Regression coverage for lexical evidence, fusion and score-safe rejection."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from acs.rag import Chunk, KnowledgeBase
from acs.retrieval import LexicalIndex, reciprocal_rank_fusion, tokenize
from acs.retrieval_eval import evidence_metrics, load_cases, summarize


def chunk(key, text):
    return Chunk(key, key.split("#")[0], "商品说明", "product", text)


def test_tokenization_preserves_ports_skus_decimals_and_units():
    words = tokenize("P003 USB-C1 LDAC 3.5 mm 37 Wh 65W 质量问题退货")
    assert {"p003", "usb-c1", "ldac", "3.5mm", "37wh", "65w", "退货"} <= set(words)
    assert tokenize("37Wh") == tokenize("37 Wh")
    assert tokenize("USB-C1") == tokenize("usb-c1")


def test_bm25_matches_exact_protocol_and_ignores_zero_overlap():
    index = LexicalIndex([
        chunk("q45#2", "音频编码 LDAC AAC SBC"),
        chunk("charger#2", "充电协议 PPS PD 65W"),
        chunk("bank#2", "容量 10000mAh 37Wh"),
    ])
    assert index.search("LDAC", 3)[0].chunk_id == "q45#2"
    assert index.search("unmentioned-protocol", 3) == []


def test_fusion_uses_ranks_not_incompatible_raw_scores_and_deduplicates():
    a, b, c = [chunk(key, key) for key in ("a#1", "b#1", "c#1")]
    a.score, b.score, c.score = 0.99, 0.1, 900.0
    result = reciprocal_rank_fusion([a, b], [c, b, b])
    assert [r.chunk_id for r in result] == ["b#1", "a#1", "c#1"]
    assert result[0].score_type == "rrf"
    assert result[0].retrieval["dense"]["rank"] == 2
    assert result[0].retrieval["bm25"]["rank"] == 2
    assert len({r.chunk_id for r in result}) == 3
    assert reciprocal_rank_fusion([], [a])[0].chunk_id == "a#1"


def test_bm25_search_never_calls_embedding(settings, fake_embedder):
    kb = KnowledgeBase(settings, llm=fake_embedder)
    kb.build()

    class NoNetwork:
        def embed(self, *args, **kwargs):
            pytest.fail("lexical ranking must not call an embedding API")

    kb._llm = NoNetwork()
    hits = kb.retrieve("P002 LDAC 开启", mode="bm25", top_k=3)
    assert hits and all(c.score_type == "bm25" for c in hits)
    assert any(c.document_id == "headphone_b" for c in hits)


def test_hybrid_returns_provenance_and_reloads_lexical_index(settings, fake_embedder):
    kb = KnowledgeBase(settings, llm=fake_embedder)
    kb.build()
    original = kb.retrieve("P003 USB-C1", mode="hybrid", top_k=3)
    reloaded = KnowledgeBase(settings, llm=fake_embedder)
    actual = reloaded.retrieve("P003 USB-C1", mode="hybrid", top_k=3)
    assert [c.as_dict() for c in actual] == [c.as_dict() for c in original]
    assert len(actual) <= 3
    assert all(c.score_type == "rrf" and c.retrieval for c in actual)


@pytest.mark.parametrize("score_type,score", [("rrf", 0.02), ("bm25", 100)])
@pytest.mark.parametrize("accepted", [True, False])
def test_non_cosine_scores_cannot_bypass_or_trigger_dense_threshold(
    settings, score_type, score, accepted,
):
    class EvidenceClient:
        def complete(self, prompt, **kwargs):
            assert json.loads(prompt)["candidates"][0]["content"] == "签收后7天"
            return SimpleNamespace(text=json.dumps({"indices": [0] if accepted else []}))

    kb = KnowledgeBase(settings, llm=EvidenceClient())
    kb._min_score, kb._uncertain_until = 0.5, 0.6
    candidate = chunk("return#2", "签收后7天")
    candidate.score, candidate.score_type = score, score_type
    assert bool(kb._filter_evidence("退货期限", [candidate])) is accepted


def test_evaluation_requires_every_claim_but_accepts_sufficient_alternatives():
    groups = [["return#2", "return#4"], ["return#3", "return#4"]]
    partial = evidence_metrics(groups, ["return#2", "warranty#2"])
    assert partial["recall"] == 0.5 and not partial["all_evidence"]
    assert evidence_metrics(groups, ["return#4"])["all_evidence"]
    assert evidence_metrics(groups, ["unrelated#1"])["mrr"] == 0
    assert evidence_metrics([], ["unrelated#1"])["recall"] is None


def test_frozen_case_labels_are_real_sections_and_queries_are_not_filtered(settings):
    from acs.config import PROJECT_ROOT

    kb = KnowledgeBase(settings)
    kb._chunks = kb.load_documents()
    dataset = load_cases(PROJECT_ROOT / "data/eval/retrieval_cases.json", kb)
    assert len(dataset["cases"]) == 52
    # A known miss remains in the dataset; retrieval success is never an admission rule.
    assert any(c["id"] == "q45-quickcharge" for c in dataset["cases"])


def test_evaluation_errors_are_not_counted_as_successful_refusals():
    row = {"mode": "dense", "evidence_groups": [], "accepted": [], "error": "APIError",
           "ranking_ms": 0, "verification_ms": 10, "paired_retrieval_ms": 10,
           "verification_usage": []}
    metrics = summarize([row])["dense"]
    assert metrics["errors"] == 1
    assert metrics["unsupported_rejected"] == 0
