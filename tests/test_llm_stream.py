"""Real streaming boundaries, fragmented tool calls and interrupted accounting."""

from types import SimpleNamespace as NS

import pytest

from acs.config import Settings
from acs.llm import LLMResponse

from .test_llm_request_shape import TOOL_SCHEMA, _Usage, make_client


def chunk(text=None, *, calls=None, reasoning=None, finish=None, usage=None):
    return NS(usage=usage, choices=[NS(
        delta=NS(content=text, tool_calls=calls, reasoning_content=reasoning),
        finish_reason=finish,
    )])


def tool(index, *, id=None, name=None, arguments=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=arguments))


class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    def __iter__(self):
        for item in self.chunks:
            self.reads += 1
            if isinstance(item, Exception):
                raise item
            yield item

    def close(self):
        self.closed = True


def client_for(stream):
    settings = Settings(_env_file=None, llm_api_key="test-key", llm_model="deepseek-flash")
    return make_client(settings, [stream])


@pytest.mark.parametrize("thinking", [False, True])
def test_text_arrives_before_the_stream_finishes_and_usage_is_counted_once(thinking):
    stream = FakeStream([
        chunk("您好", reasoning="隐藏思考"), chunk("，可以申请。"),
        chunk(finish="stop"),
        NS(choices=[], usage=_Usage(prompt_tokens_details=NS(cached_tokens=40))),
    ])
    client, fake = client_for(stream)
    events = client.chat_stream(
        [{"role": "user", "content": "退货"}], tools=[TOOL_SCHEMA],
        stage="answer", thinking=thinking,
    )
    assert next(events) == "您好"
    assert stream.reads == 1, "the first text must precede reading the rest of the response"
    assert not stream.closed
    assert client.usages == []
    assert next(events) == "，可以申请。"
    response = next(events)
    assert isinstance(response, LLMResponse)
    assert response.text == "您好，可以申请。"
    assert response.finish_reason == "stop"
    assert list(events) == []
    assert stream.closed
    assert client.totals()["calls"] == 1
    assert response.usage.total_tokens == 120
    assert response.usage.cached_prompt_tokens == 40
    assert response.usage.reasoning_chars == len("隐藏思考")
    assert response.usage.cost_cny > 0
    request = fake.chat.completions.requests[0]
    assert request["stream"] is True
    assert request["stream_options"] == {"include_usage": True}
    assert request["tools"] == [TOOL_SCHEMA]
    assert request["extra_body"]["thinking"]["type"] == ("enabled" if thinking else "disabled")
    assert ("temperature" in request) is not thinking


def test_interleaved_tool_fragments_are_assembled_before_the_response_is_published():
    stream = FakeStream([
        chunk(calls=[tool(1, id="log", name="get_log", arguments='{"order_id":')]),
        chunk(calls=[tool(0, id="ord", name="get_", arguments='{"order_')]),
        chunk(calls=[tool(1, name="istics", arguments='"O10024"}')]),
        chunk(calls=[tool(0, id="er", name="order", arguments='id":"O10024"}')]),
        chunk(finish="tool_calls", usage=_Usage()),
    ])
    client, _ = client_for(stream)
    events = list(client.chat_stream([]))
    assert len(events) == 1, "partial arguments must never reach tool execution"
    calls = events[0].tool_calls
    assert [(c.id, c.name, c.arguments) for c in calls] == [
        ("order", "get_order", '{"order_id":"O10024"}'),
        ("log", "get_logistics", '{"order_id":"O10024"}'),
    ]
    assert events[0].wants_tools
    assert stream.closed


@pytest.mark.parametrize("tail", [[RuntimeError("断开连接")], []])
def test_interruption_is_reported_and_missing_usage_is_not_priced_as_zero(tail):
    stream = FakeStream([chunk("还未完成"), *tail])
    client, _ = client_for(stream)
    events = client.chat_stream([])
    assert next(events) == "还未完成"
    with pytest.raises(RuntimeError):
        next(events)
    assert stream.closed
    assert client.totals()["calls"] == 1
    assert client.totals()["cost_is_partial"] is True
    assert client.usages[0].cost_cny is None


def test_closing_a_partial_stream_releases_the_provider_connection():
    stream = FakeStream([chunk("第一段"), chunk("第二段"), chunk(finish="stop")])
    client, _ = client_for(stream)
    events = client.chat_stream([])
    assert next(events) == "第一段"
    events.close()
    assert stream.closed
    assert stream.reads == 1
    assert client.totals()["calls"] == 1


def test_a_completed_stream_without_usage_has_partial_accounting():
    client, _ = client_for(FakeStream([chunk("完成", finish="stop")]))
    response = list(client.chat_stream([]))[-1]
    assert response.text == "完成"
    assert client.totals()["cost_is_partial"] is True
