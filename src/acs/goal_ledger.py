"""Bounded multi-goal checks for explicit, verifiable user requests.

This is deliberately not a planner or a general intent classifier.  It recognises a
small set of goals only when the user explicitly asks for several of them, and it
checks the tool receipts the runtime already produced.  Unknown wording is left to
the model and the existing answer review.

The runtime uses this to avoid the observed failure mode where a model answers the
first sub-request and stops, leaving the second one unaddressed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ORDER_WORDS = ("订单", "买了", "购买记录", "下单")
CATALOG_WORDS = ("商品", "产品", "目录", "SKU", "型号")
SHOP_WORDS = (
    "店铺",
    "店里",
    "店内的",
    "全店",
    "在售",
    "商品列表",
    "产品列表",
    "哪些商品",
    "哪些产品",
    "全部商品",
    "全部产品",
)
POLICY_WORDS = ("政策", "规则", "期限", "保修", "多久", "条件")
ALL_WORDS = ("都有哪些", "全部", "所有", "列全", "一共", "多少笔")
COMPLETE_WORDS = SHOP_WORDS + ("都有哪些",)
LABELS = {
    "orders": "订单",
    "logistics": "物流",
    "product": "商品咨询",
    "policy": "政策/期限",
    "return": "退换货办理",
    "price": "价保",
}


@dataclass(frozen=True)
class GoalSpec:
    name: str
    label: str
    require_complete_catalog: bool = False
    require_all_orders: bool = False


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def detect_goals(text: str) -> list[GoalSpec]:
    """Return explicit goals, or an empty list when this is not a bounded multi-goal case."""
    value = (text or "").strip()
    if not value:
        return []
    specs: list[GoalSpec] = []
    if _has(value, ORDER_WORDS):
        specs.append(
            GoalSpec(
                "orders",
                "订单",
                require_all_orders=_has(value, ALL_WORDS),
            )
        )
    # "商品明细" belongs to an order line-item question, not the shop catalogue.
    catalog = (
        _has(value, CATALOG_WORDS)
        and _has(value, SHOP_WORDS + ("商品咨询",))
        and "商品明细" not in value
    )
    if catalog:
        specs.append(
            GoalSpec(
                "catalog",
                "商品概览",
                require_complete_catalog=_has(value, COMPLETE_WORDS),
            )
        )
    if _has(value, POLICY_WORDS):
        specs.append(GoalSpec("policy", "政策/期限"))
    if len(specs) < 2 and not any(spec.require_complete_catalog for spec in specs):
        return []
    return specs


def _orders_done(spec: GoalSpec, name: str, payload: dict[str, Any]) -> bool:
    if name == "list_orders":
        return payload.get("orders") is not None
    return not spec.require_all_orders and name in {"get_order", "get_logistics"} and bool(payload)


def _catalog_done(spec: GoalSpec, name: str, payload: dict[str, Any]) -> bool:
    if name == "list_products" and payload.get("products") is not None:
        return not spec.require_complete_catalog or payload.get("complete_catalog") is True
    return name == "get_product" and bool(payload) and not spec.require_complete_catalog


def _policy_done(name: str, payload: dict[str, Any]) -> bool:
    if name not in {"search_knowledge", "get_knowledge_documents"}:
        return False
    return payload.get("found") is True or bool(payload.get("documents"))


def _logistics_done(name: str, payload: dict[str, Any]) -> bool:
    return name in {"get_logistics", "get_order"} and bool(payload)


def _product_done(name: str, payload: dict[str, Any]) -> bool:
    if name == "list_products":
        return payload.get("products") is not None
    return name in {"get_product", "get_price_history"} and bool(payload)


def _return_done(name: str, payload: dict[str, Any]) -> bool:
    return name in {
        "get_return_task",
        "update_return_task",
        "cancel_return_task",
        "preview_return",
        "get_return_requests",
    } and bool(payload)


def _price_done(name: str, payload: dict[str, Any]) -> bool:
    return name in {"get_price_history", "get_order"} and bool(payload)


@dataclass
class GoalLedger:
    user_text: str
    specs: list[GoalSpec] = field(init=False)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    retries: int = 0
    max_retries: int = 2

    def __post_init__(self) -> None:
        self.specs = detect_goals(self.user_text)

    @classmethod
    def from_plan(cls, plan: Any) -> GoalLedger:
        ledger = cls(user_text="")
        ledger.specs = [
            GoalSpec(name, LABELS[name])
            for name in plan.names
            if name in LABELS
        ]
        return ledger

    @property
    def active(self) -> bool:
        return bool(self.specs)

    def record(self, name: str, payload: Any, error: str | None = None) -> None:
        self.receipts.append(
            {
                "name": name,
                "payload": payload if isinstance(payload, dict) else {},
                "error": error,
            }
        )

    def missing(self) -> list[str]:
        done = {spec.name: False for spec in self.specs}
        for receipt in self.receipts:
            if receipt["error"]:
                continue
            name = receipt["name"]
            payload = receipt["payload"]
            for spec in self.specs:
                if done[spec.name]:
                    continue
                if spec.name == "orders":
                    done[spec.name] = _orders_done(spec, name, payload)
                elif spec.name == "catalog":
                    done[spec.name] = _catalog_done(spec, name, payload)
                elif spec.name == "policy":
                    done[spec.name] = _policy_done(name, payload)
                elif spec.name == "logistics":
                    done[spec.name] = _logistics_done(name, payload)
                elif spec.name == "product":
                    done[spec.name] = _product_done(name, payload)
                elif spec.name == "return":
                    done[spec.name] = _return_done(name, payload)
                elif spec.name == "price":
                    done[spec.name] = _price_done(name, payload)
        return [spec.name for spec in self.specs if not done[spec.name]]

    def labels(self, missing: list[str] | None = None) -> list[str]:
        names = set(missing if missing is not None else self.missing())
        return [spec.label for spec in self.specs if spec.name in names]

    def can_retry(self) -> bool:
        return self.retries < self.max_retries

    def retry_prompt(self, missing: list[str] | None = None) -> str:
        names = missing if missing is not None else self.missing()
        if not names:
            return ""
        labels = "、".join(self.labels(names))
        hints: list[str] = []
        for spec in self.specs:
            if spec.name not in names:
                continue
            if spec.name == "orders":
                suffix = "（全部）" if spec.require_all_orders else ""
                hints.append(f"调用 list_orders 获取本人订单候选{suffix}")
            elif spec.name == "catalog":
                if spec.require_complete_catalog:
                    hints.append(
                        '调用 list_products(view="overview") 获取完整商品概览；'
                        "若超出预算，明确说明未完成"
                    )
                else:
                    hints.append("调用 get_product 或 list_products 获取商品事实")
            elif spec.name == "policy":
                hints.append("调用 search_knowledge 获取政策原文")
            elif spec.name == "logistics":
                hints.append("调用 get_logistics 获取物流轨迹")
            elif spec.name == "product":
                hints.append("调用 list_products 或 get_product 获取商品事实")
            elif spec.name == "return":
                hints.append("调用退货草稿/预览/申请查询工具，确认业务回执")
            elif spec.name == "price":
                hints.append("调用 get_price_history 获取价格历史")
        return (
            "本轮用户明确提出了多个诉求，目前还缺少："
            + labels
            + "。请先"
            + "；".join(hints)
            + "，再分别回答；不要只回答其中一项，也不要用未完成的目录冒充全部。"
        )
