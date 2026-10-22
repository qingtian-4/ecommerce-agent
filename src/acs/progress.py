"""User-visible projections of existing traces. No answer parsing or hidden reasoning."""

from __future__ import annotations

import html

from .cs_tools import TraceEvent
from .order_candidates import order_lookup_result

TOOLS = {
    "get_order": "查询订单",
    "list_orders": "定位订单候选",
    "get_logistics": "查询物流",
    "get_product": "核对商品",
    "list_products": "查看商品目录",
    "search_knowledge": "检索知识依据",
    "get_knowledge_documents": "读取政策与商品资料",
    "get_return_task": "读取退货草稿",
    "update_return_task": "保存退货信息",
    "cancel_return_task": "核查草稿取消",
    "preview_return": "核查申请预览",
    "get_return_requests": "查询售后结果",
}
ROUTES = {
    "presales": "商品咨询",
    "orders": "订单查询",
    "logistics": "物流查询",
    "returns": "售后办理",
    "price_policy": "价保政策",
    "mixed": "组合业务",
    "unknown": "继续核实诉求",
}


def evidence(name: str, payload: dict) -> list[dict]:
    """Small fact cards, with a field allowlist; raw tools stay in the developer panel."""
    cards = []
    if name == "get_order" and payload.get("found") is True:
        cards.append(
            {k: payload[k] for k in ("order_id", "status", "total_amount") if k in payload}
        )
        items = payload.get("items")
        for item in items[:3] if isinstance(items, list) else []:
            if isinstance(item, dict):
                card = {k: item[k] for k in ("product_name", "quantity") if k in item}
                sku = item.get("sku")
                if isinstance(sku, dict):
                    card["color"] = sku.get("color")
                cards.append(card)
    elif name == "get_product" and payload.get("found") is True:
        cards.append(
            {k: payload[k] for k in ("product_id", "name", "current_price") if k in payload}
        )
    elif name == "get_knowledge_documents":
        docs = payload.get("documents")
        for doc in docs[:3] if isinstance(docs, list) else []:
            if isinstance(doc, dict):
                cards.append({k: doc[k] for k in ("source", "title", "version") if k in doc})
    elif name == "list_orders":
        source = order_lookup_result(
            [
                TraceEvent("tool_call", "list_orders", {"arguments": "{}"}),
                TraceEvent("tool_result", "list_orders", {"result": payload}),
            ],
            {},
            "answered",
        )
        if source is not None:
            cards.append({"order_count": payload["count"], "source": "本轮订单工具"})
    return cards


def progress_events(sid: str, tid: str, result, phase: str) -> list[dict]:
    events = []

    def add(seq, kind, label, status="observed", facts=None):
        events.append(
            {
                "event_id": f"{tid}:{seq}",
                "session_id": sid,
                "turn_id": tid,
                "seq": seq,
                "kind": kind,
                "label": label,
                "status": status,
                "facts": facts or [],
            }
        )

    add(0, "received", "已收到您的问题")
    preview_ready = False
    for index, event in enumerate(result.trace, 1):
        name = TOOLS.get(event.label, "执行核查")
        if event.type == "route_selected":
            add(index, "route_selected", "本轮处理：" + ROUTES.get(event.label, "继续核实诉求"))
        elif event.type == "tool_call":
            add(index, "tool_started", "正在" + name, "running")
        elif event.type == "tool_result":
            payload = event.detail.get("result")
            payload = payload if isinstance(payload, dict) else {"error": True}
            failed = bool(payload.get("error")) or payload.get("found") is False
            add(
                index,
                "tool_failed" if failed else "tool_completed",
                name + ("未取得可用结果" if failed else "已返回"),
                "blocked" if failed else "observed",
                evidence(event.label, payload) if not failed else [],
            )
            preview_ready = preview_ready or (
                event.label == "preview_return" and payload.get("ready") is True
            )
        elif event.type == "retry_scheduled":
            add(index, "retry_scheduled", "连接暂时异常，正在重试", "waiting")
        elif event.type in {"error", "runtime_failure"}:
            add(index, "tool_failed", name + "遇到限制，请核对提示", "blocked")
        elif event.type == "write_verification":
            add(index, "write_verification",
                "已核对业务执行回执" if event.detail.get("verified") else "业务状态尚未核实",
                "verified" if event.detail.get("verified") else "unverified")
        elif event.type in {"fact_render", "output_gate"}:
            add(
                index,
                "answer_verified",
                "已核对本轮展示依据" if event.detail.get("verified", True)
                else "依据不足，结果尚未核实",
                "verified" if event.detail.get("verified", True) else "unverified",
            )
        elif event.type in {"state_check", "recovered"}:
            verified = (event.type == "recovered" or "observed_status" in event.detail
                        or (event.label == "write_reconciliation"
                            and event.detail.get("status") in {
                                "draft", "cancelled", "submitted", "absent",
                            }))
            add(index, "business_state_updated",
                "已重新核对后台状态" if verified else "后台状态尚未核实，请重新载入查询",
                "observed" if verified else "unverified")
        elif event.type == "ui_action":
            payload = event.detail.get("result", {})
            preview_ready = preview_ready or payload.get("ready") is True
            add(index, "business_state_updated", "页面操作已有后台回执")
    if phase == "completed":
        if preview_ready and result.stop_reason == "answered":
            add(
                len(result.trace) + 1,
                "awaiting_confirmation",
                "预览已生成，等待您检查并明确确认",
                "waiting",
            )
        else:
            ok = result.stop_reason == "answered"
            add(
                len(result.trace) + 1,
                "turn_completed" if ok else "turn_interrupted",
                "本轮回复已完成，业务结果以回执为准" if ok else "本轮未完成，请核对实际状态后继续",
                "completed" if ok else "incomplete",
            )
    return events


def render_progress(events: list[dict]) -> str:
    if not events:
        return ""
    parts = ["<details class='execution-progress' open><summary>处理进度与依据</summary>"
             + "<div class='progress-current' role='status'>" + html.escape(events[-1]['label'])
             + "</div><ol>"]
    for event in events:
        labels = {
            "order_id": "订单",
            "status": "状态",
            "total_amount": "实付金额",
            "product_name": "商品",
            "quantity": "数量",
            "color": "颜色",
            "product_id": "型号",
            "name": "商品",
            "current_price": "当前价",
            "source": "来源",
            "title": "文档",
            "version": "版本",
            "order_count": "订单数",
        }
        states = {
            "delivered": "已签收",
            "shipped": "已发货",
            "pending": "待发货",
            "cancelled": "已取消",
        }
        facts = "".join(
            "<div class='evidence-fact'>"
            + " · ".join(
                html.escape(labels.get(key, key))
                + "："
                + html.escape(str(states.get(str(value), value) if value is not None else "未知"))
                for key, value in card.items()
            )
            + "</div>"
            for card in event.get("facts", [])
        )
        parts.append(
            f"<li data-event-id='{html.escape(event['event_id'], quote=True)}'>"
            + html.escape(event["label"])
            + facts
            + "</li>"
        )
    return "".join(parts) + "</ol></details>"
