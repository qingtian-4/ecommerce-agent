"""Legacy rule-router unit tests.

The product path no longer hard-filters tools with these rules.  They remain only as
a V3 prompt-hint baseline and as a regression record for the held-out evaluation.
"""

from __future__ import annotations

from acs.intent_router import IntentRouter


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
