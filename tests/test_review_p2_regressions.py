"""Four P2 review failures: goal scope, interrupted routing, and phone PII."""

from __future__ import annotations

import json
import socket
import sqlite3
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from acs.chat_service import ChatService
from acs.cs_tools import ToolRegistry
from acs.goal_ledger import GoalLedger
from acs.intent_planner import IntentPlan, PlannedGoal
from acs.local_return_poc import LocalReturnPoc
from acs.local_return_tools import init_fixture
from acs.memory import MemoryStore
from acs.service_agent import CustomerServiceAgent
from acs.sessions import SessionStore

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall
from .test_langgraph_poc import ScriptedModel


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Review regressions cannot use external APIs")
    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.mark.parametrize("query", [
    "我要退订单O10024，先保存草稿，不要生成预览",
    "不要生成预览，但先保存订单O10024的退货草稿",
    "先保存订单O10024的退货草稿不要生成预览",
])
def test_local_preview_negation_cannot_complete_unsaved_draft(settings, query):
    plan = IntentPlan([PlannedGoal("return", .9)])
    llm = ScriptedLLM([
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall("read", "get_return_task", "{}")]),
        ScriptedResponse(text="退货草稿已保存。"),
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall("save", "update_return_task", '{"order_id":"O10024"}')]),
        ScriptedResponse(text="已保存订单，其他退货信息待补，尚未生成预览。"),
    ])
    agent = CustomerServiceAgent(
        settings=settings, llm=llm, registry=ToolRegistry(settings), goal_check=True,
        intent_planner=SimpleNamespace(plan=lambda *a, **k: plan),
    )
    service = ChatService(settings)
    final = list(service.run_turn(query, "USER001", agent=agent))[-1]
    task = service.store.get_return_task(final.session_id, "USER001")["task"]
    assert task and task["order_id"] == "O10024"
    assert [e.label for e in final.result.trace if e.type == "tool_call"] == [
        "get_return_task", "update_return_task"]
    assert any(e.type == "goal_check" for e in final.result.trace)
    assert "其他退货信息待补" in final.result.answer
    assert not final.result.errors


@pytest.mark.parametrize("query", ["不要保存草稿，只读核对当前草稿", "如果以后取消草稿会怎样",
                                  "不要保存和预览，只查草稿"])
def test_negative_or_hypothetical_write_does_not_require_a_write(query):
    ledger = GoalLedger.from_plan(IntentPlan([PlannedGoal("return")]), user_text=query)
    ledger.record("get_return_task", {"task": None})
    assert ledger.missing() == []


@pytest.mark.parametrize("query", ["不要修改草稿，查询退货记录",
                                  "不要生成预览，查看已办理的退货申请",
                                  "不要修改草稿，查看已保存的草稿",
                                  "不要生成预览，查询取消的草稿",
                                  "不要保存草稿，核对修改后的草稿",
                                  "不要修改草稿，查看之前生成的预览"])
def test_return_history_nouns_do_not_require_a_draft_write(query):
    ledger = GoalLedger.from_plan(IntentPlan([PlannedGoal("return")]), user_text=query)
    ledger.record("get_return_requests", {"requests": []})
    assert ledger.specs[0].return_tool is None
    assert ledger.missing() == []


@pytest.mark.parametrize("query", ["请记录订单O10024的退货草稿", "请办理退货草稿"])
def test_explicit_record_or_process_still_requires_a_write(query):
    ledger = GoalLedger.from_plan(IntentPlan([PlannedGoal("return")]), user_text=query)
    ledger.record("get_return_task", {"task": None})
    assert ledger.specs[0].return_tool == "update_return_task"
    assert ledger.missing() == ["return"]


@pytest.mark.parametrize("query", ["查询当前草稿后保存订单O10024", "先保存草稿并查询状态"])
def test_query_and_explicit_save_still_requires_a_write(query):
    ledger = GoalLedger.from_plan(IntentPlan([PlannedGoal("return")]), user_text=query)
    ledger.record("get_return_task", {"task": None})
    assert ledger.specs[0].return_tool == "update_return_task"
    assert ledger.missing() == ["return"]


@pytest.mark.parametrize("query", [
    "查看订单O10024，并列出店铺所有产品",
    "查看订单O10024和店铺全部商品",
    "查我的订单，另外列出所有商品",
])
def test_catalog_quantifier_does_not_require_all_orders(query):
    ledger = GoalLedger.from_plan(
        IntentPlan([PlannedGoal("orders"), PlannedGoal("product")]), user_text=query)
    ledger.record("get_order", {"found": True, "order_id": "O10024"})
    ledger.record("list_products", {"products": [], "complete_catalog": True,
                                    "query_complete": True, "has_more": False})
    assert not ledger.specs[0].require_all_orders
    assert ledger.missing() == []


def test_explicit_all_orders_still_requires_list():
    ledger = GoalLedger.from_plan(
        IntentPlan([PlannedGoal("orders"), PlannedGoal("product")]),
        user_text="列出我的全部订单和店铺所有商品")
    ledger.record("get_order", {"found": True, "order_id": "O10024"})
    assert ledger.specs[0].require_all_orders
    assert "orders" in ledger.missing()
    ledger.record("list_orders", {"orders": []})
    assert "orders" not in ledger.missing()


@pytest.mark.parametrize("phone", ["+8613800138000", "008613800138000",
                                  "+86 138-0013-8000", "13800138000", "138-0013-8000"])
def test_country_code_phone_never_enters_memory(settings, phone):
    SessionStore(settings.db_path)
    memory = MemoryStore(settings.db_path)
    memory.add_fact("USER001", "偏好开放式耳机")
    stats = memory.record_turn("USER001", "s1", "t1", "记住我的手机号是" + phone,
                               SimpleNamespace(stop_reason="answered", trace=[]))
    assert stats["facts"] == 0
    assert "开放式" in memory.context("USER001")
    assert phone not in memory.context("USER001")
    assert memory.context("USER002") == ""
    for write in [lambda: memory.add_fact("USER001", "手机号" + phone),
                  lambda: memory.upsert_summary("s1", "USER001", "手机号" + phone),
                  lambda: memory.add_episode("USER001", "s1", "t1", "手机号" + phone)]:
        with pytest.raises(ValueError, match="PII"):
            write()
    with sqlite3.connect(settings.db_path) as conn:
        for table in ["memory_facts", "memory_summaries", "memory_episodes"]:
            assert phone not in json.dumps(conn.execute(f"SELECT * FROM {table}").fetchall())


class ClarifyingPlanner:
    def __init__(self):
        self.calls = 0

    def plan(self, *args, **kwargs):
        self.calls += 1
        return IntentPlan([PlannedGoal("return", .2)], True, "您要改哪笔订单？")


@pytest.mark.parametrize("window", ["model_error", "before_graph"])
def test_clarification_survives_restart_without_another_planner_call(tmp_path, window):
    root = tmp_path / "fixture"
    init_fixture(root)
    planner = ClarifyingPlanner()
    first = ScriptedModel(responses=[RuntimeError("offline interruption")])
    with LocalReturnPoc(root, "USER001", model=first, intent_routing=True,
                        intent_planner=planner) as poc:
        sid = SessionStore(poc.settings.db_path).create("USER001")
        if window == "model_error":
            with pytest.raises(RuntimeError, match="offline interruption"):
                list(poc.stream(sid, "我要修改退货草稿"))
            assert any("澄清前不要" in str(m.content) for m in first.seen[0])
        else:
            stream = poc.stream(sid, "我要修改退货草稿")
            assert next(stream).type == "intent_plan"
            stream.close()
    # A new host, with planning disabled, must still preserve this turn's hint.
    resumed = ScriptedModel(responses=[AIMessage(content="您要改哪笔订单？"),
                                       AIMessage(content="您好。")])
    with LocalReturnPoc(root, "USER001", model=resumed) as poc:
        assert list(poc.stream(sid, None))[-1].type == "completed"
        sent = "\n".join(str(m.content) for m in resumed.seen[0])
        assert "您要改哪笔订单？" in sent
        assert "澄清前不要创建、修改或取消" in sent
        assert planner.calls == 1
        assert poc.store.get_return_task(sid, "USER001")["task"] is None
        list(poc.stream(sid, "你好"))
        assert not any("澄清前不要创建" in str(m.content) for m in resumed.seen[1])


def test_interrupted_planner_recovers_conservatively_without_retrying_it(tmp_path):
    root = tmp_path / "fixture"
    init_fixture(root)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("simulated planner process interruption")

    first = ScriptedModel(responses=[])
    with LocalReturnPoc(root, "USER001", model=first, intent_routing=True,
                        intent_planner=SimpleNamespace(plan=interrupted)) as poc:
        sid = SessionStore(poc.settings.db_path).create("USER001")
        with pytest.raises(KeyboardInterrupt):
            list(poc.stream(sid, "我要修改退货草稿"))
    resumed = ScriptedModel(responses=[AIMessage(content="请确认要修改哪笔订单。")])
    with LocalReturnPoc(root, "USER001", model=resumed) as poc:
        assert list(poc.stream(sid, None))[-1].type == "completed"
    assert first.seen == []
    assert any("澄清前不要创建、修改或取消" in str(m.content) for m in resumed.seen[0])


def test_saved_routing_cannot_be_read_through_another_users_session(tmp_path):
    root = tmp_path / "fixture"
    init_fixture(root)
    planner = ClarifyingPlanner()
    with LocalReturnPoc(root, "USER001", model=ScriptedModel(responses=[]),
                        intent_routing=True, intent_planner=planner) as poc:
        sid = SessionStore(poc.settings.db_path).create("USER001")
        stream = poc.stream(sid, "我要修改退货草稿")
        assert next(stream).type == "intent_plan"
        stream.close()
    other = ScriptedModel(responses=[])
    with LocalReturnPoc(root, "USER002", model=other) as poc, pytest.raises(PermissionError):
        list(poc.stream(sid, None))
    assert other.seen == []
