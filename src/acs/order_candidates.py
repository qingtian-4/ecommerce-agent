"""Render a pure current-turn order lookup without merging different item attributes."""

from __future__ import annotations

import html
import re

from .cs_tools import TraceEvent


def _requested_scope(user_text: str) -> str | None:
    """Finite presentation requests; unfamiliar or qualified requests stay with the Loop."""
    text = user_text.strip().rstrip("。.!！?？")
    if (re.fullmatch(r"(?:请)?(?:列出|展开)(?:我的)?(?:全部|所有)订单(?:和|及)"
                     r"(?:全部|所有)?商品明细(?:，不要只给一句话总结)?", text)
            or re.fullmatch(r"我想退货，请(?:先)?列出(?:全部|所有)订单(?:和|及)"
                            r"(?:全部|所有)?商品明细让我选择", text)):
        return "all"
    if re.fullmatch(r"我想退(?:最近买的)?耳机(?:，请只列耳机类商品的候选明细让我选择)?", text):
        return "headphones"
    return None


def _cell(value) -> str:
    text = "未知" if value is None or value == "" else str(value)
    text = html.escape(text, quote=False).replace("\\", "\\\\")
    text = text.replace("|", "&#124;").replace("\r", " ").replace("\n", " ")
    for char in "`*_[]()!":
        text = text.replace(char, "\\" + char)
    return text


def _valid_orders(orders: list) -> bool:
    """Decline the whole presentation when any nested row violates the tool protocol."""
    for order in orders:
        if (not isinstance(order, dict) or not isinstance(order.get("order_id"), str)
                or not order["order_id"].strip()):
            return False
        if any(order.get(k) is not None and not isinstance(order[k], str)
               for k in ("status", "created_at")):
            return False
        items = order.get("items")
        if not isinstance(items, list) or not items:
            return False
        for item in items:
            if (not isinstance(item, dict) or type(item.get("id")) is not int
                    or item["id"] <= 0 or type(item.get("quantity")) is not int
                    or item["quantity"] <= 0):
                return False
            if any(not isinstance(item.get(k), str) or not item[k].strip()
                   for k in ("product_id", "product_name")):
                return False
            if "sku" not in item:
                return False
            sku = item["sku"]
            if sku is not None and (
                not isinstance(sku, dict) or not isinstance(sku.get("sku_id"), str)
                or not sku["sku_id"].strip() or "color" not in sku
                or (sku["color"] is not None and not isinstance(sku["color"], str))
            ):
                return False
    return True


def order_candidate_response(trace: list[TraceEvent], task_view: dict,
                             stop_reason: str, *, user_text: str = "",
                             has_history: bool = False) -> tuple[str, dict] | None:
    """Only explicit first-turn presentation, one list_orders and no task or other workflow."""
    if (has_history or stop_reason != "answered" or task_view.get("task") is not None
            or task_view.get("state_unavailable")):
        return None
    scope = _requested_scope(user_text)
    if scope is None:
        return None
    calls = [(i, e) for i, e in enumerate(trace) if e.type == "tool_call"]
    results = [(i, e) for i, e in enumerate(trace) if e.type == "tool_result"]
    if (len(calls) != 1 or calls[0][1].label != "list_orders" or len(results) != 1
            or results[0][1].label != "list_orders" or calls[0][0] >= results[0][0]
            or any(e.type in {"error", "retrieval"} for e in trace)):
        return None
    index, event = results[0]
    payload = event.detail.get("result")
    if not isinstance(payload, dict) or not isinstance(payload.get("orders"), list):
        return None
    if not _valid_orders(payload["orders"]):
        return None
    categories = payload.get("product_categories")
    if scope == "headphones" and (
        not isinstance(categories, dict)
        or any(not isinstance(categories.get(item["product_id"]), str)
               or categories[item["product_id"]] not in {"无线耳机", "充电配件"}
               for order in payload["orders"] for item in order["items"])
    ):
        return None
    orders = sorted(payload["orders"], key=lambda o: o["order_id"])
    orders.sort(key=lambda o: o.get("created_at") or "", reverse=True)
    rows = []
    for order in orders:
        items = sorted(order["items"], key=lambda i: i["id"])
        for item in items:
            if scope == "headphones" and categories[item["product_id"]] != "无线耳机":
                continue
            sku = item.get("sku") or {}
            rows.append({"order_id": order["order_id"], "item_id": item.get("id"),
                         "product_id": item.get("product_id"),
                         "product_name": item.get("product_name"),
                         "sku_id": sku.get("sku_id"), "color": sku.get("color"),
                         "quantity": item.get("quantity"), "status": order.get("status"),
                         "created_at": order.get("created_at")})
    detail = {"version": 2, "tool_result_trace_index": index, "request_scope": scope,
              "source_order_count": len(orders),
              "order_count": len({r["order_id"] for r in rows}), "rows": rows}
    if not orders:
        return "本轮查询未找到您的订单，请核对当前用户或提供订单号。", detail
    if scope == "headphones" and not rows:
        return "本轮查询未找到耳机类商品明细，请核对商品类别或提供订单号。", detail
    intro = (f"本次查到 {len(orders)} 笔订单；每行对应一笔订单中的一条商品明细。"
             if scope == "all" else "以下每行对应一笔订单中的一条耳机商品明细。")
    lines = ["订单候选更正（以本轮查询记录为准）：", intro]
    if scope == "headphones" and "最近" in user_text:
        lines.append("“最近”尚未限定时间范围；以下列出查询到的耳机候选，请确认时间范围。")
    lines += ["",
             "| 序号 | 订单号 | 明细编号 | 商品 | SKU | 颜色 | 数量 | 状态 | 下单日期 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    labels = {"pending": "待发货", "shipped": "已发货", "delivered": "已签收",
              "cancelled": "已取消"}
    for number, row in enumerate(rows, 1):
        values = [number, row["order_id"], row["item_id"], row["product_name"], row["sku_id"],
                  row["color"], row["quantity"], labels.get(row["status"], row["status"]),
                  row["created_at"]]
        lines.append("| " + " | ".join(map(_cell, values)) + " |")
    lines += ["", "请提供需要查询或办理的订单号及明细编号。候选不代表您已经选择；"
              "退货资格仍需另行核查。"]
    return "\n".join(lines), detail
