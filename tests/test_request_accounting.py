"""One request must include both conversation and retrieval consumption."""

from types import SimpleNamespace

import openai

from acs.llm import LLMClient, Usage
from acs.rag import KnowledgeBase
from acs.service_agent import CustomerServiceAgent
from tests.conftest import FakeEmbedder
from tests.test_llm_request_shape import (
    FakeOpenAI,
    _Choice,
    _Function,
    _Message,
    _Response,
    _ToolCall,
)


def test_retrieval_usage_is_counted_and_cleared_on_the_next_turn(settings, monkeypatch):
    settings = settings.model_copy(update={
        "llm_api_key": "sk-test-not-real", "embedding_api_key": "audit-embedding",
    })
    embedder = FakeEmbedder()
    KnowledgeBase(settings, llm=embedder).build()
    llm = LLMClient(settings=settings)
    llm._client = FakeOpenAI([
        _Response([_Choice(_Message(tool_calls=[
            _ToolCall("search", _Function("search_knowledge", '{"query": "退货"}')),
        ]))]),
        _Response([_Choice(_Message(content="7天内可申请退货。"))]),
    ])
    provider = SimpleNamespace(embeddings=SimpleNamespace(
        create=lambda **kwargs: SimpleNamespace(
            data=[SimpleNamespace(embedding=v) for v in embedder.embed(kwargs["input"])],
            usage=SimpleNamespace(prompt_tokens=7, total_tokens=7),
        ),
    ))
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: provider)
    clock = iter([10.0, 10.25, 20.0, 20.1])
    monkeypatch.setattr("acs.service_agent.perf_counter", lambda: next(clock), raising=False)
    agent = CustomerServiceAgent(settings=settings, llm=llm)

    first = agent.chat("退货政策是什么？")
    assert first.totals["calls"] == 3
    assert first.totals["total_tokens"] == 247
    assert first.totals["unpriced_models"] == [settings.embedding_model]
    assert first.totals["cost_is_partial"] is True
    assert first.totals["latency_ms"] == 250
    assert [u["stage"] for u in first.totals["usages"]] == [
        "agent_step_1", "kb_search", "agent_step_2",
    ]

    second = agent.chat("谢谢")
    assert second.totals["calls"] == 1
    assert second.totals["total_tokens"] == 120
    assert second.totals["unpriced_models"] == []
    assert second.totals["cost_is_partial"] is False


def test_unknown_prices_are_visible_as_partial_cost(settings):
    client = object.__new__(LLMClient)
    client.settings = settings
    client.usages = [Usage(model="unknown", stage="test", total_tokens=10)]
    assert client.totals()["cost_is_partial"] is True


def test_ui_labels_partial_cost():
    from app import render_stats

    from acs.service_agent import AgentResult

    result = AgentResult(answer="", totals={"cost_cny": "0.001", "unpriced_models": ["embed"]})
    assert "部分" in render_stats(result)
