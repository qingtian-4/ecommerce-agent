"""Rule intent routes and their tool-schema policy in the agent loop."""

from __future__ import annotations

import pytest

from acs.cs_tools import ToolRegistry
from acs.intent_router import IntentRouter
from acs.rag import KnowledgeBase
from acs.service_agent import CustomerServiceAgent

from .conftest import ScriptedLLM, ScriptedResponse


@pytest.fixture()
def registry(settings, fake_embedder):
    kb = KnowledgeBase(settings, llm=fake_embedder)
    kb.build()
    return ToolRegistry(settings, kb=kb)


def test_order_plus_product_is_multi_route():
    decision = IntentRouter().route("我目前都有哪些订单，然后我们这个店都有哪些产品")
    assert decision.names == ["orders", "product"]
    assert "list_orders" in decision.tools
    assert "list_products" in decision.tools
    assert "preview_return" not in decision.tools
    assert "订单查询" in decision.labels and "商品咨询" in decision.labels


def test_order_plus_policy_does_not_open_a_write_route():
    decision = IntentRouter().route("查我的订单，并说明退货政策期限")
    assert decision.names == ["orders", "policy"]
    assert "list_orders" in decision.tools
    assert "search_knowledge" in decision.tools
    assert "preview_return" not in decision.tools
    assert "update_return_task" not in decision.tools


def test_explicit_return_action_routes_to_return_tools():
    decision = IntentRouter().route("帮我退货，订单 O72001")
    assert decision.names == ["orders", "return"]
    assert "preview_return" in decision.tools
    assert "update_return_task" in decision.tools


def test_product_spec_plus_eligibility_is_product_and_policy():
    decision = IntentRouter().route("这款耳机降噪怎么样，能退吗")
    assert decision.names == ["product", "policy"]
    assert "get_product" in decision.tools
    assert "search_knowledge" in decision.tools
    assert "preview_return" not in decision.tools


def test_logistics_route_is_separate_from_orders():
    decision = IntentRouter().route("我的快递到哪了，签收了吗")
    assert decision.names == ["logistics"]
    assert "get_logistics" in decision.tools


def test_price_protection_route():
    decision = IntentRouter().route("这个订单能价保吗，有没有差价")
    assert "price" in decision.names
    assert "get_price_history" in decision.tools


def test_chitchat_has_no_tools_and_unknown_keeps_all():
    router = IntentRouter()
    chat = router.route("你好")
    assert chat.names == ["chitchat"]
    assert chat.tools == frozenset()
    unknown = router.route("今天天气怎么样")
    assert unknown.names == []
    assert unknown.tools is None


def _tool_names(call):
    return {tool["function"]["name"] for tool in call["tools"]}


def test_agent_filters_schemas_for_a_single_route(settings, registry):
    llm = ScriptedLLM([ScriptedResponse(text="B102 支持主动降噪。")])
    agent = CustomerServiceAgent(
        settings=settings,
        llm=llm,
        registry=registry,
        current_user_id="USER001",
        intent_routing=True,
    )
    result = agent.chat("这款耳机降噪怎么样")

    names = _tool_names(llm.calls[0])
    assert "get_product" in names and "list_products" in names
    assert "list_orders" not in names
    assert any(event.type == "intent_route" for event in result.trace)


def test_agent_keeps_both_tool_families_for_two_routes(settings, registry):
    llm = ScriptedLLM([ScriptedResponse(text="订单与政策分别回答。")])
    agent = CustomerServiceAgent(
        settings=settings,
        llm=llm,
        registry=registry,
        current_user_id="USER001",
        intent_routing=True,
    )
    agent.chat("查我的订单，并说明退货政策期限")

    names = _tool_names(llm.calls[0])
    assert "list_orders" in names and "search_knowledge" in names
    assert "list_products" not in names
    assert "preview_return" not in names


def test_agent_chitchat_has_empty_tools_and_unknown_keeps_all(settings, registry):
    chat_llm = ScriptedLLM([ScriptedResponse(text="您好，有什么可以帮您？")])
    chat_agent = CustomerServiceAgent(
        settings=settings,
        llm=chat_llm,
        registry=registry,
        current_user_id="USER001",
        intent_routing=True,
    )
    chat_agent.chat("你好")
    assert _tool_names(chat_llm.calls[0]) == set()

    unknown_llm = ScriptedLLM([ScriptedResponse(text="今天天气我无法查询。")])
    unknown_agent = CustomerServiceAgent(
        settings=settings,
        llm=unknown_llm,
        registry=registry,
        current_user_id="USER001",
        intent_routing=True,
    )
    unknown_agent.chat("今天天气怎么样")
    assert _tool_names(unknown_llm.calls[0]) == set(registry.names)


def test_bare_return_is_an_action_but_refund_policy_is_not():
    router = IntentRouter()
    bare = router.route("退货")
    assert bare.names == ["return"]
    assert "preview_return" in bare.tools

    policy = router.route("退款政策是什么")
    assert policy.names == ["policy"]
    assert "preview_return" not in policy.tools

    action = router.route("我要退款")
    assert action.names == ["return"]


def test_soft_routing_keeps_all_tools_but_records_the_route(settings, registry):
    llm = ScriptedLLM([ScriptedResponse(text="B102 支持主动降噪。")])
    agent = CustomerServiceAgent(
        settings=settings,
        llm=llm,
        registry=registry,
        current_user_id="USER001",
        intent_routing=True,
        strict_routing=False,
    )
    result = agent.chat("这款耳机降噪怎么样")

    assert _tool_names(llm.calls[0]) == set(registry.names)
    assert any(event.type == "intent_route" for event in result.trace)
