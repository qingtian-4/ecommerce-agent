"""Bounded, rule-based intent routing for the customer-service domain.

This is deliberately not a general NLU classifier.  It maps explicit Chinese cues to
a small set of service intents and to the tools that can provide evidence for them.
A route is a tool/prompt policy, not a claim that the model will choose the right tool.

Routing is intentionally conservative: unknown wording keeps all tools available and
receives no restrictive hint; a recognised route only narrows the tool schema and adds
a short system hint.  The existing application checks (identity, preview, confirmation,
transaction, idempotency) remain authoritative for every tool.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteSpec:
    name: str
    label: str
    cues: tuple[str, ...]
    tools: frozenset[str]


ORDER_TOOLS = frozenset({"list_orders", "get_order", "get_logistics", "get_return_requests"})
LOGISTICS_TOOLS = frozenset({"get_logistics", "get_order"})
PRODUCT_TOOLS = frozenset(
    {
        "list_products",
        "get_product",
        "get_price_history",
        "search_knowledge",
        "get_knowledge_documents",
    }
)
POLICY_TOOLS = frozenset(
    {
        "search_knowledge",
        "get_knowledge_documents",
        "get_order",
        "get_product",
        "get_return_task",
        "get_return_requests",
    }
)
RETURN_TOOLS = frozenset(
    {
        "get_order",
        "get_product",
        "get_return_task",
        "update_return_task",
        "cancel_return_task",
        "preview_return",
        "get_return_requests",
        "search_knowledge",
        "get_knowledge_documents",
    }
)
PRICE_TOOLS = frozenset({"get_order", "get_price_history", "search_knowledge"})

ORDER = RouteSpec(
    "orders",
    "订单查询",
    ("订单", "买了什么", "购买记录", "下单", "订单号"),
    ORDER_TOOLS,
)
LOGISTICS = RouteSpec(
    "logistics",
    "物流查询",
    ("物流", "快递", "到哪", "签收", "发货", "配送"),
    LOGISTICS_TOOLS,
)
PRODUCT = RouteSpec(
    "product",
    "商品咨询",
    (
        "商品",
        "产品",
        "型号",
        "规格",
        "参数",
        "降噪",
        "续航",
        "颜色",
        "尺寸",
        "重量",
        "功率",
        "接口",
        "价格",
        "多少钱",
        "推荐",
        "对比",
        "耳机",
        "充电器",
        "充电宝",
    ),
    PRODUCT_TOOLS,
)
POLICY = RouteSpec(
    "policy",
    "退换货政策",
    (
        "政策",
        "规则",
        "期限",
        "保修",
        "条件",
        "多久",
        "能退",
        "可以退",
        "怎么退",
        "退换货",
        "退货政策",
        "退款政策",
        "售后政策",
    ),
    POLICY_TOOLS,
)
RETURN = RouteSpec(
    "return",
    "退换货办理",
    (
        "申请退货",
        "办理退货",
        "帮我退",
        "退掉",
        "退这件",
        "提交退货",
        "取消退货",
        "修改草稿",
        "预览退货",
        "退货",
        "退款",
        "换货",
        "售后申请",
        "我要退",
    ),
    RETURN_TOOLS,
)
PRICE = RouteSpec(
    "price",
    "价保",
    ("价保", "差价", "降价", "价格保护"),
    PRICE_TOOLS,
)
CHITCHAT = RouteSpec(
    "chitchat", "寒暄", ("你好", "您好", "谢谢", "再见", "在吗", "嗨"), frozenset()
)
ROUTES = (ORDER, LOGISTICS, PRODUCT, POLICY, RETURN, PRICE)
ACTION_CUES = ("申请", "办理", "帮我", "退掉", "退这件", "提交", "取消", "修改", "预览", "我要")


def _hit(text: str, cues: tuple[str, ...]) -> bool:
    return any(cue in text for cue in cues)


@dataclass(frozen=True)
class RoutingDecision:
    routes: tuple[RouteSpec, ...]
    tools: frozenset[str] | None
    hint: str

    @property
    def labels(self) -> list[str]:
        return [route.label for route in self.routes]

    @property
    def names(self) -> list[str]:
        return [route.name for route in self.routes]


class IntentRouter:
    """Rule router with a bounded vocabulary; unknown text keeps all tools."""

    def route(self, text: str) -> RoutingDecision:
        value = (text or "").strip()
        if not value:
            return RoutingDecision((), None, "")
        routes: list[RouteSpec] = []
        if _hit(value, ORDER.cues):
            routes.append(ORDER)
        if _hit(value, LOGISTICS.cues):
            routes.append(LOGISTICS)
        if _hit(value, PRODUCT.cues):
            routes.append(PRODUCT)
        policy_hit = _hit(value, POLICY.cues)
        if policy_hit:
            routes.append(POLICY)
        # "退款政策" is a policy question; a bare "退货/退款/换货" is an action,
        # but an explicit policy cue keeps it on the read-only policy route.
        return_action = _hit(value, RETURN.cues) and (
            _hit(value, ACTION_CUES)
            or (not policy_hit and any(cue in value for cue in ("退货", "退款", "换货")))
        )
        if return_action:
            routes.append(RETURN)
        if _hit(value, PRICE.cues):
            routes.append(PRICE)
        if not routes and _hit(value, CHITCHAT.cues) and len(value) <= 20:
            return RoutingDecision((CHITCHAT,), frozenset(), self._hint((CHITCHAT,), frozenset()))
        if not routes:
            return RoutingDecision((), None, "")
        tools = frozenset().union(*(route.tools for route in routes))
        return RoutingDecision(tuple(routes), tools, self._hint(tuple(routes), tools))

    @staticmethod
    def _hint(routes: tuple[RouteSpec, ...], tools: frozenset[str]) -> str:
        labels = "、".join(route.label for route in routes)
        if not tools:
            return "本轮是寒暄，不需要调用工具；直接回答，不要编造业务事实。"
        return (
            f"本轮识别到的意图：{labels}。优先使用这些工具："
            + "、".join(sorted(tools))
            + "；不要调用未列出的工具。若用户还提出其他明确诉求，先澄清再处理，"
            "不要用未完成的目录或未核实的政策代替。"
        )
