"""Narrow postcondition for an explicit return request, never an execution authority."""

from __future__ import annotations

import json
import re

from .cs_tools import TraceEvent
from .runtime import BudgetExceeded, RunBudget
from .sessions import SessionStore


def _order_read(trace: list[TraceEvent], order_id: str, user_id: str) -> int | None:
    """The loop executes tools serially; pair actual calls with actual results in this turn."""
    pending, found = None, None
    for index, event in enumerate(trace):
        if event.type == "tool_call":
            pending = None
            if event.label == "get_order":
                try:
                    args = json.loads(event.detail.get("arguments", ""))
                except (ValueError, TypeError):
                    continue
                if isinstance(args, dict) and args.get("order_id") == order_id:
                    pending = order_id
        elif event.type == "error":
            if event.label == "get_order" and pending:
                found = None
            pending = None
        elif event.type == "tool_result":
            if event.label == "get_order" and pending == order_id:
                payload = event.detail.get("result", {})
                valid = (isinstance(payload, dict) and payload.get("found") is True
                         and payload.get("order_id") == order_id
                         and payload.get("user_id") == user_id)
                found = index if valid else None
            pending = None
    return found


def check_order_draft(store: SessionStore, sid: str, uid: str, tid: str, message: str,
                      trace: list[TraceEvent], stop_reason: str,
                      budget: RunBudget) -> TraceEvent | None:
    # Full utterance only. Never strip negation, quoted text, conditions or extra clauses.
    match = re.fullmatch(r"(?:我想退|我要退)\s*([A-Za-z0-9][A-Za-z0-9_-]*)[。.!！]?",
                         message.strip())
    if match is None:
        return None
    order_id = match[1]
    detail = {"version": 1, "order_id": order_id, "turn_id": tid, "user_text": message,
              "source_type": "user_claim", "status": "blocked", "reason": "turn_not_answered",
              "before_revision": None, "after_revision": None}
    try:
        if stop_reason == "answered":
            budget.remaining()
            task = store.get_return_task(sid, uid)["task"]
            detail["before_revision"] = task["revision"] if task else None
            if task is not None:
                satisfied = task["status"] == "draft" and task["order_id"] == order_id
                detail.update(status="satisfied" if satisfied else "conflict",
                              reason="existing_task")
            else:
                index = _order_read(trace, order_id, uid)
                if index is None:
                    detail["reason"] = "missing_owned_order_read"
                else:
                    detail["order_result_trace_index"] = index
                    view = store.update_return_task(
                        sid, uid, tid, {"order_id": order_id},
                        only_if_absent=True, budget=budget,
                    )
                    status = view["state_write"]
                    detail.update(status="repaired" if status == "created" else status,
                                  reason="conditional_write", write_status=status)
    except BudgetExceeded:
        detail.update(status="blocked", reason="budget_expired")
    except Exception as exc:  # noqa: BLE001 - read back authority even after an uncertain write
        detail.update(status="error", reason="check_failed", error_type=type(exc).__name__)
    return TraceEvent("state_check", "return_order_draft", detail)


def state_notice(event: TraceEvent, view: dict) -> str:
    """An explicit final correction, based on a fresh read, even if text already streamed."""
    detail, task = event.detail, view.get("task")
    prefix = "状态更正（以重新读取的后台记录为准）："
    if view.get("state_unavailable"):
        return prefix + "当前草稿状态无法核实，不能确认是否保存；请重新载入核对，勿重复提交。"
    if task is None:
        text = "当前没有退货草稿，订单尚未保存为草稿；本轮没有创建新的售后申请。"
    elif task["status"] == "submitted":
        text = (f"当前会话已有售后申请 {task['request_id']}，本轮没有新建申请，"
                "结果需查询实际审核状态，尚未退款。")
    elif task["status"] == "cancelled":
        text = "当前退货草稿已中止，未执行自动补存或创建新的申请。"
    elif task["order_id"] is None:
        text = "当前已有退货草稿但尚未绑定订单；未执行自动补存，请补充或更正草稿订单。"
    elif task["order_id"] != detail["order_id"]:
        text = (f"当前草稿对应订单 {task['order_id']}，与本轮请求的 {detail['order_id']} 不同；"
                "未执行自动补存，请明确是否更改。")
    else:
        text = f"订单 {task['order_id']} 已记录在退货草稿中，尚未提交申请。"
        if task["item_id"] is None:
            text += "仍需由您选择商品明细，未替您选择商品。"
    if detail["reason"] == "budget_expired":
        text = "本轮运行预算已耗尽，未确认自动补存完成。" + text
    elif detail["reason"] == "turn_not_answered":
        text = "本轮回答未完成，未执行自动补存。" + text
    elif detail["status"] == "error":
        text = "状态检查出现异常，以下是重新查询的结果。" + text
    return prefix + text
