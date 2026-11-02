"""Bounded multi-goal checks for explicit, verifiable user requests.

This is deliberately not a planner or a general intent classifier.  It recognises a
small set of goals only when the user explicitly asks for several of them, and it
checks the tool receipts the runtime already produced.  Unknown wording is left to
the model and the existing answer review.

The runtime uses this to avoid the observed failure mode where a model answers the
first sub-request and stops, leaving the second one unaddressed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .cs_tools import CATALOG_CONTRACT, catalog_progress

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
    return_tool: str | None = None


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _clauses(text: str) -> list[str]:
    """Bound evidence cues to a local request, never use them to authorize tools."""
    return [part for part in re.split(
        r"[，,。；;！？!?\n]|然后|另外|同时|并且|以及|而且|但是|但|并|和|再", text
    ) if part.strip()]


def _all_orders(text: str) -> bool:
    for clause in _clauses(text):
        if not _has(clause, ORDER_WORDS):
            continue
        specific = re.search(r"[Oo]\d{5,}|这笔|这单|该订单|指定订单", clause)
        explicit_all = re.search(r"(?:全部|所有)(?:的)?(?:订单|购买记录)", clause)
        if _has(clause, ALL_WORDS) and (not specific or explicit_all):
            return True
    return False


def _return_tool(text: str) -> str | None:
    actions = set()
    # Keep coordinated actions together ("不要保存和预览"), but start a
    # new scope at an explicit negation even without punctuation.
    clauses = re.split(r"[，,。；;！？!?\n]|然后|另外|但是|但|再|(?=不要|先别|先不|不用)", text)
    for clause in clauses:
        if re.search(r"不要|别|先不|不用|只读|只查|如果|假如|不是|不想|不需要", clause):
            continue
        read = re.search(r"查询|查看|核对|看看|查一下", clause)
        action = re.search(r"预览|取消|中止|清空|帮我退|我要退|修改|改成|保存|存草稿|记录|办理",
                           clause)
        if read and action and read.start() < action.start():
            # "查看已保存的草稿" describes evidence; "查询后保存" asks for
            # another action. Only an explicit following action changes scope.
            following = re.search(
                r"(?:后|并)(?=(?:请|先)?(?:保存|修改|改成|生成|预览|取消|中止|清空|记录|办理))",
                clause[read.end():],
            )
            if following is None:
                continue
            clause = clause[read.end() + following.end():]
        if "预览" in clause:
            actions.add("preview_return")
        if re.search(r"取消|中止|清空", clause):
            actions.add("cancel_return_task")
        # "退货记录" and "已办理申请" name query objects, not write requests.
        explicit_write = re.search(r"帮我退|我要退|修改|改成|保存|存草稿", clause)
        record_or_process = re.search(r"(?:^|请|先|帮我|帮忙|给我|麻烦|为我)(?:记录|办理)",
                                      clause.strip())
        if explicit_write or record_or_process:
            actions.add("update_return_task")
    return next((name for name in ("preview_return", "cancel_return_task", "update_return_task")
                 if name in actions), None)


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
                require_all_orders=_all_orders(value),
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


def _read_result(payload: dict[str, Any], id_key: str) -> bool:
    # A well-formed, tool-scoped absence is an answer to a read query, not
    # evidence of business eligibility. Errors/denials are excluded by missing().
    return (payload.get("found") is True and bool(payload.get(id_key))) or (
        payload.get("found") is False and bool(payload.get(id_key))
        and isinstance(payload.get("reason"), str) and bool(payload["reason"]))


def _orders_done(spec: GoalSpec, name: str, payload: dict[str, Any]) -> bool:
    if name == "list_orders":
        return isinstance(payload.get("orders"), list) and payload.get("has_more") is not True
    return not spec.require_all_orders and name == "get_order" and _read_result(payload, "order_id")


def _catalog_done(spec: GoalSpec, name: str, payload: dict[str, Any]) -> bool:
    if name == "list_products" and payload.get("products") is not None:
        return not spec.require_complete_catalog or payload.get("complete_catalog") is True
    return name == "get_product" and bool(payload) and not spec.require_complete_catalog


def _policy_done(name: str, payload: dict[str, Any]) -> bool:
    if name not in {"search_knowledge", "get_knowledge_documents"}:
        return False
    return payload.get("found") is True or bool(payload.get("documents"))


def _logistics_done(name: str, payload: dict[str, Any]) -> bool:
    return name == "get_logistics" and _read_result(payload, "order_id")


def _product_done(spec: GoalSpec, name: str, payload: dict[str, Any]) -> bool:
    if spec.require_complete_catalog:
        return _catalog_done(spec, name, payload)
    if name == "list_products":
        return (isinstance(payload.get("products"), list)
                and payload.get("has_more") is not True
                and payload.get("query_complete") is not False)
    return name == "get_product" and _read_result(payload, "product_id")


def _return_done(spec: GoalSpec, name: str, payload: dict[str, Any]) -> bool:
    if spec.return_tool and name != spec.return_tool:
        return False
    if name == "preview_return":
        return payload.get("ready") is True
    if name == "get_return_requests":
        return isinstance(payload.get("requests"), list)
    if name == "update_return_task":
        return isinstance(payload.get("task"), dict) and payload["task"].get("status") == "draft"
    if name in {"get_return_task", "cancel_return_task"}:
        return "task" in payload and payload.get("success") is not False
    return False


def _price_done(name: str, payload: dict[str, Any]) -> bool:
    return name == "get_price_history" and _read_result(payload, "product_id")


def _paged_catalog_done(spec: GoalSpec, receipts: list[dict]) -> bool:
    pages = []
    for receipt in receipts:
        payload = receipt["payload"]
        if (receipt["name"] != "list_products" or receipt["error"]
                or payload.get("contract") != CATALOG_CONTRACT or payload.get("error_type")):
            continue
        if (spec.require_complete_catalog
                and payload.get("catalog_count") != payload.get("total_count")):
            continue
        pages.append({"name": "list_products", "arguments": receipt["arguments"],
                      "result": json.dumps(payload), "error": None})
    try:
        progress = catalog_progress(pages)
    except (TypeError, ValueError, KeyError):
        return False  # malformed receipts cannot become proof of completion
    return any(p["complete"] and (not spec.require_complete_catalog or not p["query"])
               for p in progress)


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
    def from_plan(cls, plan: Any, *, user_text: str = "") -> GoalLedger:
        ledger = cls(user_text=user_text)
        complete_catalog = any(s.require_complete_catalog for s in ledger.specs)
        all_orders = _all_orders(user_text)
        return_tool = _return_tool(user_text)
        ledger.specs = [
            GoalSpec(name, LABELS[name],
                     require_complete_catalog=name == "product" and complete_catalog,
                     require_all_orders=name == "orders" and all_orders,
                     return_tool=return_tool if name == "return" else None)
            for name in plan.names
            if name in LABELS
        ]
        return ledger

    @property
    def active(self) -> bool:
        return bool(self.specs)

    def record(self, name: str, payload: Any, error: str | None = None, *,
               arguments: str | dict | None = None) -> None:
        self.receipts.append(
            {
                "name": name,
                "payload": payload if isinstance(payload, dict) else {},
                "error": error,
                "arguments": (arguments if isinstance(arguments, str)
                              else json.dumps(arguments or {})),
            }
        )

    def missing(self) -> list[str]:
        done = {spec.name: False for spec in self.specs}
        for receipt in self.receipts:
            if receipt["error"]:
                continue
            name = receipt["name"]
            payload = receipt["payload"]
            if payload.get("error_type") or payload.get("error"):
                continue
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
                    done[spec.name] = _product_done(spec, name, payload)
                elif spec.name == "return":
                    done[spec.name] = _return_done(spec, name, payload)
                elif spec.name == "price":
                    done[spec.name] = _price_done(name, payload)
        for spec in self.specs:
            if spec.name in {"product", "catalog"} and not done[spec.name]:
                done[spec.name] = _paged_catalog_done(spec, self.receipts)
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
                hints.append('调用 list_products(view="overview") 获取完整商品概览'
                             if spec.require_complete_catalog
                             else "调用 list_products 或 get_product 获取商品事实")
            elif spec.name == "return":
                hints.append("调用退货草稿/预览/申请查询工具，确认业务回执")
            elif spec.name == "price":
                hints.append("调用 get_price_history 获取价格历史")
        return (
            "本轮用户明确提出了多个诉求，目前还缺少："
            + labels
            + "。请先"
            + "；".join(hints)
            + "，再给出覆盖全部诉求的完整回答，修正之前的错误，不要只补一句。"
            "遇到拒绝、无结果或缺项应如实说明，不能猜测或扩大写操作授权；"
            "不要用未完成的目录冒充全部。"
        )
