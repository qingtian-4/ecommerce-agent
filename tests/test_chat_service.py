"""The UI and evaluations must share persistence, streaming and context limits."""

import json
from contextlib import closing
from threading import BoundedSemaphore

import pytest

from acs.chat_service import ChatService
from acs.context import ContextLimitExceeded, bounded_context
from acs.cs_tools import ToolRegistry
from acs.service_agent import CustomerServiceAgent
from acs.sessions import ConversationBusy, SessionStore

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall


def make_agent(settings, responses):
    return CustomerServiceAgent(
        settings=settings, llm=ScriptedLLM(responses), registry=ToolRegistry(settings),
    )


def test_context_keeps_whole_recent_turn_and_required_task():
    pair = [
        {"role": "user", "content": "查这单"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c"}]},
        {"role": "tool", "tool_call_id": "c", "content": '{"order_id":"O10024"}'},
        {"role": "assistant", "content": "已签收"},
    ]
    old = [{"role": "user", "content": "旧话" * 1000},
           {"role": "assistant", "content": "旧回答"}]
    required = [{"role": "system", "content": "当前退货草稿 O10024 数量1"}]
    selected, stats = bounded_context(required, old + pair, "只退一个", [], 600)
    assert selected == required + pair
    assert stats["dropped_turns"] == 1 and stats["retained_turns"] == 1
    assert stats["input_chars"] <= 600
    assert old[0]["content"] == "旧话" * 1000


def test_mandatory_context_is_rejected_instead_of_truncating_user_or_task():
    with pytest.raises(ContextLimitExceeded):
        bounded_context([{"role": "system", "content": "规则"}], [], "问题" * 100, [], 80)


def test_stream_and_new_service_restore_tool_pairs_without_browser_history(settings):
    service = ChatService(settings)
    agent = make_agent(settings, [ScriptedResponse(wants_tools=True, tool_calls=[
        ScriptedToolCall("order", "get_order", '{"order_id":"O10024"}'),
    ]), ScriptedResponse(text="已查到本人订单")])
    updates = list(service.run_turn("查订单O10024", "USER001", agent=agent))
    sid = updates[-1].session_id
    assert updates[0].phase == "running"
    assert any(u.result.tool_calls == 1 and u.phase == "running" for u in updates)
    assert updates[-1].history[-1]["content"] == "已查到本人订单"
    next_agent = make_agent(settings, [ScriptedResponse(text="仍需重新查当前状态")])
    list(ChatService(settings).run_turn("继续", "USER001", sid, agent=next_agent))
    sent = next_agent.llm.calls[0]["messages"]
    assert any(m.get("tool_call_id") == "order" for m in sent)
    assert "可能已经变化" in json.dumps(sent, ensure_ascii=False)
    assert SessionStore(settings.db_path).load(sid, "USER001")["running"] is False
    with pytest.raises(PermissionError):
        list(service.run_turn("继续", "USER002", sid, agent=next_agent))


def test_queue_is_visible_same_session_rejected_and_close_releases_reservation(settings):
    gate = BoundedSemaphore(1)
    gate.acquire()
    service = ChatService(settings, limiter=gate)
    agent = make_agent(settings, [ScriptedResponse(text="完成")])
    with closing(service.run_turn("排队问题", "USER001", agent=agent)) as stream:
        queued = next(stream)
        assert queued.phase == "queued" and not agent.llm.calls
        with pytest.raises(ConversationBusy):
            list(service.run_turn("重复发送", "USER001", queued.session_id, agent=agent))
    gate.release()
    snapshot = service.store.load(queued.session_id, "USER001")
    assert snapshot["last_result"]["stop_reason"] == "interrupted"
    assert not snapshot["running"]
    assert list(service.run_turn("重试", "USER001", queued.session_id,
                                 agent=agent))[-1].result.stop_reason == "answered"


def test_context_compaction_leaves_all_original_history_in_database(settings):
    settings = settings.model_copy(update={"context_max_chars": 15000})
    service = ChatService(settings)
    sid = service.store.create("USER001")
    for i in range(5):
        query = f"历史{i}" + "历史内容" * 1300
        tid = service.store.begin_turn(sid, "USER001", query)
        service.store.finish_turn(sid, "USER001", tid, [
            {"role": "user", "content": query}, {"role": "assistant", "content": "旧回答"},
        ], {"answer": "旧回答"})
    agent = make_agent(settings, [ScriptedResponse(text="当前问题回答")])
    final = list(service.run_turn("当前问题", "USER001", sid, agent=agent))[-1]
    assert final.result.totals["context"]["dropped_turns"] > 0
    assert len(service.store.load(sid, "USER001")["history"]) == 12
    assert "历史0" not in json.dumps(agent.llm.calls[0]["messages"], ensure_ascii=False)


def test_two_active_sessions_and_a_visible_third_queue_release_cleanly(settings):
    service = ChatService(settings, limiter=BoundedSemaphore(2))
    streams = [service.run_turn(f"会话{i}", "USER001", agent=make_agent(
        settings, [ScriptedResponse(text="完成")],
    )) for i in range(3)]
    try:
        first, second, third = [next(s) for s in streams]
        assert [first.phase, second.phase, third.phase] == ["running", "running", "queued"]
        streams[0].close()
        assert list(streams[2])[-1].result.stop_reason == "answered"
        assert service.store.load(first.session_id, "USER001")["last_result"]["stop_reason"] == (
            "interrupted"
        )
    finally:
        for stream in streams:
            stream.close()


def test_load_failure_after_reserving_a_turn_does_not_leave_it_running(settings, monkeypatch):
    service = ChatService(settings)
    sid = service.store.create("USER001")

    def fail(*args):
        raise ValueError("broken task data")

    monkeypatch.setattr(service.store, "get_return_task", fail)
    with pytest.raises(ValueError, match="broken task"):
        list(service.run_turn("问题", "USER001", sid))
    assert service.store.load(sid, "USER001")["running"] is False
