"""Fault decisions and model/tool ablation; never uses a paid endpoint."""

import json
from types import SimpleNamespace

import pytest

from acs.chat_service import ChatService
from acs.cs_tools import ToolRegistry
from acs.recovery import classify_failure, retry_delay
from acs.runtime import RunBudget
from acs.service_agent import CustomerServiceAgent

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall


@pytest.mark.parametrize(
    "status,retry", [(429, True), (503, True), (500, True), (401, False), (400, False)]
)
def test_http_failure_policy(status, retry):
    exc = RuntimeError("provider response")
    exc.status_code = status
    exc.response = SimpleNamespace(headers={"Retry-After": "2"})
    result = classify_failure(exc=exc)
    assert result["retryable"] is retry
    assert retry_delay(exc, 0) >= 2


@pytest.mark.parametrize(
    "payload,category",
    [
        ({"found": False}, "not_found"),
        ({"found": False, "error_type": "forbidden"}, "permission_denied"),
        ({"error_type": "invalid_arguments"}, "invalid_arguments"),
        ({"error_type": "business_rejected"}, "business_rejected"),
        ({"orders": [], "count": 1}, "invalid_result"),
    ],
)
def test_tool_error_policy(payload, category):
    value = classify_failure(payload=payload)
    assert value["category"] == category and not value["retryable"]


class FaultModel(ScriptedLLM):
    def __init__(self, failures, partial=False):
        super().__init__([ScriptedResponse(text="恢复后的完整回答")])
        self.failures = failures
        self.partial = partial
        self.attempts = 0

    def chat_stream(self, messages, **kwargs):
        self.attempts += 1
        if self.budget:
            self.budget.request()
        if self.attempts <= self.failures:
            if self.partial:
                yield "已展示半句"
            exc = RuntimeError("temporary HTTP error")
            exc.status_code = 503
            raise exc
        yield from super().chat_stream(messages, **kwargs)


@pytest.mark.parametrize("enabled", [False, True])
def test_retry_before_content_only(settings, enabled, monkeypatch):
    monkeypatch.setattr("acs.service_agent.sleep", lambda delay: None)
    settings = settings.model_copy(update={"runtime_recovery_enabled": enabled})
    model = FaultModel(1)
    agent = CustomerServiceAgent(settings=settings, llm=model, registry=ToolRegistry(settings))
    final = list(ChatService(settings).run_turn("问候", "USER001", agent=agent))[-1]
    assert model.attempts == (2 if enabled else 1)
    assert final.result.stop_reason == ("answered" if enabled else "llm_error")
    if enabled:
        assert final.result.totals["budget"]["api_requests"] == 2
        assert any(e.type == "retry_scheduled" for e in final.result.trace)


def test_partial_stream_never_retried(settings, monkeypatch):
    monkeypatch.setattr("acs.service_agent.sleep", lambda delay: None)
    settings = settings.model_copy(update={"runtime_recovery_enabled": True})
    model = FaultModel(1, partial=True)
    agent = CustomerServiceAgent(settings=settings, llm=model, registry=ToolRegistry(settings))
    final = list(agent.chat_stream("问候", budget=RunBudget(5, 3)))[-1]
    assert model.attempts == 1 and final.stop_reason == "llm_error"
    assert "未完成" in final.answer


@pytest.mark.parametrize("changing,expected", [(False, "no_progress"), (True, "answered")])
def test_no_progress_uses_normalized_arguments_and_result(settings, changing, expected):
    settings = settings.model_copy(update={"runtime_recovery_enabled": True, "max_steps": 6})
    responses = [
        ScriptedResponse(
            wants_tools=True,
            tool_calls=[
                ScriptedToolCall(
                    str(i),
                    "get_order",
                    json.dumps({"order_id": "O10024" if not changing or i % 2 else "O10023"}),
                )
            ],
        )
        for i in range(4)
    ] + [ScriptedResponse(text="查询完成")]
    model = ScriptedLLM(responses)
    agent = CustomerServiceAgent(settings=settings, llm=model, registry=ToolRegistry(settings))
    final = list(agent.chat_stream("查询", budget=RunBudget(15, 8)))[-1]
    assert final.stop_reason == expected
    assert len(model.calls) == (3 if not changing else 5)


def test_retry_budget_and_exhaustion(settings, monkeypatch):
    monkeypatch.setattr("acs.service_agent.sleep", lambda delay: None)
    settings = settings.model_copy(update={"runtime_recovery_enabled": True})
    for max_requests, expected in [(1, "budget_exceeded"), (4, "retry_exhausted")]:
        model = FaultModel(3)
        agent = CustomerServiceAgent(settings=settings, llm=model, registry=ToolRegistry(settings))
        final = list(agent.chat_stream("问候", budget=RunBudget(5, max_requests)))[-1]
        assert final.stop_reason == expected
        assert model.attempts <= 2


@pytest.mark.parametrize("read_fails", [False, True])
def test_committed_write_with_lost_response_is_reconciled_not_replayed(
    settings, monkeypatch, read_fails
):
    from acs.sessions import SessionStore

    settings = settings.model_copy(update={"runtime_recovery_enabled": True})
    store = SessionStore(settings.db_path)
    original = store.update_return_task
    writes = []

    def lost(*args, **kwargs):
        writes.append(1)
        original(*args, **kwargs)
        raise RuntimeError("response lost after commit")

    monkeypatch.setattr(store, "update_return_task", lost)
    read = store.get_return_task
    reads = []

    def observed(*args, **kwargs):
        reads.append(1)
        if read_fails and len(reads) > 1:
            raise OSError("read unavailable")
        return read(*args, **kwargs)

    monkeypatch.setattr(store, "get_return_task", observed)
    model = ScriptedLLM(
        [
            ScriptedResponse(
                wants_tools=True,
                tool_calls=[
                    ScriptedToolCall("write", "update_return_task", '{"order_id":"O10024"}')
                ],
            ),
            ScriptedResponse(text="退款已到账"),
        ]
    )
    agent = CustomerServiceAgent(settings=settings, llm=model, registry=ToolRegistry(settings))
    final = list(ChatService(settings, store).run_turn("我想退O10024", "USER001", agent=agent))[-1]
    assert len(writes) == 1
    assert "退款已到账" not in final.result.answer
    assert final.result.stop_reason == ("state_uncertain" if read_fails else "tool_error")
    assert store.load(final.session_id, "USER001")["messages"][-1]["content"] == final.result.answer
    if not read_fails:
        assert final.task_view["task"]["order_id"] == "O10024"
        assert final.task_view["task"]["revision"] == 1


def test_real_client_counts_every_failed_attempt_and_unknown_usage(settings, monkeypatch):
    from acs.llm import LLMClient

    settings = settings.model_copy(
        update={"runtime_recovery_enabled": True, "llm_api_key": "local-test-only"}
    )
    client = LLMClient(settings)
    attempts = []

    def provider(kwargs, remaining):
        attempts.append(1)
        if len(attempts) == 1:
            exc = RuntimeError("injected 429")
            exc.status_code = 429
            raise exc
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content="恢复完成",
                        tool_calls=[],
                        reasoning_content="hidden-thought-sentinel",
                    ),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=5, total_tokens=17),
        )

    monkeypatch.setattr(client, "_async_chat", provider)
    monkeypatch.setattr("acs.service_agent.sleep", lambda delay: None)
    agent = CustomerServiceAgent(settings=settings, llm=client, registry=ToolRegistry(settings))
    final = list(agent.chat_stream("问候", budget=RunBudget(5, 3)))[-1]
    assert len(attempts) == final.totals["calls"] == 2
    assert final.totals["tokens_are_partial"] and final.totals["cost_is_partial"]
    assert final.stop_reason == "answered" and final.answer == "恢复完成"
    assert "hidden-thought-sentinel" not in json.dumps(final.trace, default=str)


def test_retry_after_longer_than_remaining_budget_never_sleeps(settings, monkeypatch):
    model = FaultModel(5)
    settings = settings.model_copy(update={"runtime_recovery_enabled": True})
    waits = []
    monkeypatch.setattr("acs.service_agent.retry_delay", lambda *args: 120)
    monkeypatch.setattr("acs.service_agent.sleep", waits.append)
    agent = CustomerServiceAgent(settings=settings, llm=model, registry=ToolRegistry(settings))
    final = list(agent.chat_stream("问候", budget=RunBudget(5, 3)))[-1]
    assert final.stop_reason == "budget_exceeded" and not waits and model.attempts == 1


def test_repeated_task_reads_ignore_observation_clock(settings):
    settings = settings.model_copy(update={"runtime_recovery_enabled": True})
    model = ScriptedLLM(
        [
            ScriptedResponse(
                wants_tools=True,
                tool_calls=[
                    ScriptedToolCall(str(i), "get_return_task", "{}"),
                ],
            )
            for i in range(4)
        ]
        + [ScriptedResponse(text="读取结束")]
    )
    final = list(
        ChatService(settings).run_turn(
            "查看草稿",
            "USER001",
            agent=(
                CustomerServiceAgent(settings=settings, llm=model, registry=ToolRegistry(settings))
            ),
        )
    )[-1]
    assert final.result.stop_reason == "no_progress"
    assert final.result.tool_calls == 3


def test_changed_result_for_same_arguments_is_real_progress(settings):
    settings = settings.model_copy(update={"runtime_recovery_enabled": True})
    registry = ToolRegistry(settings)
    ticks = iter(range(10))
    registry._tools["get_order"].func = lambda **kwargs: {"state_revision": next(ticks)}
    model = ScriptedLLM(
        [
            ScriptedResponse(
                wants_tools=True,
                tool_calls=[
                    ScriptedToolCall(str(i), "get_order", '{"order_id":"O10024"}'),
                ],
            )
            for i in range(4)
        ]
        + [ScriptedResponse(text="读取结束")]
    )
    final = list(
        CustomerServiceAgent(settings=settings, llm=model, registry=registry).chat_stream(
            "查看订单",
            budget=RunBudget(10, 6),
        )
    )[-1]
    assert final.stop_reason == "answered" and final.tool_calls == 4


def test_direct_unbudgeted_api_does_not_schedule_automatic_retries(settings, monkeypatch):
    class Broken(ScriptedLLM):
        def chat(self, *args, **kwargs):
            exc = RuntimeError("injected 503")
            exc.status_code = 503
            raise exc

    waits = []
    monkeypatch.setattr("acs.service_agent.sleep", waits.append)
    settings = settings.model_copy(update={"runtime_recovery_enabled": True})
    final = CustomerServiceAgent(
        settings=settings, llm=Broken([]), registry=ToolRegistry(settings)
    ).chat("查询")
    assert final.stop_reason == "llm_error" and not waits


def test_non_object_tool_result_is_classified_without_crashing(settings):
    settings = settings.model_copy(update={"runtime_recovery_enabled": True})
    registry = ToolRegistry(settings)
    registry._tools["get_order"].func = lambda **kwargs: ["invalid envelope"]
    model = ScriptedLLM(
        [
            ScriptedResponse(
                wants_tools=True,
                tool_calls=[
                    ScriptedToolCall("bad", "get_order", '{"order_id":"O10024"}'),
                ],
            ),
            ScriptedResponse(text="结果未核实"),
        ]
    )
    result = list(
        CustomerServiceAgent(settings=settings, llm=model, registry=registry).chat_stream(
            "查询",
            budget=RunBudget(5, 3),
        )
    )[-1]
    assert any(
        e.type == "runtime_failure" and e.detail["category"] == "invalid_result"
        for e in result.trace
    )
    assert result.answer == "结果未核实"
