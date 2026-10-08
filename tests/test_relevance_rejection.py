"""Reject weak evidence and refuse calibration from a different corpus or model."""

import hashlib
import json

import pytest

from acs.cs_tools import ToolRegistry
from acs.rag import CHUNKER_VERSION, KnowledgeBase


@pytest.fixture()
def calibrated(settings, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "policy.md"
    source.write_text("# 政策\n\n退货期限7天。")
    calibration = tmp_path / "relevance.json"
    calibration.write_text(json.dumps({
        "model": "fake-embedding", "chunker_version": CHUNKER_VERSION, "threshold": 0.5,
        "knowledge_sha256": hashlib.sha256(b"policy.md\0" + source.read_bytes()).hexdigest(),
    }))
    return settings.model_copy(update={
        "docs_dir": docs, "relevance_calibration_path": calibration, "embedding_dim": 2,
    })


class OrthogonalEmbedder:
    def embed(self, texts, *, stage):
        return [[0.0, 1.0] if text == "库外问题" else [1.0, 0.0] for text in texts]


@pytest.mark.parametrize("indices,expected", [([0], True), ([], False)])
def test_overlapping_scores_use_evidence_and_record_a_distinct_model_stage(
    calibrated, indices, expected,
):
    from types import SimpleNamespace

    path = calibrated.relevance_calibration_path
    data = json.loads(path.read_text())
    data["uncertain_until"] = 0.7
    path.write_text(json.dumps(data))

    class AmbiguousClient:
        def __init__(self):
            self.requests = []

        def embed(self, texts, *, stage):
            return [[0.6, 0.8] if stage == "kb_search" else [1.0, 0.0] for text in texts]

        def complete(self, prompt, **kwargs):
            self.requests.append((json.loads(prompt), kwargs))
            return SimpleNamespace(text=json.dumps({"indices": indices}))

    client = AmbiguousClient()
    kb = KnowledgeBase(calibrated, llm=client)
    kb.build()
    assert bool(kb.search("问题")) is expected
    assert len(client.requests) == 1
    assert client.requests[0][1]["stage"] == "kb_relevance"
    assert client.requests[0][1]["thinking"] is False
    assert "退货期限7天" in client.requests[0][0]["candidates"][0]["content"]


def test_rejected_search_reports_found_false_instead_of_unrelated_documents(calibrated):
    kb = KnowledgeBase(calibrated, llm=OrthogonalEmbedder())
    kb.build()
    assert kb.search("退货期限")[0].score == 1.0
    assert kb.search("库外问题") == []
    registry = ToolRegistry(calibrated, kb=kb)
    result, events, error = registry.run(
        "search_knowledge", '{"query":"库外问题"}', current_user_id="USER001",
    )
    assert error is None
    assert result["found"] is False
    assert result["documents"] == []
    assert next(e for e in events if e.type == "retrieval").detail["documents"] == []
    reloaded = KnowledgeBase(calibrated, llm=OrthogonalEmbedder())
    assert reloaded.search("库外问题") == []


@pytest.mark.parametrize("field,value", [
    ("model", "different-model"), ("knowledge_sha256", "outdated"), ("chunker_version", -1),
])
def test_calibration_mismatch_requires_new_measurement(calibrated, field, value):
    path = calibrated.relevance_calibration_path
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match="标定"):
        KnowledgeBase(calibrated, llm=OrthogonalEmbedder()).build()
    assert not (calibrated.index_dir / "kb.faiss").exists()


def test_omitting_calibration_is_an_explicit_controlled_baseline(calibrated):
    settings = calibrated.model_copy(update={"relevance_calibration_path": None})
    kb = KnowledgeBase(settings, llm=OrthogonalEmbedder())
    kb.build()
    assert kb.search("库外问题")
