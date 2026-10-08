"""Partial answers must reach the UI before the model completes its response."""

from acs.service_agent import CustomerServiceAgent

from .test_llm_request_shape import _Usage, make_client
from .test_llm_stream import FakeStream, chunk, tool


def make_agent(settings, streams):
    settings = settings.model_copy(update={"llm_api_key": "test-key"})
    llm, _ = make_client(settings, streams)
    return CustomerServiceAgent(settings=settings, llm=llm)


def test_answer_snapshots_arrive_before_model_completion_and_remain_immutable(settings):
    provider = FakeStream([
        chunk("您可以"), chunk("申请退货。"), chunk(finish="stop", usage=_Usage()),
    ])
    agent = make_agent(settings, [provider])
    events = agent.chat_stream("怎么退货？")
    assert next(events).answer == ""
    first = next(events)
    assert first.answer == "您可以"
    assert first.stop_reason == "running"
    assert provider.reads == 1
    assert agent.messages[-1]["role"] == "user", "partial text is not a completed model turn"
    second = next(events)
    assert second.answer == "您可以申请退货。"
    assert second.stop_reason == "running"
    final = list(events)[-1]
    assert final.stop_reason == "answered"
    assert final.totals["calls"] == 1
    assert [e.type for e in final.trace] == ["final"]
    assert agent.messages[-1]["content"] == final.answer
    assert first.answer == "您可以"


def test_tool_arguments_finish_before_execution_and_only_the_answer_is_retained(settings):
    tools = FakeStream([
        chunk("我来查询。"),
        chunk(calls=[tool(0, id="order", name="get_order", arguments='{"order_id":')]),
        chunk(calls=[tool(0, arguments='"O10024"}')]),
        chunk(finish="tool_calls", usage=_Usage()),
    ])
    answer = FakeStream([
        chunk("订单"), chunk("已签收。"), chunk(finish="stop", usage=_Usage()),
    ])
    agent = make_agent(settings, [tools, answer])
    events = agent.chat_stream("查 O10024")
    next(events)
    assert next(events).answer == "我来查询。"
    calling = next(events)
    assert tools.closed, "the provider stream must finish before tools can execute"
    assert calling.answer == ""
    assert agent.messages[-1]["role"] == "assistant"
    assert agent.messages[-1]["tool_calls"][0]["function"]["arguments"] == (
        '{"order_id":"O10024"}'
    )
    returned = next(events)
    assert returned.trace[-1].type == "tool_result"
    assert returned.errors == []
    rest = list(events)
    assert any(s.answer == "订单" and s.stop_reason == "running" for s in rest)
    final = rest[-1]
    assert final.answer == "订单已签收。"
    assert final.tool_calls == 1
    assert final.totals["calls"] == 2
    assert final.totals["total_tokens"] == 240
    assert [e.type for e in final.trace] == ["tool_call", "tool_result", "final"]


def test_interrupted_answer_stays_visible_with_an_explicit_failure_notice(settings):
    provider = FakeStream([chunk("根据政策"), RuntimeError("连接断开")])
    agent = make_agent(settings, [provider])
    results = list(agent.chat_stream("如何退货？"))
    assert results[1].answer == "根据政策"
    final = results[-1]
    assert final.answer.startswith("根据政策")
    assert "回答中断" in final.answer
    assert final.stop_reason == "llm_error"
    assert final.totals["calls"] == 1
    assert final.totals["cost_is_partial"] is True
    assert [e.type for e in final.trace] == ["error"]
    assert provider.closed


def test_cancelling_the_agent_stream_closes_the_model_connection(settings):
    provider = FakeStream([chunk("第一段"), chunk("第二段"), chunk(finish="stop")])
    agent = make_agent(settings, [provider])
    results = agent.chat_stream("你好")
    next(results)
    assert next(results).answer == "第一段"
    results.close()
    assert provider.closed
    assert provider.reads == 1
