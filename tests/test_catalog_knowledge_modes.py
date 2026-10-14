"""Catalogue facts, real capabilities and simple knowledge source boundaries."""

from __future__ import annotations

import hashlib
import json

import pytest

from acs.cs_tools import ToolRegistry
from acs.service_agent import CustomerServiceAgent

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall


def test_model_can_enumerate_complete_catalogue_with_current_prices(settings):
    registry = ToolRegistry(settings)
    payload, _, error = registry.run("list_products", "{}", current_user_id="USER001")
    assert error is None
    assert payload["complete_catalog"] is True and payload["count"] == 4
    products = {p["product_id"]: p for p in payload["products"]}
    assert set(products) == {"P001", "P002", "P003", "P004"}
    assert products["P001"]["current_price"] == 469.0
    assert "price" not in products["P001"]
    assert products["P002"]["current_price"] == 879.0
    assert "入耳式" in products["P001"]["attributes"]
    assert "头戴式" in products["P002"]["attributes"]


@pytest.mark.parametrize("failure", ["api", "step_limit"])
def test_real_fallback_does_not_point_to_nonexistent_human_channel(settings, failure):
    class FailingLLM(ScriptedLLM):
        def chat(self, *args, **kwargs):
            raise RuntimeError("simulated API failure")

    llm = FailingLLM([]) if failure == "api" else ScriptedLLM([
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall("repeat", "get_product", '{"product_id":"P001"}'),
        ]),
    ])
    result = CustomerServiceAgent(settings=settings, llm=llm, max_steps=1).chat("需要帮助")
    assert result.stop_reason == ("llm_error" if failure == "api" else "max_steps")
    assert "人工接入" in result.answer and "没有" in result.answer
    assert "联系人工客服" not in result.answer


def test_application_exception_also_reports_actual_human_capability(settings, monkeypatch):
    import app

    class BrokenAgent:
        registry = ToolRegistry(settings)

        def chat_stream(self, message):
            raise RuntimeError("simulated handler failure")

    monkeypatch.setattr(app, "SETTINGS", settings)
    monkeypatch.setattr(app, "_SESSION_STORE", None)
    monkeypatch.setattr(app, "ensure_agent", lambda *_: BrokenAgent())
    updates = list(app.on_send("需要帮助", [], {}, "USER001"))
    answer = updates[-1][0][-1]["content"]
    assert "人工接入" in answer and "没有" in answer
    assert "联系人工客服" not in answer


def test_full_context_uses_fresh_versioned_sources_without_an_index(settings, tmp_path):
    path = tmp_path / "policy.md"
    path.write_text("---\ndocument_id: policy\nversion: v1\n---\n完整正文甲乙丙")
    settings = settings.model_copy(update={"knowledge_mode": "full_context", "docs_dir": tmp_path})
    llm = ScriptedLLM([ScriptedResponse(text="有依据的回答")])
    agent = CustomerServiceAgent(settings=settings, llm=llm)
    agent.chat("政策是什么")
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "完整正文甲乙丙" in prompt
    assert hashlib.sha256(path.read_bytes()).hexdigest() in prompt
    assert "search_knowledge" not in {s["function"]["name"] for s in llm.calls[0]["tools"]}
    assert not settings.index_dir.exists()
    path.write_text("---\ndocument_id: policy\nversion: v2\n---\n修订后正文丁戊己")
    agent.reset()
    assert "修订后正文丁戊己" in agent.messages[0]["content"]
    assert "完整正文甲乙丙" not in agent.messages[0]["content"]


def test_document_mode_returns_whole_selected_documents_and_provenance(settings, tmp_path):
    path = tmp_path / "policy.md"
    body = "首项事实。" * 180 + "最后一项事实。"
    path.write_text("---\ndocument_id: policy\nversion: v1\n---\n" + body)
    settings = settings.model_copy(update={"knowledge_mode": "documents", "docs_dir": tmp_path})
    registry = ToolRegistry(settings)
    payload, _, error = registry.run(
        "get_knowledge_documents", json.dumps({"document_ids": ["policy"]}),
        current_user_id="USER001",
    )
    assert error is None
    assert payload["documents"][0]["content"] == body
    assert payload["documents"][0]["hash"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert payload["documents"][0]["version"] == "v1"
    assert "价保只有查询和政策咨询" in payload["application_notice"]
    assert '"entry": null' in payload["application_notice"]
    assert "search_knowledge" not in registry.names
    assert not settings.index_dir.exists()


@pytest.mark.parametrize("ids", [["../.env"], ["unknown"], [], "policy"])
def test_document_selection_rejects_invalid_ids_without_partial_evidence(settings, tmp_path, ids):
    (tmp_path / "policy.md").write_text("---\ndocument_id: policy\n---\n正文")
    settings = settings.model_copy(update={"knowledge_mode": "documents", "docs_dir": tmp_path})
    payload, _, error = ToolRegistry(settings).run(
        "get_knowledge_documents", json.dumps({"document_ids": ids}), current_user_id="USER001",
    )
    assert error and "invalid_arguments" in error
    assert "documents" not in payload
