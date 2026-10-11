"""Offline regressions for lost evidence, API integrity and shared accounting."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from acs.llm import LLMClient
from acs.rag import Chunk, KnowledgeBase
from acs.retrieval_eval import evidence_metrics


def candidates():
    return [Chunk(key, key.split("#")[0], "政策", "policy", text,
                  score=0.03, score_type="rrf") for key, text in [
        ("warranty#2", "保修12个月"), ("return#1", "适用于所有商品"),
        ("return#2", "无理由7天"), ("return#4", "无理由7天，质量问题15天"),
    ]]


class EvidenceClient:
    def complete(self, prompt, **kwargs):
        pool = json.loads(prompt)["candidates"]
        return SimpleNamespace(text=json.dumps({
            "indices": [c["index"] for c in pool if "7天" in c["content"]],
        }))


def test_backfill_recovers_rank_four_after_irrelevant_top_hits_are_rejected(settings):
    kb = KnowledgeBase(settings, llm=EvidenceClient())
    kb._min_score, kb._uncertain_until = 0.5, 0.6
    groups = [["return#2", "return#4"], ["return#4"]]
    baseline = kb.select_evidence("退货期限", candidates(), backfill=False)
    after = kb.select_evidence("退货期限", candidates(), backfill=True)
    assert evidence_metrics(groups, [c.chunk_id for c in baseline])["recall"] == 0.5
    assert evidence_metrics(groups, [c.chunk_id for c in after])["recall"] == 1.0
    assert {c.chunk_id for c in after} == {"return#2", "return#4"}


def test_backfill_keeps_the_context_limit_and_rejects_an_entire_unsupported_pool(settings):
    kb = KnowledgeBase(settings, llm=EvidenceClient())
    kb._min_score, kb._uncertain_until = 0.5, 0.6
    valid = candidates()[2:]
    assert len(kb.select_evidence("退货期限", valid * 3, backfill=True)) == 3
    assert kb.select_evidence("退货期限", candidates()[:2], backfill=True) == []


def test_new_validation_is_frozen_and_old_cases_are_all_development_evidence(settings):
    from acs.config import PROJECT_ROOT
    from acs.retrieval_eval import load_cases

    kb = KnowledgeBase(settings)
    kb._chunks = kb.load_documents()
    dataset = load_cases(PROJECT_ROOT / "data/eval/rerank_cases.json", kb)
    old = json.loads((PROJECT_ROOT / "data/eval/retrieval_cases.json").read_text())
    dev = [c for c in dataset["cases"] if c["split"] == "development"]
    validation = [c for c in dataset["cases"] if c["split"] == "validation"]
    assert dev == [dict(c, split="development") for c in old["cases"]]
    assert len(validation) == 20 and sum(bool(c["evidence_groups"]) for c in validation) == 14
    # A paragraph mentioning LDAC alone cannot support a complete list of audio codecs.
    codecs = next(c for c in validation if c["id"] == "q45-cable-codecs")
    assert codecs["evidence_groups"][0] == ["headphone_b#2"]


def client(settings, **overrides):
    return LLMClient(settings=settings.model_copy(update={
        "llm_api_key": "test-placeholder", "reranker_api_key": "rerank-placeholder",
        **overrides,
    }))


def test_rerank_maps_api_indices_to_original_chunks_and_counts_usage(settings, monkeypatch):
    def post(url, **kwargs):
        assert url == "https://api.siliconflow.cn/v1/rerank"
        assert kwargs["json"]["top_n"] == 4
        assert kwargs["json"]["documents"][3] == "政策\n无理由7天，质量问题15天"
        return httpx.Response(200, json={
            "results": [{"index": i, "relevance_score": score}
                        for i, score in [(3, .99), (2, .8), (0, .1), (1, .01)]],
            "meta": {"tokens": {"input_tokens": 100, "output_tokens": 0}},
        })

    monkeypatch.setattr(httpx, "post", post)
    llm = client(settings)
    kb = KnowledgeBase(settings, llm=llm)
    original = candidates()
    result = kb.rerank("退货期限", original)
    assert result[0].chunk_id == "return#4" and result[0].score_type == "reranker"
    assert result[0].retrieval["rrf"]["rank"] == 4
    assert result[0].retrieval["reranker"]["rank"] == 1
    assert original[3].score_type == "rrf" and original[3].retrieval == {}
    totals = llm.totals()
    assert totals["calls"] == 1 and totals["total_tokens"] == 100
    assert totals["usages"][0]["stage"] == "kb_rerank"
    assert totals["cost_is_partial"] and not totals["tokens_are_partial"]


@pytest.mark.parametrize("results", [
    [], [{"index": 0, "relevance_score": .5}],
    [{"index": 0, "relevance_score": .5}, {"index": 0, "relevance_score": .2}],
    [{"index": -1, "relevance_score": .5}, {"index": 1, "relevance_score": .2}],
    [{"index": False, "relevance_score": .5}, {"index": 1, "relevance_score": .2}],
    [{"index": 0, "relevance_score": "0.5"}, {"index": 1, "relevance_score": .2}],
])
def test_invalid_api_ranking_never_silently_drops_or_substitutes_evidence(
    settings, monkeypatch, results,
):
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: httpx.Response(200, json={
        "results": results, "tokens": {"input_tokens": 42, "output_tokens": 0},
    }))
    llm = client(settings)
    with pytest.raises(ValueError):
        llm.rerank("问题", ["片段1", "片段2"])
    assert llm.totals()["total_tokens"] == 42


def test_rerank_rejects_nonfinite_scores_and_marks_missing_usage(settings, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: SimpleNamespace(
        is_success=True, json=lambda: {"results": [{"index": 0, "relevance_score": float("nan")}]},
    ))
    llm = client(settings)
    with pytest.raises(ValueError, match="scores"):
        llm.rerank("问题", ["片段"])
    assert llm.totals()["tokens_are_partial"]


@pytest.mark.parametrize("failure", ["http", "timeout"])
def test_failed_rerank_is_visible_accounted_and_never_falls_back(settings, monkeypatch, failure):
    def post(*args, **kwargs):
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(503, text="private provider details")

    monkeypatch.setattr(httpx, "post", post)
    llm = client(settings)
    with pytest.raises((RuntimeError, httpx.ReadTimeout)) as err:
        llm.rerank("问题", ["片段"])
    assert "private provider details" not in str(err.value)
    assert llm.totals()["calls"] == 1 and llm.totals()["tokens_are_partial"]


def test_key_reuse_requires_identical_service_and_empty_pool_makes_no_request(
    settings, monkeypatch,
):
    def post(*args, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer embedding-placeholder"
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": .3}]})

    monkeypatch.setattr(httpx, "post", post)
    llm = client(settings, reranker_api_key="", embedding_api_key="embedding-placeholder",
                 embedding_base_url="https://api.siliconflow.cn/v1/")
    assert llm.rerank("问题", []) == [] and not llm.usages
    assert len(llm.rerank("问题", ["片段"])) == 1
    llm = client(settings, reranker_api_key="", embedding_api_key="embedding-placeholder",
                 embedding_base_url="https://other.example/v1")
    with pytest.raises(RuntimeError, match="ACS_RERANKER_API_KEY"):
        llm.rerank("问题", ["片段"])
    assert not llm.usages


def test_search_applies_enabled_rerank_before_verification_and_bounds_context(
    settings, monkeypatch,
):
    class RerankClient(EvidenceClient):
        def rerank(self, query, documents):
            return [{"index": i, "relevance_score": score}
                    for i, score in [(3, .99), (0, .1), (1, .05), (2, .01)]]

    s = settings.model_copy(update={"reranker_enabled": True, "evidence_backfill": True})
    kb = KnowledgeBase(s, llm=RerankClient())
    kb._min_score, kb._uncertain_until = 0.5, 0.6

    def retrieve(query, **kwargs):
        assert kwargs["top_k"] == 10
        return candidates()

    monkeypatch.setattr(kb, "retrieve", retrieve)
    result = kb.search("退货期限")
    assert [c.chunk_id for c in result] == ["return#4", "return#2"]
    assert all(c.score_type == "reranker" for c in result)


def test_reranker_score_cannot_bypass_text_verification(settings):
    kb = KnowledgeBase(settings, llm=EvidenceClient())
    kb._min_score, kb._uncertain_until = 0.5, 0.6
    irrelevant = candidates()[0]
    irrelevant.score_type, irrelevant.score = "reranker", .999
    assert kb.select_evidence("退货期限", [irrelevant]) == []


def test_ablation_retains_failed_rerank_without_counting_it_as_a_refusal(settings, tmp_path):
    from acs.llm import Usage
    from acs.rerank_eval import VARIANTS, run_evaluation

    class FailedRerankClient:
        def __init__(self):
            self.usages = []

        def embed(self, texts, **kwargs):
            self.usages.append(Usage(model="fake-embedding", stage=kwargs["stage"], total_tokens=7))
            return [[0.0] * 8]

        def rerank(self, *args):
            self.usages.append(Usage(model=settings.reranker_model, stage="kb_rerank",
                                     usage_missing=True))
            raise RuntimeError("service unavailable")

        def complete(self, prompt, **kwargs):
            self.usages.append(Usage(model="test-llm", stage=kwargs["stage"], total_tokens=10))
            data = json.loads(prompt)
            return SimpleNamespace(text=json.dumps({
                "indices": [] if data["question"] == "unknown" else [0],
            }))

        def totals(self):
            return {"calls": len(self.usages), "total_tokens": sum(u.total_tokens
                                                                     for u in self.usages)}

    class FrozenKB(KnowledgeBase):
        def load(self):
            self._chunks = self.load_documents()
            self._fingerprint = self._current_fingerprint()
            self._min_score, self._uncertain_until = .5, .6

        def retrieve(self, query, **kwargs):
            return [Chunk("return_policy#2", "return_policy", "退货", "policy", "7天",
                          score=.03, score_type="rrf")]

    calibration = tmp_path / "calibration.json"
    calibration.write_text("{}")
    settings = settings.model_copy(update={"relevance_calibration_path": calibration})
    settings.index_dir.mkdir()
    index = settings.index_dir / "kb.faiss"
    index.write_bytes(b"frozen test index")
    kb = FrozenKB(settings, llm=FailedRerankClient())
    kb.load()
    conditions = {"candidate_k": 10, "context_k": 3, "rrf_k": 60}
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"conditions": conditions, "cases": [
        {"id": "supported", "split": "development", "query": "退货", "evidence_groups": [
            ["return_policy#2"],
        ]},
        {"id": "unsupported", "split": "development", "query": "unknown", "evidence_groups": []},
    ]}))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "cases_sha256": hashlib.sha256(cases.read_bytes()).hexdigest(),
        "knowledge_fingerprint": kb._fingerprint, "embedding_model": settings.embedding_model,
        "index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
        "calibration_sha256": hashlib.sha256(calibration.read_bytes()).hexdigest(),
        "reranker_model": settings.reranker_model, "reranker_base_url": settings.reranker_base_url,
        "llm_model": settings.llm_model, "conditions": conditions, "variants": list(VARIANTS),
    }))
    out = tmp_path / "result"
    report = run_evaluation(kb, cases, manifest, "development", out)
    m = report["summary"]
    assert m["hybrid"]["errors"] == 0 and m["hybrid"]["unsupported_rejected"] == 1
    assert m["hybrid_rerank"]["errors"] == 2
    assert m["hybrid_rerank"]["unsupported_rejected"] == 0
    assert m["hybrid_rerank"]["rerank_calls"] == 2
    # Two embeds + two reranks + six verifications; shared requests count once.
    assert report["actual_accounting"]["calls"] == 10
    records = [json.loads(line) for line in (out / "per_case.jsonl").read_text().splitlines()]
    assert len(records) == 10 and sum(r["error"] is not None for r in records) == 4
    with pytest.raises(FileExistsError):
        run_evaluation(kb, cases, manifest, "development", out)
