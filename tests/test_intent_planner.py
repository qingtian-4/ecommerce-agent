"""RAG planner and planner-derived multi-goal checks; no network or API key."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from acs.cs_tools import ToolRegistry
from acs.goal_ledger import GoalLedger
from acs.intent_planner import IntentPlan, IntentPlanner, IntentRegistry, PlannedGoal
from acs.rag import KnowledgeBase
from acs.service_agent import CustomerServiceAgent

from .conftest import ScriptedLLM, ScriptedResponse


@pytest.fixture()
def tools(settings, fake_embedder):
    kb = KnowledgeBase(settings, llm=fake_embedder)
    kb.build()
    return ToolRegistry(settings, kb=kb)


class FakePlanner:
    def __init__(self, goals, *, clarify=False, question=None):
        self.plan_value = IntentPlan(
            goals=goals,
            needs_clarification=clarify,
            clarify_question=question,
        )

    def plan(self, text, *, history=None):
        return self.plan_value


def _tool_names(call):
    return {tool["function"]["name"] for tool in call["tools"]}


def test_registry_retrieves_return_action_and_policy_boundaries():
    registry = IntentRegistry.load()
    hits = registry.retrieve("我要退款")
    names = {hit["intent"] for hit in hits}
    assert "return" in names
    assert "policy" in names


def test_planner_parses_multi_goal_json():
    class FakeLLM:
        def complete(self, *args, **kwargs):
            return SimpleNamespace(
                text='{"goals":[{"intent":"orders","confidence":0.9},'
                '{"intent":"policy","confidence":0.8}],'
                '"needs_clarification":false,"clarify_question":null}'
            )

    plan = IntentPlanner(FakeLLM()).plan("查订单，顺便说退货期限")
    assert plan.names == ["orders", "policy"]
    assert not plan.needs_clarification
    assert "订单查询" in plan.to_hint()


def test_planner_asks_for_clarification_on_low_confidence():
    class FakeLLM:
        def complete(self, *args, **kwargs):
            return SimpleNamespace(
                text='{"goals":[{"intent":"return","confidence":0.3}],'
                '"needs_clarification":false,"clarify_question":"您要退哪一笔？"}'
            )

    plan = IntentPlanner(FakeLLM()).plan("那个退一下")
    assert plan.needs_clarification
    assert "退哪一笔" in plan.to_hint()


def test_soft_planner_keeps_every_tool_but_records_goals(settings, tools):
    llm = ScriptedLLM([ScriptedResponse(text="订单和政策分别回答。")])
    agent = CustomerServiceAgent(
        settings=settings,
        llm=llm,
        registry=tools,
        current_user_id="USER001",
        intent_planner=FakePlanner([PlannedGoal("orders", 0.9), PlannedGoal("policy", 0.9)]),
    )
    result = agent.chat("查我的订单，并说明退货政策期限")

    assert _tool_names(llm.calls[0]) == set(tools.names)
    assert any(event.type == "intent_plan" for event in result.trace)
    assert any(
        event.type == "intent_plan" and event.label == "orders+policy" for event in result.trace
    )


def test_planner_clarification_keeps_tools_and_does_not_create_ledger(settings, tools):
    llm = ScriptedLLM([ScriptedResponse(text="您要退哪一笔？")])
    agent = CustomerServiceAgent(
        settings=settings,
        llm=llm,
        registry=tools,
        current_user_id="USER001",
        goal_check=True,
        intent_planner=FakePlanner(
            [PlannedGoal("return", 0.9)],
            clarify=True,
            question="您要退哪一笔？",
        ),
    )
    result = agent.chat("那个退一下")

    assert _tool_names(llm.calls[0]) == set(tools.names)
    assert any("歧义" in message.get("content", "") for message in llm.calls[0]["messages"])
    assert not any(event.type == "goal_missing" for event in result.trace)


def test_goal_ledger_from_plan_tracks_six_goal_types():
    plan = IntentPlan(
        goals=[
            PlannedGoal("orders"),
            PlannedGoal("logistics"),
            PlannedGoal("product"),
            PlannedGoal("policy"),
            PlannedGoal("return"),
            PlannedGoal("price"),
        ]
    )
    ledger = GoalLedger.from_plan(plan)
    ledger.record("list_orders", {"orders": [{"order_id": "O1"}]})
    ledger.record("get_logistics", {"events": [{"status": "in_transit"}]})
    ledger.record("get_product", {"product": {"product_id": "B1"}})
    ledger.record("search_knowledge", {"found": True, "documents": [{"id": "d"}]})
    ledger.record("get_return_task", {"task": {"task_id": "t"}})
    ledger.record("get_price_history", {"history": [{"price": 1}]})
    assert ledger.missing() == []
