"""Bounded multi-goal detection and the runtime retry it triggers."""

from __future__ import annotations

import pytest

from acs.cs_tools import ToolRegistry
from acs.goal_ledger import GoalLedger, detect_goals
from acs.rag import KnowledgeBase
from acs.service_agent import CustomerServiceAgent

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall


@pytest.fixture()
def registry(settings, fake_embedder):
    kb = KnowledgeBase(settings, llm=fake_embedder)
    kb.build()
    return ToolRegistry(settings, kb=kb)


def test_order_plus_catalog_is_detected_as_two_goals():
    specs = detect_goals("我目前都有哪些订单，然后我们这个店都有哪些产品")
    assert [spec.name for spec in specs] == ["orders", "catalog"]
    assert specs[0].require_all_orders is True
    assert specs[1].require_complete_catalog is True


def test_order_count_is_not_a_catalog_goal():
    assert detect_goals("我的订单一共买了多少件商品？只给总件数。") == []


def test_order_line_items_are_not_the_shop_catalog():
    assert detect_goals("请列出我的全部订单和全部商品明细。") == []


def test_orders_plus_policy_is_detected():
    specs = detect_goals("查我的订单，并说明退货政策期限")
    assert [spec.name for spec in specs] == ["orders", "policy"]


def test_first_catalog_page_does_not_satisfy_complete_catalog():
    ledger = GoalLedger("我目前都有哪些订单，然后我们这个店都有哪些产品")
    ledger.record("list_orders", {"orders": [{"order_id": "O1"}]})
    ledger.record("list_products", {"products": [{"product_id": "P1"}], "complete_catalog": False})
    assert ledger.missing() == ["catalog"]
    assert "list_products" in ledger.retry_prompt()


def test_agent_retries_once_and_answers_both_goals(settings, registry):
    llm = ScriptedLLM(
        [
            ScriptedResponse(
                wants_tools=True,
                tool_calls=[ScriptedToolCall("orders", "list_orders", "{}")],
            ),
            ScriptedResponse(text="您目前有 25 笔订单。"),
            ScriptedResponse(
                wants_tools=True,
                tool_calls=[ScriptedToolCall("products", "list_products", '{"view": "overview"}')],
            ),
            ScriptedResponse(text="本店目前有 4 件商品。"),
        ]
    )
    agent = CustomerServiceAgent(
        settings=settings,
        llm=llm,
        registry=registry,
        current_user_id="USER001",
        goal_check=True,
    )
    result = agent.chat("我目前都有哪些订单，然后我们这个店都有哪些产品")

    assert result.stop_reason == "answered"
    assert "25 笔订单" in result.answer
    assert "4 件商品" in result.answer
    assert [event.label for event in result.trace if event.type == "tool_call"] == [
        "list_orders",
        "list_products",
    ]
    assert any(
        event.type == "goal_check" and event.label == "missing_goals" for event in result.trace
    )
    assert len(llm.calls) == 4


def test_agent_stops_after_bounded_retries_without_claiming_completion(settings, registry):
    llm = ScriptedLLM(
        [
            ScriptedResponse(
                wants_tools=True,
                tool_calls=[ScriptedToolCall("orders", "list_orders", "{}")],
            ),
            ScriptedResponse(text="订单已经查到了。"),
            ScriptedResponse(text="商品部分下次再看。"),
            ScriptedResponse(text="还是只回答订单。"),
        ]
    )
    agent = CustomerServiceAgent(
        settings=settings,
        llm=llm,
        registry=registry,
        current_user_id="USER001",
        goal_check=True,
    )
    result = agent.chat("我目前都有哪些订单，然后我们这个店都有哪些产品")

    assert result.stop_reason == "answered"
    assert "还缺少：商品概览" in result.answer
    assert "missing_goals:catalog" in result.errors
    assert len(llm.calls) == 4


def test_single_goal_question_does_not_trigger_goal_retry(settings, registry):
    llm = ScriptedLLM([ScriptedResponse(text="一共 29 件。")])
    agent = CustomerServiceAgent(
        settings=settings,
        llm=llm,
        registry=registry,
        current_user_id="USER001",
        goal_check=True,
    )
    result = agent.chat("我的订单一共买了多少件商品？只给总件数。")

    assert result.stop_reason == "answered"
    assert len(llm.calls) == 1
    assert not any(event.type == "goal_check" for event in result.trace)
