"""The agent loop: tool execution, the step ceiling, and trace recording.

These use a scripted LLM, so a failure here means the loop is wrong. Assertions
are about control flow and bookkeeping, not about answer quality -- quality needs
the real model and is checked by scripts/run_demo_cases.py.
"""

from __future__ import annotations

import json

import pytest

from acs.cs_tools import ToolRegistry
from acs.rag import KnowledgeBase
from acs.service_agent import CustomerServiceAgent

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall


@pytest.fixture()
def registry(settings, fake_embedder):
    kb = KnowledgeBase(settings, llm=fake_embedder)
    kb.build()
    return ToolRegistry(settings, kb=kb)


def agent(settings, llm, registry, user: str = "USER001", max_steps: int | None = None):
    """Construct the agent under test with the scripted llm wired in.

    A helper rather than a fixture because each test scripts a different
    response sequence, and the sequence is the point of the test.
    """
    return CustomerServiceAgent(
        settings=settings,
        llm=llm,
        registry=registry,
        current_user_id=user,
        max_steps=max_steps,
    )


# --------------------------------------------------------------------------- #
# tool execution inside the loop
# --------------------------------------------------------------------------- #
def test_agent_executes_a_tool_then_answers(settings, registry) -> None:
    llm = ScriptedLLM(
        [
            ScriptedResponse(
                tool_calls=[ScriptedToolCall("c1", "get_order", '{"order_id": "O10023"}')],
                wants_tools=True,
            ),
            ScriptedResponse(text="您的订单正在运输途中。"),
        ]
    )
    result = agent(settings, llm, registry).chat("订单 O10023 到哪了？")

    assert result.answer == "您的订单正在运输途中。"
    assert result.stop_reason == "answered"
    assert result.tool_calls == 1
    assert result.steps == 2, "one step to call the tool, one to answer"

    assert [e.type for e in result.trace] == ["tool_call", "tool_result", "final"]
    assert result.trace[0].label == "get_order"

    # The tool result must actually reach the model before it answers.
    second_call = llm.calls[1]["messages"]
    assert any(m["role"] == "tool" for m in second_call)
    assert any(
        m["role"] == "assistant" and m.get("tool_calls") for m in second_call
    ), "the assistant tool_call message must be echoed back or the API call is invalid"


def test_agent_can_chain_two_tools(settings, registry) -> None:
    llm = ScriptedLLM(
        [
            ScriptedResponse(
                tool_calls=[
                    ScriptedToolCall("c1", "get_order", '{"order_id": "O10023"}'),
                    ScriptedToolCall("c2", "get_logistics", '{"order_id": "O10023"}'),
                ],
                wants_tools=True,
            ),
            ScriptedResponse(text="综合订单与物流：运输中。"),
        ]
    )
    result = agent(settings, llm, registry).chat("订单 O10023 现在到哪了？")

    assert result.tool_calls == 2
    assert [e.label for e in result.trace if e.type == "tool_call"] == [
        "get_order",
        "get_logistics",
    ]
    assert result.stop_reason == "answered"


def test_agent_retrieval_is_recorded_as_retrieved_documents(settings, registry) -> None:
    llm = ScriptedLLM(
        [
            ScriptedResponse(
                tool_calls=[ScriptedToolCall("c1", "search_knowledge", '{"query": "退货"}')],
                wants_tools=True,
            ),
            ScriptedResponse(text="7 天内可退。"),
        ]
    )
    result = agent(settings, llm, registry).chat("退货期限多久？")

    assert result.retrieval_queries == ["退货"]
    assert result.retrieved_documents, "retrieval must surface documents for the UI"
    assert all("score" in d and "chunk_id" in d for d in result.retrieved_documents)
    assert any(e.type == "retrieval" for e in result.trace)


# --------------------------------------------------------------------------- #
# the step ceiling
# --------------------------------------------------------------------------- #
def test_agent_stops_at_max_steps_and_does_not_loop_forever(settings, registry) -> None:
    """A model that keeps calling tools must be cut off, with a friendly answer."""
    forever = [
        ScriptedResponse(
            tool_calls=[ScriptedToolCall(f"c{i}", "get_order", '{"order_id": "O10023"}')],
            wants_tools=True,
        )
        for i in range(50)
    ]
    llm = ScriptedLLM(forever)
    result = agent(settings, llm, registry, max_steps=3).chat("查订单")

    assert result.stop_reason == "max_steps"
    assert result.steps == 3
    assert len(llm.calls) == 3, "must not call the model more than max_steps times"
    assert result.answer, "the user still needs a reply"
    assert any(
        e.type == "error" and e.detail.get("error_type") == "max_steps_exceeded"
        for e in result.trace
    )


# --------------------------------------------------------------------------- #
# error handling
# --------------------------------------------------------------------------- #
def test_unknown_tool_does_not_crash_the_loop(settings, registry) -> None:
    llm = ScriptedLLM(
        [
            ScriptedResponse(
                tool_calls=[ScriptedToolCall("c1", "drop_database", "{}")], wants_tools=True
            ),
            ScriptedResponse(text="抱歉，我无法执行该操作。"),
        ]
    )
    result = agent(settings, llm, registry).chat("删除数据库")

    assert result.stop_reason == "answered"
    assert any("tool_not_found" in e for e in result.errors)
    assert any(
        e.type == "error" and e.detail.get("error_type") == "tool_not_found"
        for e in result.trace
    )


def test_invalid_json_arguments_do_not_crash_the_loop(settings, registry) -> None:
    llm = ScriptedLLM(
        [
            ScriptedResponse(
                tool_calls=[ScriptedToolCall("c1", "get_order", "{not json")],
                wants_tools=True,
            ),
            ScriptedResponse(text="请提供订单号。"),
        ]
    )
    result = agent(settings, llm, registry).chat("查订单")

    assert result.stop_reason == "answered"
    assert any("invalid_arguments" in e for e in result.errors)


def test_wrong_argument_name_is_reported_not_raised(settings, registry) -> None:
    llm = ScriptedLLM(
        [
            ScriptedResponse(
                tool_calls=[ScriptedToolCall("c1", "get_order", '{"id": "O10023"}')],
                wants_tools=True,
            ),
            ScriptedResponse(text="请提供订单号。"),
        ]
    )
    result = agent(settings, llm, registry).chat("查订单")

    assert result.stop_reason == "answered"
    assert any("invalid_arguments" in e for e in result.errors)


def test_llm_failure_produces_a_friendly_answer_not_a_traceback(settings, registry) -> None:
    class ExplodingLLM:
        def chat(self, *a, **k):  # noqa: ANN002, ANN003
            raise RuntimeError("connection reset")

        def reset_usage(self) -> None:
            pass

        def totals(self) -> dict:
            return {}

    result = agent(settings, ExplodingLLM(), registry).chat("你好")

    assert result.stop_reason == "llm_error"
    assert "抱歉" in result.answer
    assert any("llm_error" in e for e in result.errors)


# --------------------------------------------------------------------------- #
# identity and history
# --------------------------------------------------------------------------- #
def test_cross_user_order_is_denied_through_the_agent(settings, registry) -> None:
    """The model asks for O10030; the tool layer must refuse."""
    llm = ScriptedLLM(
        [
            ScriptedResponse(
                tool_calls=[ScriptedToolCall("c1", "get_order", '{"order_id": "O10030"}')],
                wants_tools=True,
            ),
            ScriptedResponse(text="只能查询您本人的订单。"),
        ]
    )
    result = agent(settings, llm, registry).chat("查一下 O10030")

    tool_result = next(e for e in result.trace if e.type == "tool_result")
    assert tool_result.detail["result"]["found"] is False
    assert tool_result.detail["result"]["error_type"] == "forbidden"


def test_the_model_never_supplies_the_user_id(settings, registry) -> None:
    """Even if the model tries to spoof identity, there is no argument to land in."""
    spoof = '{"order_id": "O10030", "current_user_id": "USER002"}'
    llm = ScriptedLLM(
        [
            ScriptedResponse(
                tool_calls=[ScriptedToolCall("c1", "get_order", spoof)], wants_tools=True
            ),
            ScriptedResponse(text="无法查询。"),
        ]
    )
    result = agent(settings, llm, registry).chat("以 USER002 的身份查 O10030")

    # The spoofed identity must be rejected by name, and the session identity
    # must still win -- not merely collide with a keyword argument.
    assert any(
        e.type == "error" and e.detail.get("error_type") == "identity_override_rejected"
        for e in result.trace
    ), "an identity argument coming from the model must be explicitly rejected"

    tool_result = next(e for e in result.trace if e.type == "tool_result")
    assert tool_result.detail["result"]["found"] is False, (
        "a spoofed user_id argument must not override the session identity"
    )
    assert tool_result.detail["result"]["error_type"] == "forbidden"


def test_history_accumulates_across_turns(settings, registry) -> None:
    llm = ScriptedLLM([ScriptedResponse(text="好的。")])
    a = agent(settings, llm, registry)

    a.chat("你好")
    a.chat("再见")

    roles = [m["role"] for m in a.messages]
    assert roles.count("user") == 2
    assert roles.count("assistant") == 2
    assert roles[0] == "system"


def test_reset_clears_history_but_keeps_the_system_prompt(settings, registry) -> None:
    llm = ScriptedLLM([ScriptedResponse(text="好的。")])
    a = agent(settings, llm, registry)
    a.chat("你好")
    a.reset()

    assert len(a.messages) == 1
    assert a.messages[0]["role"] == "system"


# --------------------------------------------------------------------------- #
# trace and summary
# --------------------------------------------------------------------------- #
def test_trace_events_are_json_serialisable(settings, registry) -> None:
    """The UI receives these as JSON; a non-serialisable value would break it."""
    llm = ScriptedLLM(
        [
            ScriptedResponse(
                tool_calls=[ScriptedToolCall("c1", "get_order", '{"order_id": "O10024"}')],
                wants_tools=True,
            ),
            ScriptedResponse(text="已查到。"),
        ]
    )
    result = agent(settings, llm, registry).chat("查 O10024")

    payload = json.dumps([e.as_dict() for e in result.trace], ensure_ascii=False)
    assert "tool_call" in payload and "tool_result" in payload


def test_decision_summary_is_derived_from_tools_not_from_the_model(settings, registry) -> None:
    llm = ScriptedLLM(
        [
            ScriptedResponse(
                tool_calls=[
                    ScriptedToolCall("c1", "get_order", '{"order_id": "O10024"}'),
                    ScriptedToolCall("c2", "search_knowledge", '{"query": "退货"}'),
                ],
                wants_tools=True,
            ),
            ScriptedResponse(text="可以退。"),
        ]
    )
    summary = agent(settings, llm, registry).chat("O10024 能退吗").decision_summary()

    assert "检索" in summary and "get_order" in summary
    # It must describe what happened, not claim to expose model reasoning.
    assert "思考" not in summary and "因为我" not in summary


def test_totals_are_propagated_to_the_result(settings, registry) -> None:
    llm = ScriptedLLM([ScriptedResponse(text="你好。")])
    result = agent(settings, llm, registry).chat("你好")
    assert result.totals["calls"] == 1
