"""The LLM client's request shape, asserted without any network call.

This file exists because the riskiest integration point in the MVP is not the
agent loop but the request the client builds. Two things had to be true for
DeepSeek to behave and were previously only verified by observing real output:

* thinking mode must be explicitly disabled, because DeepSeek enables it by
  default and it ignores `temperature` -- so a run would silently not be
  deterministic;
* tool schemas must be forwarded verbatim and tool calls parsed back out.

Both are asserted here against a fake OpenAI client, so a future refactor that
drops `extra_body` or forgets `tools` fails immediately instead of quietly
doubling latency and cost in an experiment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from acs.config import Settings
from acs.llm import LLMClient


# --------------------------------------------------------------------------- #
# a fake OpenAI-shaped client
# --------------------------------------------------------------------------- #
@dataclass
class _Function:
    name: str
    arguments: str


@dataclass
class _ToolCall:
    id: str
    function: _Function


@dataclass
class _Message:
    content: str | None = None
    tool_calls: list[_ToolCall] | None = None
    reasoning_content: str | None = None


@dataclass
class _Choice:
    message: _Message
    finish_reason: str = "stop"


@dataclass
class _Usage:
    prompt_tokens: int = 100
    completion_tokens: int = 20
    total_tokens: int = 120
    prompt_tokens_details: Any = None


@dataclass
class _Response:
    choices: list[_Choice]
    usage: _Usage = field(default_factory=_Usage)


class FakeCompletions:
    def __init__(self, responses: list[_Response]) -> None:
        self._responses = responses
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Response:
        self.requests.append(kwargs)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        return self._responses[index]


class FakeOpenAI:
    def __init__(self, responses: list[_Response]) -> None:
        self.chat = type("Chat", (), {"completions": FakeCompletions(responses)})()


@pytest.fixture()
def settings_with_key() -> Settings:
    return Settings(
        _env_file=None,
        llm_api_key="sk-test-not-a-real-key",
        llm_model="deepseek-flash",
        llm_thinking=False,
        llm_temperature=0.0,
        llm_max_output_tokens=512,
    )


def make_client(settings: Settings, responses: list[_Response]) -> tuple[LLMClient, FakeOpenAI]:
    client = LLMClient(settings=settings)
    fake = FakeOpenAI(responses)
    client._client = fake  # noqa: SLF001 - injecting the seam the client owns
    return client, fake


TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_order",
        "description": "查订单",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}},
    },
}


# --------------------------------------------------------------------------- #
# thinking mode
# --------------------------------------------------------------------------- #
def test_thinking_is_explicitly_disabled_by_default(settings_with_key) -> None:
    client, fake = make_client(settings_with_key, [_Response([_Choice(_Message(content="ok"))])])
    client.complete("hi")

    request = fake.chat.completions.requests[0]
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}, (
        "thinking must be explicitly disabled; DeepSeek enables it by default"
    )
    assert "reasoning_effort" not in request


def test_temperature_is_sent_only_when_thinking_is_off(settings_with_key) -> None:
    client, fake = make_client(settings_with_key, [_Response([_Choice(_Message(content="ok"))])])
    client.complete("hi")
    assert fake.chat.completions.requests[0]["temperature"] == 0.0

    on = Settings(
        _env_file=None,
        llm_api_key="sk-test-not-a-real-key",
        llm_thinking=True,
    )
    client2, fake2 = make_client(on, [_Response([_Choice(_Message(content="ok"))])])
    client2.complete("hi")
    request = fake2.chat.completions.requests[0]
    assert request["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "temperature" not in request, (
        "thinking mode ignores temperature; sending it would imply control that "
        "does not exist and make a trace look reproducible when it is not"
    )
    assert request["reasoning_effort"]


# --------------------------------------------------------------------------- #
# tools
# --------------------------------------------------------------------------- #
def test_tools_are_forwarded_verbatim(settings_with_key) -> None:
    client, fake = make_client(settings_with_key, [_Response([_Choice(_Message(content="ok"))])])
    client.chat([{"role": "user", "content": "查订单"}], tools=[TOOL_SCHEMA])

    assert fake.chat.completions.requests[0]["tools"] == [TOOL_SCHEMA]


def test_tools_are_omitted_when_not_supplied(settings_with_key) -> None:
    client, fake = make_client(settings_with_key, [_Response([_Choice(_Message(content="ok"))])])
    client.complete("hi")
    assert "tools" not in fake.chat.completions.requests[0]


def test_tool_calls_are_parsed_out_of_the_response(settings_with_key) -> None:
    response = _Response(
        [
            _Choice(
                _Message(
                    content=None,
                    tool_calls=[_ToolCall("call_1", _Function("get_order", '{"order_id": "O1"}'))],
                ),
                finish_reason="tool_calls",
            )
        ]
    )
    client, _ = make_client(settings_with_key, [response])
    result = client.chat([{"role": "user", "content": "查"}], tools=[TOOL_SCHEMA])

    assert result.wants_tools is True
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert (call.id, call.name, call.arguments) == ("call_1", "get_order", '{"order_id": "O1"}')


def test_plain_answer_reports_no_tool_calls(settings_with_key) -> None:
    client, _ = make_client(
        settings_with_key, [_Response([_Choice(_Message(content="你好，有什么可以帮您？"))])]
    )
    result = client.chat([{"role": "user", "content": "你好"}], tools=[TOOL_SCHEMA])

    assert result.wants_tools is False
    assert result.tool_calls == []
    assert "有什么可以帮您" in result.text


# --------------------------------------------------------------------------- #
# accounting
# --------------------------------------------------------------------------- #
def test_usage_is_recorded_and_priced(settings_with_key) -> None:
    client, _ = make_client(settings_with_key, [_Response([_Choice(_Message(content="ok"))])])
    client.complete("hi")

    usage = client.usages[0]
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 20
    assert usage.thinking is False
    assert usage.cost_cny is not None and usage.cost_cny > 0

    totals = client.totals()
    assert totals["calls"] == 1
    assert totals["total_tokens"] == 120
    assert totals["unpriced_models"] == []


def test_reasoning_content_length_is_recorded_when_present(settings_with_key) -> None:
    """Thinking tokens are billed as output; a silent spike must be visible."""
    response = _Response([_Choice(_Message(content="ok", reasoning_content="思考" * 50))])
    client, _ = make_client(settings_with_key, [response])
    client.complete("hi", thinking=True)
    assert client.usages[0].reasoning_chars == 100


def test_reset_usage_clears_totals(settings_with_key) -> None:
    client, _ = make_client(settings_with_key, [_Response([_Choice(_Message(content="ok"))])])
    client.complete("hi")
    client.reset_usage()
    assert client.totals()["calls"] == 0


def test_missing_key_raises_a_clear_error() -> None:
    with pytest.raises(RuntimeError, match="ACS_LLM_API_KEY"):
        LLMClient(settings=Settings(_env_file=None, llm_api_key=""))
