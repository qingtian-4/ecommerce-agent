"""Render a pure current-turn order lookup without merging different item attributes."""

from __future__ import annotations

import html

from .cs_tools import TraceEvent


def _cell(value) -> str:
    text = "未知" if value is None or value == "" else str(value)
    text = html.escape(text, quote=False).replace("\\", "\\\\")
    text = text.replace("|", "&#124;").replace("\r", " ").replace("\n", " ")
    for char in "`*_[]()!":
        text = text.replace(char, "\\" + char)
    return text


def order_candidate_response(trace: list[TraceEvent], task_view: dict,
                             stop_reason: str) -> tuple[str, dict] | None:
    """Only a single successful list_orders, no active task and no other tool workflow."""
    if (stop_reason != "answered" or task_view.get("task") is not None
            or task_view.get("state_unavailable")):
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
    orders = sorted(payload["orders"], key=lambda o: o["order_id"])
    orders.sort(key=lambda o: o.get("created_at") or "", reverse=True)
    rows = []
    for order in orders:
        items = sorted(order.get("items") or [{}], key=lambda i: i.get("id") or 0)
        for item in items:
            sku = item.get("sku") or {}
            rows.append({"order_id": order["order_id"], "item_id": item.get("id"),
                         "product_id": item.get("product_id"),
                         "product_name": item.get("product_name"),
                         "sku_id": sku.get("sku_id"), "color": sku.get("color"),
                         "quantity": item.get("quantity"), "status": order.get("status"),
                         "created_at": order.get("created_at")})
    detail = {"version": 1, "tool_result_trace_index": index,
              "order_count": len(orders), "rows": rows}
    if not orders:
        return "本轮查询未找到您的订单，请核对当前用户或提供订单号。", detail
    lines = ["订单候选更正（以本轮查询记录为准）：",
             f"本次查到 {len(orders)} 笔订单；每行对应一笔订单中的一条商品明细。", "",
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
