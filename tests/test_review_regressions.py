"""Adversarial regressions from the d309feb review; no model network."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from acs.cs_tools import ToolRegistry
from acs.goal_ledger import GoalLedger
from acs.intent_planner import IntentPlan, IntentPlanner, PlannedGoal
from acs.memory import MemoryStore
from acs.service_agent import CustomerServiceAgent
from acs.sessions import SessionStore

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall


@pytest.mark.parametrize("raw", [None, [], "not JSON", "{}", '{"goals":"orders"}',
                                      '{"goals":[{"intent":[]}]}'])
def test_invalid_plan_restores_completion_check(settings, raw):
    planner_llm = SimpleNamespace(complete=lambda *a, **k: SimpleNamespace(text=raw))
    llm = ScriptedLLM([ScriptedResponse(text="已经查完。")])
    agent = CustomerServiceAgent(
        settings=settings, llm=llm, registry=ToolRegistry(settings), goal_check=True,
        intent_planner=IntentPlanner(planner_llm),
    )
    result = agent.chat("查我的订单，并说明退货政策期限")
    assert "missing_goals:orders,policy" in result.errors
    assert len(llm.calls) == 3
    assert any(e.type == "intent_plan" and e.detail["source"] == "planner_invalid"
               for e in result.trace)


@pytest.mark.parametrize("confidence", ['"NaN"', '"Infinity"', 'true', 'null', '-1', '2'])
def test_malformed_confidence_cannot_become_high_confidence(confidence):
    raw = ('{"goals":[{"intent":"return","confidence":' + confidence
           + '}],"needs_clarification":false}')
    llm = SimpleNamespace(complete=lambda *a, **k: SimpleNamespace(text=raw))
    plan = IntentPlanner(llm).plan("修改退货草稿")
    assert plan.source == "planner_invalid"
    assert plan.goals == []


def test_planner_preserves_catalog_completeness_and_accepts_empty_complete_query():
    plan = IntentPlan([PlannedGoal("orders"), PlannedGoal("product")])
    ledger = GoalLedger.from_plan(plan, user_text="列出我的全部订单和店里所有产品")
    ledger.record("list_orders", {"orders": []})
    ledger.record("list_products", {"products": [{"product_id": "P1"}],
                                  "has_more": True, "complete_catalog": False,
                                  "query_complete": False})
    assert ledger.missing() == ["product"]
    ledger.record("list_products", {"products": [], "complete_catalog": True,
                                  "query_complete": True, "has_more": False})
    assert ledger.missing() == []


def test_business_denial_is_not_evidence_for_other_goals():
    ledger = GoalLedger.from_plan(IntentPlan([
        PlannedGoal("orders"), PlannedGoal("logistics"), PlannedGoal("price"),
    ]))
    ledger.record("get_order", {"found": False, "error_type": "forbidden"})
    assert ledger.missing() == ["orders", "logistics", "price"]
    ledger.record("get_order", {"found": True, "order_id": "O1", "items": []})
    assert ledger.missing() == ["logistics", "price"]


def test_normal_absent_logistics_resolves_query_without_inventing_events():
    ledger = GoalLedger.from_plan(IntentPlan([PlannedGoal("logistics")]))
    ledger.record("get_logistics", {"found": False, "reason": "无信息"})
    assert ledger.missing() == ["logistics"]  # an unbound negative is not evidence
    ledger.record("get_logistics", {"found": False, "order_id": "O10026",
                                    "order_status": "pending", "reason": "本人订单暂未发货"})
    assert ledger.missing() == []


def test_complete_filtered_page_chain_is_recognised(settings):
    tools = ToolRegistry(settings)
    ledger = GoalLedger.from_plan(IntentPlan([PlannedGoal("product")]))
    args = {"category": "无线耳机", "page_size": 1}
    first, _, error = tools.run("list_products", json.dumps(args), current_user_id="USER001")
    ledger.record("list_products", first, error, arguments=args)
    assert ledger.missing() == ["product"]
    args = first["next_arguments"]
    last, _, error = tools.run("list_products", json.dumps(args), current_user_id="USER001")
    assert last["has_more"] is False and last["query_complete"] is False
    ledger.record("list_products", last, error, arguments=args)
    assert ledger.missing() == []

    # Last page alone never proves the preceding products were obtained.
    missing_first = GoalLedger.from_plan(IntentPlan([PlannedGoal("product")]))
    missing_first.record("list_products", last, arguments=args)
    assert missing_first.missing() == ["product"]


def test_failed_preview_cannot_complete_a_requested_preview():
    ledger = GoalLedger.from_plan(IntentPlan([PlannedGoal("return")]),
                                  user_text="生成退货预览")
    ledger.record("get_return_task", {"task": None})
    ledger.record("preview_return", {"ready": False, "reasons": ["缺少商品"]})
    assert ledger.missing() == ["return"]
    ledger.record("preview_return", {"ready": True, "preview_id": "p1"})
    assert ledger.missing() == []


def test_empty_draft_query_cannot_complete_requested_write():
    ledger = GoalLedger.from_plan(IntentPlan([PlannedGoal("return")]),
                                  user_text="我要退货，先保存草稿")
    ledger.record("get_return_task", {"task": None})
    assert ledger.missing() == ["return"]
    ledger.record("update_return_task", {"task": {"status": "draft", "order_id": "O1"}})
    assert ledger.missing() == []


def test_corrected_final_does_not_republish_early_wrong_answer(settings):
    llm = ScriptedLLM([
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall("o", "list_orders", "{}")]),
        ScriptedResponse(text="本店只有1件商品。"),
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall("p", "list_products", '{"view":"overview"}')]),
        ScriptedResponse(text="订单已查询；更正：本店有4件商品。"),
    ])
    agent = CustomerServiceAgent(settings=settings, llm=llm, registry=ToolRegistry(settings),
                                 goal_check=True)
    result = agent.chat("我的全部订单和店铺所有产品")
    assert "本店有4件商品" in result.answer
    assert "只有1件商品" not in result.answer
    assert not result.errors


@pytest.fixture()
def memory(settings):
    SessionStore(settings.db_path)
    return MemoryStore(settings.db_path)


def terminal():
    return SimpleNamespace(stop_reason="answered", trace=[
        SimpleNamespace(type="tool_result", label="get_order")])


def test_delete_and_expiry_apply_to_all_injected_memory(memory):
    memory.record_turn("USER001", "s1", "t1", "记住我偏好入耳式耳机", terminal())
    assert "入耳式" in memory.context("USER001")
    # Repeating a fact in another session must not leave an older derived copy.
    memory.record_turn("USER001", "s2", "t2", "记住我偏好入耳式耳机", terminal())
    fact = memory.list_facts("USER001")[0]
    assert memory.delete_fact("USER001", fact.fact_id)
    assert "入耳式" not in memory.context("USER001")
    memory.record_turn("USER001", "s3", "t3", "记住我偏好开放式耳机", terminal())
    assert "开放式" in memory.context("USER001")
    with sqlite3.connect(memory.db_path) as conn:
        conn.execute("UPDATE memory_facts SET expires_at='2000-01-01T00:00:00+00:00'")
        conn.execute("UPDATE memory_episodes SET expires_at='2000-01-01T00:00:00+00:00'")
    assert "开放式" not in memory.context("USER001")


def test_legacy_derived_copies_are_retained_but_never_injected(memory):
    with sqlite3.connect(memory.db_path) as conn:
        conn.execute("INSERT INTO memory_summaries "
                     "(session_id,user_id,summary,updated_at) VALUES(?,?,?,?)",
                     ("legacy", "USER001", "旧摘要的已删除偏好", "2099-01-01"))
        conn.execute("INSERT INTO memory_episodes "
                     "(episode_id,user_id,session_id,summary,created_at,expires_at) "
                     "VALUES(?,?,?,?,?,?)",
                     ("legacy", "USER001", "legacy", "旧情节的已删除偏好", "2099", "2099"))
    assert memory.get_summary("legacy", "USER001") == ""
    assert "已删除偏好" not in memory.context("USER001")
    with sqlite3.connect(memory.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM memory_summaries").fetchone()[0] == 1
    memory.upsert_summary("fresh", "USER001", "已查询订单")
    assert "已查询订单" in memory.context("USER001")
    with sqlite3.connect(memory.db_path) as conn:
        conn.execute("UPDATE memory_summaries SET updated_at='2000-01-01'")
    assert memory.get_summary("fresh", "USER001") == ""
    assert "已查询订单" not in memory.context("USER001")


@pytest.mark.parametrize("phone", ["138-0013-8000", "138 0013 8000", "+86 138 0013 8000"])
def test_grouped_phone_is_not_persisted_or_injected(memory, phone):
    assert memory.add_fact("USER001", "偏好开放式耳机")
    memory.record_turn("USER001", "s1", "t1", "记住我的手机号是" + phone, terminal())
    context = memory.context("USER001")
    assert "开放式" in context
    assert phone not in context
    with sqlite3.connect(memory.db_path) as conn:
        for table in ("memory_facts", "memory_summaries", "memory_episodes"):
            assert phone not in json.dumps(conn.execute(f"SELECT * FROM {table}").fetchall())


def test_rule_metrics_do_not_depend_on_planner_clarification():
    from scripts.eval_intent_planner import _metrics

    row = {"required": ["policy"], "rule": ["return"], "planner": [],
           "planned_clarify": True, "clarify": False}
    assert _metrics([row], "rule")["write_false_positive"] == 1
    assert _metrics([row], "rule")["unnecessary_clarification"] == 0
    before = _metrics([row], "rule")
    row["planned_clarify"] = False
    assert _metrics([row], "rule") == before


def test_real_gradio_factory_enables_planner_and_goal_check(settings, monkeypatch):
    import app

    from acs.llm import LLMClient

    monkeypatch.setattr(app, "SETTINGS", settings)
    monkeypatch.setattr(LLMClient, "__post_init__", lambda self: None)
    agent = app.ensure_agent({}, app.USER_CHOICES[0])
    assert agent.goal_check
    assert agent.intent_planner is not None


def test_actual_gradio_send_plans_and_retries_missing_goal(settings, monkeypatch):
    import app

    from acs.llm import LLMClient

    monkeypatch.setattr(app, "SETTINGS", settings)
    monkeypatch.setattr(LLMClient, "__post_init__", lambda self: None)
    planner_calls = []

    def complete(self, prompt, **kwargs):
        planner_calls.append(prompt)
        return SimpleNamespace(text=json.dumps({"goals": [
            {"intent": "orders", "confidence": .9},
            {"intent": "product", "confidence": .9}], "needs_clarification": False}))

    llm = ScriptedLLM([
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall("orders", "list_orders", "{}")]),
        ScriptedResponse(text="只回答订单。"),
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall("catalog", "list_products", '{"view":"overview"}')]),
        ScriptedResponse(text="订单已查询，商品概览有4件。"),
    ])
    monkeypatch.setattr(LLMClient, "complete", complete)
    monkeypatch.setattr(LLMClient, "chat_stream", lambda self, *a, **k: llm.chat_stream(*a, **k))
    state = {}
    final = list(app.on_send("我的全部订单和店铺所有产品", [], state, app.USER_CHOICES[0]))[-1]
    assert len(planner_calls) == 1
    assert "商品概览有4件" in final[0][-1]["content"]
    saved = app.session_store().load(state["session_id"], "USER001")["last_result"]
    assert saved["stop_reason"] == "answered"
    assert any(e["type"] == "intent_plan" for e in saved["trace"])
    assert any(e["type"] == "goal_check" for e in saved["trace"])
