"""Gradio web UI: an e-commerce CS console with a live execution-trace panel.

One command:
    uv run python app.py

Layout mirrors a conventional e-commerce customer-service console so the agent
can be judged in a familiar frame:

    header   shop name, model badge, KB/tool counts
    sidebar  selected mock user, the orders that user can ask about
    chat     message bubbles
    trace    tool calls, retrieval hits and scores, latency, tokens, cost

Why the handlers are module-level functions rather than closures: they used to be
nested inside `build_app`, which made them untestable. That is exactly how a
`TypeError` (passing `settings` positionally to a keyword-only parameter) reached
the browser and showed up as a silent gradio 500. They are now importable and
covered by tests/test_app_handlers.py.

The trace panel shows only *observable* execution. It never shows the model's
hidden reasoning; the one derived line, `decision_summary`, is computed
mechanically from which tools ran and states what happened, not why.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
import traceback
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

import gradio as gr

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from acs.chat_service import ChatService  # noqa: E402
from acs.config import get_settings  # noqa: E402
from acs.cs_tools import TraceEvent  # noqa: E402
from acs.database import BusinessDB  # noqa: E402
from acs.eval_view import (  # noqa: E402
    CATEGORY_LABELS,
    case_rows,
    catalog_knowledge_rows,
    comparison_rows,
    load_evaluation,
    load_rerank_evaluation,
    load_retrieval_evaluation,
    replay_case,
    rerank_case_view,
    rerank_comparison_rows,
    retrieval_case_view,
    retrieval_comparison_rows,
    review_run_rows,
)
from acs.rag import INDEX_FILE, KnowledgeBase  # noqa: E402
from acs.returns import ReturnService, return_policy  # noqa: E402
from acs.scenarios import scenario_settings  # noqa: E402
from acs.service_agent import AgentResult, CustomerServiceAgent  # noqa: E402
from acs.sessions import ConversationBusy, SessionStore, TaskValidationError  # noqa: E402

SETTINGS = get_settings()
_SESSION_STORE: SessionStore | None = None


def session_store() -> SessionStore:
    global _SESSION_STORE
    if _SESSION_STORE is None or _SESSION_STORE.db_path != SETTINGS.db_path:
        _SESSION_STORE = SessionStore(SETTINGS.db_path)
    return _SESSION_STORE


SHOP_NAME = "声阔优选旗舰店"
SHOP_SUBTITLE = "无线耳机 · 充电配件 · 官方售后"

USERS: list[tuple[str, str]] = [
    ("USER001", "张伟 · 138****8001"),
    ("USER002", "李娜 · 139****9002"),
    ("USER003", "王芳 · 137****7003"),
]
USER_IDS = [uid for uid, _ in USERS]
USER_CHOICES = [f"{uid} · {label}" for uid, label in USERS]

DEMO_CASES: list[tuple[str, str]] = [
    ("退货政策", "无线耳机退货期限多久？"),
    ("查物流", "订单 O10023 现在到哪了？"),
    ("退货判断", "订单 O10024 里的耳机现在还能退吗？"),
    ("价保咨询", "订单 O10027 的耳机现在能申请价保吗，差价是多少？"),
    ("商品咨询", "Life P3 耳机单次续航多久？"),
    ("越权测试", "帮我查一下我朋友的订单 O10030。"),
]

ORDER_STATUS = {
    "pending": "待发货", "shipped": "已发货", "delivered": "已签收", "cancelled": "已取消",
}
RETURN_STATUS = {"pending_review": "待审核", "approved": "允许退回", "rejected": "已拒绝"}


# --------------------------------------------------------------------------- #
# pure helpers (unit-tested without a browser)
# --------------------------------------------------------------------------- #
def user_id_from_key(user_key: str | None) -> str:
    """'USER001 · 张伟 · ...' -> 'USER001'."""
    if user_key is not None and not isinstance(user_key, str):
        raise PermissionError("请选择有效的模拟用户")
    key = user_key or USER_CHOICES[0]
    user_id = key.split(" · ")[0].strip()
    if user_id not in USER_IDS:
        raise PermissionError("请选择有效的模拟用户")
    return user_id


def label_for_id(user_id: str) -> str:
    if user_id in USER_IDS:
        return USERS[USER_IDS.index(user_id)][1]
    return USERS[0][1]


def ensure_agent(state_dict: dict, user_key: str) -> CustomerServiceAgent:
    """Return the session agent, recreating it when the user changes.

    `settings` is keyword-only on CustomerServiceAgent. Passing it positionally
    raised `TypeError: __init__() takes 1 positional argument but 2 were given`
    on every single send, so this construction is pinned by a test.
    """
    user_id = user_id_from_key(user_key)
    agent = state_dict.get("agent")
    if agent is None or state_dict.get("user_id") != user_id:
        agent = CustomerServiceAgent(settings=SETTINGS, current_user_id=user_id)
        state_dict["agent"] = agent
        state_dict["user_id"] = user_id
    else:
        agent.set_user(user_id)
    state_dict["user_key"] = user_key
    return agent


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _truncate(value: Any, limit: int = 900) -> str:
    rendered = (
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)
    )
    if len(rendered) <= limit:
        return rendered
    return rendered[:limit] + f"\n… (+{len(rendered) - limit} chars)"


def render_header(user_key: str) -> str:
    clock = ("场景日期 " + SETTINGS.reference_date.isoformat() if SETTINGS.reference_date else "按当前日期核查")
    return (
        "<header class='cs-header' lang='zh'>"
        "<div class='cs-brand'><span class='cs-mark' aria-hidden='true'>服</span><div>"
        f"<div class='shop'>{html.escape(SHOP_NAME)}</div>"
        f"<div class='sub'>{html.escape(SHOP_SUBTITLE)}</div></div></div>"
        "<div class='meta'><span class='cs-badge'>合成业务演示</span>"
        f"<span>{html.escape(user_id_from_key(user_key))} · {clock}</span></div>"
        "</header>"
    )


def render_user_card(user_id: str, user_label: str) -> str:
    name = user_label.split("·")[0].strip()
    phone = user_label.split("·")[-1].strip() if "·" in user_label else ""
    initials = name[0] if name else "客"
    user_orders = BusinessDB(SETTINGS).list_orders(user_id)
    orders = "".join(
        f"<div class='cs-order'><b>{html.escape(order['order_id'])}</b> "
        f"<span>{html.escape(ORDER_STATUS.get(order['status'], order['status']))}"
        f" · ¥{order['paid_amount']:.2f}</span></div>"
        for order in user_orders
    )
    return (
        "<div class='cs-card'>"
        "<h4>当前会话</h4>"
        "<div class='cs-user'>"
        f"<div class='cs-avatar'>{html.escape(initials)}</div>"
        "<div>"
        f"<div class='nm'>{html.escape(name)}</div>"
        f"<div class='ph'>{html.escape(user_id)} · {html.escape(phone)}</div>"
        "</div></div>"
        "<div class='cs-hint' style='margin-top:9px'>"
        "只能查询该用户本人的订单，权限校验在数据层执行。</div>"
        "</div>"
        "<div class='cs-card'>"
        "<h4>订单列表</h4>"
        f"<div class='cs-orders'>{orders}</div>"
        "<div class='cs-hint' style='margin-top:8px'>"
        f"共 {len(user_orders)} 个订单，可向下滚动查看全部。</div>"
        "</div>"
    )


def render_database_overview() -> str:
    labels = {
        "users": "模拟用户", "products": "商品", "orders": "订单",
        "order_items": "订单明细", "logistics": "物流记录", "price_history": "价格记录",
    }
    cards = "".join(
        f"<div class='stat'><small>{labels[table]}</small><strong>{count}</strong></div>"
        for table, count in BusinessDB(SETTINGS).counts().items()
    )
    return f"<div class='stat-grid'>{cards}</div>"


def on_data_user_change(user_key: str) -> tuple:
    orders = BusinessDB(SETTINGS).list_orders(user_id_from_key(user_key))
    rows = [[o["order_id"], ORDER_STATUS.get(o["status"], o["status"]), o["paid_amount"],
             o["created_at"], o["shipped_at"] or "—", o["delivered_at"] or "—"]
            for o in orders]
    ids = [o["order_id"] for o in orders]
    return rows, gr.update(choices=ids, value=ids[0] if ids else None)


def on_order_change(order_id: str | None, user_key: str) -> tuple:
    db = BusinessDB(SETTINGS)
    user_id = user_id_from_key(user_key)
    order = db.get_order(order_id or "", user_id)
    if not order["found"]:
        return f"<div class='cs-card'>{html.escape(order['reason'])}</div>", [], []
    fields = [
        ("订单", order_id), ("状态", ORDER_STATUS.get(order["status"], order["status"])),
        ("实付金额", f"¥{order['paid_amount']:.2f}"), ("下单日期", order["created_at"]),
        ("签收日期", order["delivered_date"] or "尚未签收"), ("查询日期", order["today"]),
    ]
    detail = "<div class='stat-grid'>" + "".join(
        f"<div class='stat'><small>{label}</small>"
        f"<strong>{html.escape(str(value))}</strong></div>" for label, value in fields
    ) + "</div>"
    items = [[i["product_id"], i["product_name"] + (
        " · " + i["sku"]["color"] if i.get("sku") and i["sku"].get("color") else ""
    ), i["quantity"], i["purchase_price"],
              i["current_price"], i["price_delta"]] for i in order["items"]]
    logistics = db.get_logistics(order_id, user_id)
    if logistics["found"]:
        detail += (
            f"<p class='cs-hint'>承运商：{html.escape(logistics['carrier'])} · "
            f"运单号：{html.escape(logistics['tracking_no'])}</p>"
        )
        trace = [[t["time"], t["text"]] for t in logistics["trace"]]
    else:
        detail += f"<p class='cs-hint'>{html.escape(logistics['reason'])}</p>"
        trace = []
    return detail, items, trace


def product_rows() -> list[list]:
    db = BusinessDB(SETTINGS)
    products = [db.get_product(p["product_id"]) for p in db.list_products()]
    return [[p["product_id"], p["name"], p["category"], p["current_price"],
             p["price_changed_at"]] for p in products]


def sku_rows() -> list[list]:
    db = BusinessDB(SETTINGS)
    status = {"available": "可购买", "unavailable": "不可购买", "unknown": "未知"}
    return [[sku["sku_id"], product["name"], sku["color"] or "未知",
             sku["wearing_type"] or "不适用", {1: "支持", 0: "不支持"}.get(sku["anc"], "未知"),
             status[sku["availability"]], product["current_price"]]
            for row in db.list_products() for product in [db.get_product(row["product_id"])]
            for sku in product["skus"]]


def price_rows(product_id: str) -> list[list]:
    history = BusinessDB(SETTINGS).get_price_history(product_id)
    return [[r["changed_at"], r["price"]] for r in history.get("records", [])]


def on_eval_category(category: str, report: dict) -> tuple:
    rows = case_rows(report, category)
    return rows, gr.update(choices=[r[0] for r in rows], value=rows[0][0])


def on_try_case(case_id: str, report: dict, state_dict: dict, user_key: str) -> tuple:
    case = next(c for c in report["cases"] if c["id"] == case_id)
    packed = list(on_clear(state_dict, user_key))
    packed[1] = case["query"]
    return (*packed, gr.update(selected="chat"))


def format_retrieval_score(document: dict) -> str:
    kind = {"cosine": "Dense", "bm25": "BM25", "rrf": "RRF", "reranker": "Reranker"}.get(
        document.get("score_type", "cosine"), "score",
    )
    ranks = " / ".join(f"{name} #{data['rank']}"
                       for name, data in document.get("retrieval", {}).items())
    return f"{kind} {document.get('score', 0):.4f}" + (f"；{ranks}" if ranks else "")


def render_trace(result: AgentResult | None, error: str | None = None) -> str:
    if error:
        return (
            "<div class='trace-panel'>"
            "<div class='err-box'>前端调用出错（已捕获，服务未中断）：\n"
            f"{html.escape(error)}</div></div>"
        )
    if result is None:
        return (
            "<div class='trace-panel'><div class='trace-empty'>"
            "尚未发送消息。<br><br>"
            "左侧提问后，这里会显示 Agent 的完整执行轨迹：<br>"
            "· 调用了哪些工具、参数是什么<br>"
            "· 工具返回了什么<br>"
            "· 知识检索命中了哪些章节、各路排名和分数<br>"
            "· 每步实际进展和最终结果<br><br>"
            "这里展示的是<b>可观测的执行过程</b>，不包含模型的隐藏思维过程。"
            "</div></div>"
        )

    running = result.stop_reason == "running"
    badge = {"running": "warn", "answered": "ok", "max_steps": "warn", "llm_error": "bad",
             "state_check_error": "bad"}.get(
        result.stop_reason, ""
    )
    parts: list[str] = ["<div class='trace-panel'>"]
    parts.append(
        f"<span class='pill {badge}'>stop: {html.escape(result.stop_reason)}</span>"
        f"<span class='pill'>steps: {result.steps}</span>"
        f"<span class='pill'>tool calls: {result.tool_calls}</span>"
    )
    parts.append(
        "<div class='trace-step final'><div class='trace-head'>决策摘要</div>"
        f"<div class='trace-body'>{html.escape('执行中，步骤随运行实时更新。' if running else result.decision_summary())}</div></div>"
    )

    for step_no, event in enumerate(result.trace, start=1):
        if event.type == "tool_call":
            title = f"Step {step_no} · 工具调用 <code>{html.escape(event.label)}</code>"
        elif event.type == "tool_result":
            title = f"Step {step_no} · 工具返回 <code>{html.escape(event.label)}</code>"
        elif event.type == "retrieval":
            docs = event.detail.get("documents", [])
            title = (
                f"Step {step_no} · 知识检索 "
                f"<code>{html.escape(str(event.detail.get('query', '')))}</code>"
                f" · {len(docs)} 条命中"
            )
        elif event.type == "error":
            title = f"Step {step_no} · 错误 <code>{html.escape(event.label)}</code>"
        elif event.type == "ui_action":
            title = f"Step {step_no} · 页面操作 <code>{html.escape(event.label)}</code>"
        elif event.type == "state_check":
            title = f"Step {step_no} · 服务端状态检查 <code>{html.escape(event.label)}</code>"
        else:
            title = f"Step {step_no} · 最终回答"

        detail = event.detail
        if event.type == "tool_call":
            body = _truncate(detail.get("arguments", ""))
        elif event.type == "retrieval":
            docs = detail.get("documents", [])
            body = (
                "\n".join(
                    f"[{format_retrieval_score(d)}] {d.get('title', '')} "
                    f"({d.get('chunk_id', '')})\n    {_truncate(d.get('content', ''), 200)}"
                    for d in docs
                )
                or "（无命中）"
            )
        elif event.type == "final":
            body = _truncate(detail.get("text", ""))
        elif event.type == "error":
            body = _truncate(detail)
        else:
            body = _truncate(detail.get("result", detail))

        css_class = event.type.replace("_", "-")
        if css_class not in {"call", "result", "retrieval", "error", "final", "state-check"}:
            css_class = ""
        parts.append(
            f"<div class='trace-step {css_class}'>"
            f"<div class='trace-head'>{title}</div>"
            f"<div class='trace-body'>{html.escape(body)}</div></div>"
        )

    if result.retrieved_documents:
        parts.append(
            "<div style='margin-top:9px'><b style='font-size:12.5px'>累计命中文档</b>"
            + "".join(
                "<div class='trace-body'>· "
                f"[{html.escape(format_retrieval_score(d))}] {html.escape(str(d.get('title', '')))} "
                f"<span class='mono-key'>({html.escape(str(d.get('chunk_id', '')))})</span>"
                "</div>"
                for d in result.retrieved_documents
            )
            + "</div>"
        )

    if result.errors:
        parts.append(
            f"<div class='err-box'>{html.escape(chr(10).join(result.errors))}</div>"
        )

    parts.append("</div>")
    return "".join(parts)


def render_stats(result: AgentResult | None) -> str:
    if result is None:
        cells = [(label, "—") for label in (
            "输入 token", "输出 token", "总 token",
            "估算成本", "工具调用", "检索次数", "命中文档",
        )]
    else:
        totals = result.totals or {}
        cost_label = "已定价成本（部分）" if totals.get("unpriced_models") else "估算成本"
        cells = [
            ("输入 token", f"{totals.get('prompt_tokens', 0):,}"),
            ("输出 token", f"{totals.get('completion_tokens', 0):,}"),
            ("总 token（部分）" if totals.get("tokens_are_partial") else "总 token",
             f"{totals.get('total_tokens', 0):,}"),
            (cost_label, f"¥{totals.get('cost_cny', 'n/a')}"),
            ("工具调用", str(result.tool_calls)),
            ("检索次数", str(len(result.retrieval_queries))),
            ("命中文档", str(len(result.retrieved_documents))),
        ]
        budget, context = totals.get("budget"), totals.get("context")
        if budget:
            cells.append(("API 请求", f"{budget['api_requests']} / {budget['max_api_requests']}"))
        if context:
            cells.extend([
                ("首次输入 / 字符", f"{context['input_chars']:,}"),
                ("裁剪旧历史 / 轮", str(context["dropped_turns"])),
            ])
    cards = "".join(
        f"<div class='stat'><small>{label}</small><strong>{value}</strong></div>"
        for label, value in cells
    )
    return f"<div class='stat-grid'>{cards}</div>"


def runtime_evaluation_rows(folder: Path) -> list[list]:
    summary = json.loads((folder / "summary.json").read_text())
    frozen = json.loads((folder / "frozen_manifest.json").read_text())
    for name, expected in (("raw.jsonl", summary["raw_sha256"]),
                           ("cases.json", frozen["cases_sha256"])):
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != expected:
            raise ValueError("运行评测快照与来源不一致")
    return [["v2 合成开发场景", summary["cases"], summary["execution_checks_passed"],
             summary["api_requests"], summary["total_tokens"]]]


def render_reply_status(elapsed_ms: float, *, running: bool = False, failed: bool = False) -> str:
    label = "正在回答 · 已用" if running else ("回答失败 · 用时" if failed else "回答完成 · 用时")
    state = "running" if running else "done"
    return (
        f"<div class='cs-hint' data-reply-state='{state}' data-elapsed-ms='{elapsed_ms:.1f}'>"
        f"{label} {elapsed_ms / 1000:.1f} s</div>"
    )


REPLY_CLOCK_JS = """
let started = null;
let timer = null;
watch('value', () => {
    clearInterval(timer);
    const status = element.querySelector('[data-reply-state]');
    if (!status || status.dataset.replyState !== 'running') {
        started = null;
        return;
    }
    if (started === null) started = performance.now() - Number(status.dataset.elapsedMs);
    const update = () => {
        if (!element.isConnected) { clearInterval(timer); return; }
        const seconds = ((performance.now() - started) / 1000).toFixed(1);
        status.textContent = `正在回答 · 已用 ${seconds} s`;
    };
    update();
    timer = setInterval(update, 100);
});
"""


TRACE_FOLLOW_JS = """
watch('value', () => {
    const steps = element.querySelectorAll('.trace-step');
    const latest = steps[steps.length - 1];
    if (!latest) return;
    const panel = element.parentElement;
    panel.scrollTop += latest.getBoundingClientRect().top - panel.getBoundingClientRect().top;
});
"""


# --------------------------------------------------------------------------- #
# event handlers -- module level so they can be tested
# --------------------------------------------------------------------------- #
# Every update includes chat, input, trace, session, metrics, user card, header, reply status.
def pack(
    history: list[dict],
    trace: str,
    state_dict: dict,
    stats: str,
    card: str,
    header: str,
    reply_status: str = "",
):
    """Single place that defines the handler return arity.

    Must stay in step with the `outputs` list in `build_app`:
        chat, message box, trace, state, stats, sidebar card, header, reply status
    """
    return list(history), "", trace, state_dict, stats, card, header, reply_status


def render_return_task(view: dict | None) -> str:
    task = (view or {}).get("task")
    if (view or {}).get("state_unavailable"):
        content = "当前退货草稿状态无法读取，请重新载入核对，勿重复提交。"
    elif not task:
        content = "尚无退货草稿，可在对话中描述需要退货的商品。"
    elif task["status"] == "cancelled":
        content = "退货草稿已中止；订单未取消。"
    elif task["status"] == "submitted":
        content = ("已创建售后申请：" + html.escape(task["request_id"]) +
                   "<p>尚未退款，可查询实际申请状态。</p>")
    else:
        labels = {"order_id": "订单", "item_id": "商品明细", "quantity": "数量", "reason": "原因",
                  "condition": "状况（自述）", "problem_description": "问题（自述）",
                  "quality_evidence": "质量照片或视频", "secondary_sale_ok": "二次销售条件声明",
                  "quality_non_user_damage": "非人为损坏声明"}
        values = {"no_reason": "无理由", "quality": "质量问题", "sealed": "未拆封", "opened": "已拆封"}
        order = view.get("order") or {}
        item = next((i for i in order.get("items", []) if i["id"] == task["item_id"]), {})
        content = ""
        for field, label in labels.items():
            if field in task and task[field] is not None:
                value = item.get("product_name", task[field]) if field == "item_id" else task[field]
                value = values.get(str(value), value)
                if type(value) is bool:
                    value = "已声明符合" if value else "声明不符合"
                content += f"<div>{label}：{html.escape(str(value))}</div>"
        missing = "、".join(labels[f] for f in view["missing_fields"])
        content += f"<p>待补充：{missing}</p>" if missing else "<p>基本信息已记录</p>"
        if task.get("evidence_id"):
            content += "<p>质量凭证已保存，待审核</p>"
        content += "<small>用户自述待审核 · 尚未提交</small>"
    return ("<div class='cs-card' id='return-task' style='overflow-wrap:anywhere'>"
            "<b>当前退货办理</b><div>" + content + "</div></div>")


def _card_for(user_key: str, task_view: dict | None = None) -> str:
    user_id = user_id_from_key(user_key)
    return render_user_card(user_id, label_for_id(user_id)) + render_return_task(task_view)


def on_send(message: str, history: list[dict] | None, state_dict: dict, user_key: str):
    user_id = user_id_from_key(user_key)
    store = session_store()
    sid = state_dict.get("session_id")
    # Browser history is display data, never trusted model context.
    history = []
    card = _card_for(user_key)
    if not (message or "").strip():
        if sid:
            history = store.load(sid, user_id)["history"]
        yield pack(
            history,
            render_trace(None),
            state_dict,
            render_stats(None),
            card,
            render_header(user_key),
        )
        return

    started = perf_counter()
    reply_index = len(history)
    try:
        service = ChatService(SETTINGS, store)
        with closing(service.run_turn(
            message, user_id, sid, agent=lambda: ensure_agent(state_dict, user_key),
        )) as stream:
            for update in stream:
                state_dict["session_id"] = update.session_id
                sid = update.session_id
                history = update.history
                reply_index = len(history) - int(bool(update.result.answer))
                result = update.result
                card = _card_for(user_key, update.task_view)
                status = ("<div class='cs-hint' role='status'>正在排队，当前最多同时处理两个会话。</div>"
                          if update.phase == "queued" else render_reply_status(
                              update.elapsed_ms, running=update.phase == "running",
                              failed=result.stop_reason not in {"running", "answered"},
                          ))
                yield pack(history, render_trace(result), state_dict, render_stats(result),
                           card, render_header(user_key), status)
    except (PermissionError, ConversationBusy) as exc:
        if isinstance(exc, ConversationBusy):
            history = store.load(sid, user_id)["history"]
        else:
            state_dict.clear()
        yield pack(history, render_trace(None, error=str(exc)), state_dict, render_stats(None),
                   card, render_header(user_key), render_reply_status(0, failed=True))
    except Exception as exc:  # noqa: BLE001 - the UI must never go blank
        detail = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc(limit=6)}"
        recovered = False
        try:
            if sid:
                card = _card_for(user_key, store.get_return_task(sid, user_id))
                saved = store.load(sid, user_id)
                if (not saved["running"] and len(saved["history"]) >= 2
                        and len(saved["history"]) == reply_index + 1
                        and saved["history"][-2].get("content") == message):
                    history, recovered = saved["history"], True
        except Exception:  # noqa: BLE001 - never reuse a stale empty/success card
            card = render_return_task({"state_unavailable": True})
        if not recovered:
            partial = history[reply_index]["content"] + "\n\n" if len(history) > reply_index else ""
            history[reply_index:] = [{
                "role": "assistant",
                "content": partial + "抱歉，系统出现异常，本次回答未完成。当前草稿状态无法核实，请重新载入核对，勿重复提交。当前没有人工接入此窗口的功能。",
            }]
        yield pack(history, render_trace(None, error=detail), state_dict, render_stats(None),
                   card, render_header(user_key),
                   render_reply_status((perf_counter() - started) * 1000, failed=True))


def on_clear(state_dict: dict, user_key: str):
    state_dict.clear()
    state_dict["session_id"] = session_store().create(user_id_from_key(user_key))
    return pack(
        [],
        render_trace(None),
        state_dict,
        render_stats(None),
        _card_for(user_key),
        render_header(user_key),
    )


def on_history_choices(state_dict: dict, user_key: str):
    user_id = user_id_from_key(user_key)
    sessions = session_store().list(user_id)
    ids = {s["session_id"] for s in sessions}
    choices = [(f"{s['title']} · " + datetime.fromisoformat(s["updated_at"]).astimezone(
        ZoneInfo("Asia/Shanghai"),
    ).strftime("%m-%d %H:%M"), s["session_id"]) for s in sessions]
    return gr.update(choices=choices,
                     value=state_dict.get("session_id") if state_dict.get("session_id") in ids else None)


def on_restore(sid: str | None, state_dict: dict, user_key: str):
    if sid is None:
        state_dict.clear()
        return pack([], render_trace(None), state_dict, render_stats(None), _card_for(user_key),
                    render_header(user_key), "可选择历史会话，或开始新会话。")
    snapshot = session_store().load(sid, user_id_from_key(user_key))
    state_dict.clear()
    state_dict["session_id"] = sid
    record = snapshot["last_result"]
    result = AgentResult(**(record | {"trace": [TraceEvent(**e) for e in record.get("trace", [])]})) if record else None
    status = "会话已恢复，可继续提问。" if not snapshot["running"] else "此会话仍在处理，请稍后重新载入。"
    return pack(snapshot["history"], render_trace(result), state_dict, render_stats(result),
                _card_for(user_key, session_store().get_return_task(sid, user_id_from_key(user_key))),
                render_header(user_key), status)


def on_load(user_key: str):
    state = {}
    return (*pack([], render_trace(None), state, render_stats(None), _card_for(user_key),
                  render_header(user_key), "选择历史会话可继续，或直接发送以开始新会话。"),
            *on_data_user_change(user_key), on_history_choices(state, user_key))


def return_panel(state_dict: dict, user_key: str):
    uid, sid = user_id_from_key(user_key), state_dict.get("session_id")
    service = ReturnService(SETTINGS, session_store())
    task = session_store().get_return_task(sid, uid)["task"] if sid else None
    preview = service.latest_preview(sid, uid) if sid else None
    content = "<p>先在聊天中确定退货商品，再检查申请信息。</p>"
    if task and task["status"] == "submitted":
        content = "<p>这份申请已创建，可在下方查询实际进度；新申请请新建会话。</p>"
    elif task:
        content = "<p>请补齐当前申请信息，并核查生成有效预览；更改后需要重新核查。</p>"
    if preview:
        item = preview["content"]
        reason = "无理由" if item["reason"] == "no_reason" else "质量问题"
        content = (f"<p><b>{html.escape(item['product_name'])} × {item['quantity']}</b></p>"
                   f"<p>订单：{html.escape(item['order_id'])} · 原因：{reason}</p>"
                   "<p>商品状况与故障是用户陈述，尚未审核。确认后创建待审核申请，不代表退款。</p>"
                   "<p>预览10分钟内有效；修改信息后须重新生成并确认。</p>")
        if item.get("sku"):
            content += "<p>所购颜色：" + html.escape(item["sku"].get("color") or "未知") + "</p>"
        if item.get("problem_description"):
            content += "<p>故障（自述）：" + html.escape(item["problem_description"]) + "</p>"
        if item.get("condition"):
            content += "<p>商品状况（自述）：" + (
                "未拆封" if item["condition"] == "sealed" else "已拆封"
            ) + "</p>"
        if item["reason"] == "no_reason":
            content += "<p>本次声明符合以下条件（待审核）：</p><ul>" + "".join(
                "<li>" + html.escape(line) + "</li>"
                for line in item["policy"].get("required_conditions",
                                               item["policy"].get("secondary_sale_conditions", []))
            ) + "</ul>"
        else:
            content += "<p>本次用户声明：故障非人为损坏，尚未核实。</p>"
        if item.get("evidence"):
            content += "<p>照片或视频已保存，尚未核实内容。</p>"
    requests = service.list_requests(uid)
    rows = "".join(
        f"<p>{html.escape(r['request_id'])}<br>{html.escape(r['order_id'])} · "
        f"{r['quantity']}件 · {RETURN_STATUS[r['status']]}</p>"
        + (f"<p>审核原因：{html.escape(r['review']['reason'])}<br>"
           f"审核时间：{html.escape(r['review']['reviewed_at'])}</p>" if r["review"] else "")
        + ("<p>允许退回商品，尚未退款。</p>" if r["status"] == "approved" else "")
        for r in requests
    ) or "<p>当前用户暂无售后申请。</p>"
    draft = bool(task and task["status"] == "draft")
    return ("<div class='cs-card' id='return-preview'>" + content + "</div>",
            preview["preview_id"] if preview else None,
            gr.update(interactive=bool(preview)),
            gr.update(value=bool(task and task.get("secondary_sale_ok")), interactive=draft),
            gr.update(value=bool(task and task.get("quality_non_user_damage")), interactive=draft),
            gr.update(interactive=bool(draft and task["reason"] == "quality"), **(
                {"value": None} if not task or not task.get("evidence_id") else {}
            )),
            "<div id='return-requests' style='max-height:180px;overflow-y:auto;"
            "overflow-wrap:anywhere'>" + rows + "</div>", gr.update(interactive=draft))


def _local_return_action(state_dict: dict, user_key: str, text: str, operation, describe):
    uid, store = user_id_from_key(user_key), session_store()
    sid, tid, finished, answer = state_dict.get("session_id"), None, False, ""
    started = perf_counter()
    result = AgentResult(answer="")
    try:
        if not sid:
            sid = store.create(uid)
            state_dict["session_id"] = sid
        tid = store.begin_turn(sid, uid, text)
        payload = operation(ReturnService(SETTINGS, store), sid, uid, tid)
        answer = describe(payload)
        result.trace = [TraceEvent("ui_action", text, {"result": payload})]
        if payload.get("ready") is False:
            result.stop_reason = "blocked"
    except PermissionError as exc:
        state_dict.clear()
        return pack([], render_trace(None, str(exc)), state_dict, render_stats(None),
                    _card_for(user_key), render_header(user_key))
    except (TaskValidationError, ConversationBusy) as exc:
        answer = str(exc)
        result.stop_reason = "blocked"
        result.errors = [str(exc)]
        result.trace = [TraceEvent("error", text, {"message": str(exc)})]
    except Exception as exc:  # noqa: BLE001 - commit may have succeeded before response loss
        answer = "本次操作未完整返回，请点击查询提交结果核查后再重试。"
        result.stop_reason = "interrupted"
        result.errors = [type(exc).__name__]
    result.answer = answer
    result.totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                     "cost_cny": "0", "latency_ms": (perf_counter() - started) * 1000}
    try:
        if tid:
            store.finish_turn(sid, uid, tid, [{"role": "user", "content": text},
                                            {"role": "assistant", "content": answer}], asdict(result))
            finished = True
    finally:
        if tid and not finished:
            store.interrupt_turn(sid, uid, tid, answer or "操作中断，请查询提交结果")
    history = store.load(sid, uid)["history"]
    packed = list(pack(history, render_trace(result), state_dict, render_stats(result),
                       _card_for(user_key, store.get_return_task(sid, uid)), render_header(user_key),
                       render_reply_status((perf_counter() - started) * 1000,
                                           failed=result.stop_reason in {"blocked", "interrupted"})))
    packed[1] = gr.update()  # A form action must preserve an unsent chat message.
    return tuple(packed)


def on_return_declaration(state_dict: dict, user_key: str, secondary: bool, non_user_damage: bool):
    def declare(service, sid, uid, tid):
        _require_return_draft(service.store, sid, uid)
        return service.store.update_return_task(sid, uid, tid, {
            "secondary_sale_ok": True if secondary else None,
            "quality_non_user_damage": True if non_user_damage else None,
        })

    return _local_return_action(
        state_dict, user_key,
        "[操作] 更正条件声明：" + _declaration_text(secondary, non_user_damage),
        declare, lambda _: "条件声明已更新；原预览失效，请重新生成并检查。",
    )


def _require_return_draft(store: SessionStore, sid: str, uid: str) -> None:
    task = store.get_return_task(sid, uid)["task"]
    if not task or task["status"] != "draft":
        raise TaskValidationError("请先准备未提交的草稿；已创建申请可查询结果，不能修改原申请。")


def on_return_preview(state_dict: dict, user_key: str, secondary: bool, non_user_damage: bool):
    def prepare(service, sid, uid, tid):
        _require_return_draft(service.store, sid, uid)
        service.store.update_return_task(sid, uid, tid, {
            "secondary_sale_ok": True if secondary else None,
            "quality_non_user_damage": True if non_user_damage else None,
        })
        return service.preview(sid, uid, tid)

    return _local_return_action(
        state_dict, user_key,
        "[操作] 请求申请预览：" + _declaration_text(secondary, non_user_damage),
        prepare, lambda p: "申请预览已生成，请检查后点击确认提交。尚未创建售后单。"
        if p["ready"] else "暂不能提交：" + "；".join(p["reasons"]),
    )


def _declaration_text(secondary: bool, non_user_damage: bool) -> str:
    return ("声明商品符合二次销售条件" if secondary else "未声明二次销售条件") + "；" + (
        "声明故障非人为损坏" if non_user_damage else "未声明非人为损坏"
    )


def on_return_confirm(preview_id: str | None, state_dict: dict, user_key: str):
    return _local_return_action(
        state_dict, user_key, "[操作] 点击确认提交当前申请",
        lambda s, sid, uid, tid: s.confirm_and_submit(sid, uid, preview_id or "", tid),
        lambda p: f"订单 {p['order_id']} 的售后申请 {p['request_id']} 已创建，"
        f"当前状态{RETURN_STATUS[p['status']]}，尚未退款。",
    )


def on_return_upload(data: bytes | None, state_dict: dict, user_key: str):
    return _local_return_action(
        state_dict, user_key, "[操作] 用户上传质量问题照片或视频",
        lambda s, sid, uid, tid: s.attach_evidence(sid, uid, tid, data),
        lambda _: "质量凭证已保存。上传不等于核实故障，还需人工审核。",
    )


def on_return_clear_upload(state_dict: dict, user_key: str):
    sid = state_dict.get("session_id")
    if not sid:
        return (*[gr.update() for _ in range(3)], state_dict,
                *[gr.update() for _ in range(4)])
    try:
        task = session_store().get_return_task(sid, user_id_from_key(user_key))["task"]
        if not task or not task.get("evidence_id"):
            return (*[gr.update() for _ in range(3)], state_dict,
                    *[gr.update() for _ in range(4)])
    except PermissionError:
        pass  # Let the action boundary return an owned, empty UI state.
    return _local_return_action(
        state_dict, user_key, "[操作] 撤下当前草稿的质量凭证",
        lambda s, sid, uid, tid: s.clear_evidence(sid, uid, tid),
        lambda _: "已撤下当前草稿凭证，原预览失效；如需继续质量申请，请重新上传。",
    )


def on_return_query(state_dict: dict, user_key: str):
    return _local_return_action(
        state_dict, user_key, "[操作] 查询提交结果",
        lambda s, sid, uid, tid: {"requests": s.list_requests(uid)},
        lambda p: "；".join(f"{r['request_id']}：{RETURN_STATUS[r['status']]}" for r in p["requests"])
        or "当前用户暂无售后申请。",
    )


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #
CSS = """
.gradio-container {
  max-width: 1600px !important; padding: clamp(8px, 2vw, 24px) !important;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  color: #172033; background: #edf0f4;
}
.gradio-container .main { padding:0 !important; }
.skip-link { position:fixed; left:16px; top:-60px; z-index:100; padding:12px; background:white; }
.skip-link:focus { top:8px; }
#skip-navigation { position:absolute; height:0; min-height:0; padding:0; }
#shop-header, #chat-title { padding:0; min-height:0; }
#reply-status { padding:0; min-height:20px; font-size:12px; color:#536174; }
button { min-height:40px; border-radius:8px !important; }
button:active:not(:disabled) { transform:scale(.96); }
button:focus-visible, input:focus-visible, textarea:focus-visible {
  outline:2px solid #c64b06 !important; outline-offset:3px;
}
button.primary { background:#c64b06 !important; color:white !important; }
.cs-header { padding:4px 0 16px; display:flex; align-items:center; justify-content:space-between; gap:16px; }
.cs-brand { display:flex; gap:12px; align-items:center; }
.cs-mark { background:#172033; color:#fff; width:40px; height:40px; border-radius:8px;
           display:grid; place-items:center; font-size:18px; font-weight:600; }
.cs-header .shop { font-size:20px; font-weight:650; color:#172033; }
.cs-header .sub { font-size:12px; color:#536174; margin-top:3px; }
.cs-header .meta { display:flex; flex-direction:column; gap:6px; align-items:flex-end;
                   color:#536174; font-size:12px; }
.cs-badge { padding:3px 8px; background:#fff1e7; color:#923a0b; border-radius:99px; }
#project-tabs { border:none; }
#project-tabs > .tab-nav { margin-bottom:12px; border-bottom:1px solid #dbe1e8; }
#workspace { display:grid !important; grid-template-columns:210px minmax(0,1fr) 310px;
             align-items:stretch !important;
             gap:0 !important; height:calc(100dvh - 200px); min-height:600px;
             background:#fff; border:1px solid #dbe1e8; border-radius:12px; overflow:hidden; }
#conversation-nav, #chat-workspace, #business-inspector { min-width:0 !important; min-height:0; gap:12px; }
#conversation-nav { padding:16px !important; background:#e5e9ef; border-right:1px solid #dbe1e8;
                    flex-wrap:nowrap; overflow-y:auto; }
#conversation-nav .block { background:transparent; border:0; }
#user-selector { padding:0; }
#workspace [data-testid='block-info'], #workspace [data-testid='block-label'] {
  background:transparent !important; color:#536174 !important; font-size:12px; }
#business-inspector { padding:16px !important; background:#f7f8fa; border-left:1px solid #dbe1e8;
                      flex-wrap:nowrap; overflow-y:auto; overscroll-behavior:contain; }
#business-inspector > * { flex:0 0 auto; }
#chat-workspace { padding:16px !important; background:#fff; flex-wrap:nowrap; }
.cs-section-label { font-size:12px; color:#536174; font-weight:650; }
#conversation-history { background:transparent; border:0; padding:0; }
#conversation-history .wrap { flex-direction:column; max-height:calc(100dvh - 440px); overflow-y:auto; }
#conversation-history label { background:transparent; border:0; box-shadow:none; width:100%;
                              margin:0; font-size:12px; padding:9px 6px; overflow-wrap:anywhere; }
#conversation-history label, #conversation-history label * { color:#172033 !important; }
#conversation-history label:has(input:checked) { background:#fff1e7; }
#conversation-history input { flex-shrink:0; }
#history-note, #example-note { font-size:12px; color:#536174; }
#customer-chat { overflow:hidden !important; border:0; border-radius:0;
                 flex:1 1 0; height:auto !important; min-height:220px; background:#fff; }
#customer-chat .bubble-wrap { overscroll-behavior:contain; }
#customer-chat .message { font-size:14px; line-height:1.75; }
#composer { flex:0 0 auto !important; gap:8px; background:#fff;
            padding-top:12px; border-top:1px solid #dbe1e8; }
#composer-row { align-items:stretch; gap:8px; }
#message-input { min-width:0 !important; }
#message-input textarea { font-size:14px; line-height:1.6; border-radius:8px; }
#send-message { align-self:stretch; flex-grow:0; }
.quick-prompts { gap:6px !important; }
.quick-prompts button { background:#f7f8fa; font-size:12px; border:1px solid #dbe1e8;
                        box-shadow:none; }
.cs-welcome { padding:40px 16px; max-width:420px; margin:auto; color:#536174; }
.cs-welcome .cs-kicker { font-size:12px; color:#923a0b; }
.cs-welcome h2 { font-size:24px; font-weight:600; color:#172033; margin:12px 0; }
.cs-welcome p { font-size:14px; line-height:1.8; margin-bottom:20px; }
.cs-welcome small { font-size:12px; line-height:1.75; }
.cs-card { padding:0 0 16px; border-bottom:1px solid #dbe1e8; margin-bottom:16px; }
.cs-card h4 { margin:0 0 10px; font-size:14px; color:#172033; font-weight:600; }
.cs-user { display:flex; align-items:center; gap:10px; }
.cs-avatar { width:34px; height:34px; border-radius:50%; background:#fbe4d5; color:#923a0b;
             display:grid; place-items:center; font-weight:600; font-size:14px; flex-shrink:0; }
.cs-user .nm { font-size:14px; font-weight:600; color:#172033; }
.cs-user .ph { font-size:12px; color:#536174; }
.cs-hint { font-size:12px; color:#536174; line-height:1.7; overflow-wrap:anywhere; }
.cs-orders { display:flex; flex-direction:column; max-height:190px; overflow-y:auto;
             overscroll-behavior:contain; }
.cs-order { border-bottom:1px solid #e2e6ed; padding:9px 0; flex-shrink:0; font-size:12px;
            color:#172033; display:flex; justify-content:space-between; gap:8px; }
.cs-order b { font-variant-numeric:tabular-nums; font-weight:600; }
.cs-order span { color:#536174; text-align:right; }
.cs-panel-title { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between;
                  gap:8px; padding-bottom:12px; border-bottom:1px solid #dbe1e8; }
.cs-panel-title b { font-size:14px; color:#172033; }
.cs-panel-title span { font-size:12px; color:#536174; }
.cs-online::before { content:"●"; color:#187348; margin-right:5px; font-size:10px; }
#developer-observations { margin-top:16px; padding:16px; background:#fff; border-radius:8px; }
@media (max-width:1100px) {
  #workspace { grid-template-columns:190px minmax(0,1fr) 285px; }
}
@media (max-width:900px) {
  #workspace { grid-template-columns:minmax(0,1fr); height:auto; min-height:0; overflow:visible; }
  #conversation-nav { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px;
                       border-right:0; border-bottom:1px solid #dbe1e8; }
  #conversation-nav > :first-child, #conversation-history { grid-column:1 / -1; }
  #conversation-history .wrap { max-height:100px; }
  #history-note { display:none; }
  #chat-workspace { height:720px; min-width:0 !important; }
  #business-inspector { overflow:visible; border-left:0; border-top:1px solid #dbe1e8; }
  .cs-header { align-items:flex-start; flex-wrap:wrap; }
  .cs-header .meta { flex-direction:row; align-items:center; flex-wrap:wrap; }
  .cs-header .shop { font-size:18px; }
}
@media (max-width:480px) {
  #conversation-nav, #chat-workspace, #business-inspector { padding:12px !important; }
  #chat-workspace { height:540px; }
  #send-message { min-width:64px !important; }
  #composer-row { flex-wrap:nowrap; }
  .cs-welcome { padding:24px 6px; }
  .cs-welcome h2 { font-size:21px; }
}
.trace-panel { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px;
               padding-right: 6px; }
.trace-step { border-top: 1px solid #dbe1e8; padding: 7px 10px; margin: 7px 0;
              background: #f8fafc; border-radius: 8px; }
.trace-step.call      { border-top-color: #2563eb; background: #eff6ff; }
.trace-step.result    { border-top-color: #16a34a; background: #f0fdf4; }
.trace-step.retrieval { border-top-color: #7c3aed; background: #f5f3ff; }
.trace-step.error     { border-top-color: #dc2626; background: #fef2f2; }
.trace-step.final     { border-top-color: #0f172a; background: #f1f5f9; }
.trace-step.state-check { border-top-color: #b45309; background: #fffbeb; }
.trace-head { font-weight: 600; margin-bottom: 4px; color:#0f172a; }
.trace-body { white-space: pre-wrap; word-break: break-word; color: #334155; line-height:1.5; }
.mono-key { color: #64748b; }
.trace-empty { color:#6b7280; font-size:12.5px; line-height:1.8; }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
             gap: 7px; }
.stat { background: #f8fafc; border:1px solid #eef0f3; border-radius: 7px; padding: 6px 9px; }
.stat small { display: block; color: #6b7280; font-size: 10.5px; margin-bottom:1px; }
.stat strong { font-size: 13.5px; color:#111827; }
.pill { display:inline-block; padding:1.5px 8px; border-radius:99px; font-size:11px;
        background:#e2e8f0; color:#334155; margin-right:5px; }
.pill.ok   { background:#dcfce7; color:#166534; }
.pill.warn { background:#fef3c7; color:#92400e; }
.pill.bad  { background:#fee2e2; color:#991b1b; }
.err-box {
  background:#fef2f2; border:1px solid #fecaca; color:#991b1b; border-radius:8px;
  padding:9px 12px; font-size:12.5px; line-height:1.7; margin-top:6px;
  font-family: ui-monospace, Menlo, monospace; white-space:pre-wrap;
}
.replay-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }
.replay-answer { font-size:13px; line-height:1.8; white-space:pre-wrap; overflow-wrap:anywhere; }
@media (max-width:900px) { .replay-grid { grid-template-columns:1fr; } }
"""


def build_app() -> gr.Blocks:
    session_store().recover_interrupted()
    # Build the index on first run so `python app.py` is genuinely the only step.
    if SETTINGS.knowledge_mode == "retrieval" and not (SETTINGS.index_dir / INDEX_FILE).exists():
        print("knowledge index missing - building it now (one-off) ...")
        KnowledgeBase(SETTINGS).build()

    with gr.Blocks(title="电商客服工作台") as demo:
        gr.HTML("<a class='skip-link' href='#customer-chat'>跳到客服对话</a>", elem_id="skip-navigation")
        header = gr.HTML(elem_id="shop-header")
        state = gr.State({})
        with gr.Tabs(selected="chat", elem_id="project-tabs") as tabs:
            with gr.Tab("客服对话", id="chat"):
                with gr.Row(elem_id="workspace"):
                    with gr.Column(scale=2, min_width=200, elem_id="conversation-nav"):
                        gr.HTML("<div class='cs-section-label'>会话记录</div>")
                        user_selector = gr.Dropdown(
                            [(f"{label.split(' · ')[1]} · {uid}", label)
                             for uid, label in zip(USER_IDS, USER_CHOICES, strict=True)],
                            value=USER_CHOICES[0], label="模拟用户", elem_id="user-selector",
                        )
                        clear_btn = gr.Button("＋ 新建会话", elem_id="new-conversation")
                        history_selector = gr.Radio(
                            choices=[], label="历史会话 · 选择后继续", value=None,
                            elem_id="conversation-history",
                        )
                        gr.Markdown("对话与草稿自动保存。新建会话会保留旧记录。", elem_id="history-note")
                        debug_toggle = gr.Checkbox(label="显示开发观察", value=False)

                    with gr.Column(scale=6, min_width=320, elem_id="chat-workspace"):
                        gr.HTML(
                            "<div class='cs-panel-title'><b>在线客服</b>"
                            "<span class='cs-online'>可咨询 · 可核查 · 可申请</span></div>",
                            elem_id="chat-title",
                        )
                        reply_status = gr.HTML("", elem_id="reply-status", js_on_load=REPLY_CLOCK_JS)
                        chatbot = gr.Chatbot(
                            height=440, elem_id="customer-chat", show_label=False,
                            placeholder=(
                                "<div class='cs-welcome' lang='zh'><span class='cs-kicker'>有什么需要帮您？</span>"
                                "<h2>从一个具体问题开始</h2>"
                                "<p>商品选择、订单物流、退货与价保，<br>我会根据本店资料和您的订单核查。</p>"
                                "<small>退货申请需要您检查并确认，提交后仍待审核。</small></div>"
                            ),
                        )
                        with gr.Column(elem_id="composer", min_width=0):
                            with gr.Row(elem_id="composer-row"):
                                message_box = gr.Textbox(
                                    show_label=False, placeholder="描述您的问题，或粘贴订单号…",
                                    lines=2, max_lines=5, scale=6, container=False,
                                    elem_id="message-input",
                                )
                                send_btn = gr.Button("发送", variant="primary", scale=1,
                                                     min_width=76, elem_id="send-message")
                            with gr.Row(elem_classes="quick-prompts"):
                                for label, text in DEMO_CASES[:3]:
                                    btn = gr.Button(label, size="sm", min_width=60)
                                    btn.click(lambda t=text: t, outputs=message_box, show_progress="hidden")
                            with gr.Row(elem_classes="quick-prompts"):
                                for label, text in DEMO_CASES[3:]:
                                    btn = gr.Button(label, size="sm", min_width=60)
                                    btn.click(lambda t=text: t, outputs=message_box, show_progress="hidden")
                            gr.Markdown("示例填入后发送；订单示例对应 USER001。", elem_id="example-note")

                    with gr.Column(scale=3, min_width=285, elem_id="business-inspector"):
                        gr.HTML("<div class='cs-section-label'>业务信息</div>")
                        user_card = gr.HTML()
                        with gr.Accordion("退货申请 · 检查并确认", open=True):
                            policy = return_policy(SETTINGS)
                            gr.Markdown("无理由退货的二次销售条件：\n" + "\n".join(
                                "- " + line for line in policy["secondary_sale_conditions"]
                            ))
                            secondary_box = gr.Checkbox(
                                label="我声明商品符合上述条件（仍需审核）", interactive=False,
                            )
                            damage_box = gr.Checkbox(
                                label="质量问题：我声明故障非人为损坏（仍需审核）", interactive=False,
                            )
                            evidence_file = gr.File(
                                label="质量凭证 · PNG/JPEG/WebP照片或MP4视频，最大10MB",
                                file_types=["image", "video"], type="binary", interactive=False,
                            )
                            preview_btn = gr.Button("核查并生成申请预览", interactive=False)
                            preview_html = gr.HTML(elem_id="return-preview-panel")
                            preview_state = gr.State(None)
                            confirm_btn = gr.Button("确认提交申请", variant="primary", interactive=False)
                            query_btn = gr.Button("查询提交结果")
                            gr.Markdown("我的售后申请 · 状态来自数据库")
                            requests_html = gr.HTML()

                with gr.Column(visible=False, elem_id="developer-observations") as observations:
                    gr.HTML("<div class='cs-panel-title'><b>开发观察</b>"
                            "<span>当前执行轨迹，不含隐藏思维</span></div>")
                    gr.HTML("<div class='cs-hint'>模型：" + html.escape(SETTINGS.llm_model)
                            + " · 知识方式：" + html.escape(SETTINGS.knowledge_mode)
                            + f" · 最多 {SETTINGS.max_steps} 个 step</div>")
                    stats_html = gr.HTML(render_stats(None))
                    trace_html = gr.HTML(
                        render_trace(None), elem_id="execution-trace", max_height=440,
                        js_on_load=TRACE_FOLLOW_JS,
                    )

            debug_toggle.change(lambda show: gr.update(visible=show), inputs=debug_toggle,
                                outputs=observations, show_progress="hidden", api_name=False)

            with gr.Tab("业务数据", id="data"):
                gr.Markdown("浏览当前 SQLite 合成数据，展示所选模拟用户的订单。")
                gr.HTML(render_database_overview())
                orders_table = gr.Dataframe(
                    [], headers=["订单号", "状态", "实付 / 元", "下单", "发货", "签收"],
                    interactive=False, max_height=300,
                )
                order_selector = gr.Dropdown(
                    choices=[], value=None,
                    label="查看订单详情",
                )
                order_detail = gr.HTML()
                items_table = gr.Dataframe(
                    [], headers=["商品 ID", "商品", "数量", "购买价", "当前价", "单件价差"],
                    label="订单明细 · 价差仅为金额事实，能否价保还需核对政策",
                    interactive=False, wrap=True,
                )
                logistics_table = gr.Dataframe(
                    [], headers=["日期", "物流节点"], label="物流时间线",
                    interactive=False, wrap=True,
                )
                products = product_rows()
                gr.Dataframe(
                    products, headers=["商品 ID", "商品", "分类", "当前价 / 元", "最近调价"],
                    label="商品目录", interactive=False, wrap=True,
                )
                gr.Dataframe(
                    sku_rows(), headers=["SKU", "商品", "颜色", "佩戴形式", "主动降噪", "可用状态", "现价 / 元"],
                    label="SKU 事实 · 旧目录未提供的颜色与库存保持未知，同型号颜色同价",
                    interactive=False, wrap=True, max_height=320,
                )
                product_selector = gr.Dropdown(
                    choices=[(p[1], p[0]) for p in products], value=products[0][0],
                    label="查看商品价格历史",
                )
                prices_table = gr.Dataframe(
                    price_rows(products[0][0]), headers=["生效日期", "售价 / 元"],
                    interactive=False, label="价格历史（最新在前）", max_height=250,
                )

            with gr.Tab("评测进展", id="evaluation"):
                gr.Markdown("**运行服务开发检查** · 独立 v2 合成场景，非未见留出。")
                try:
                    runtime_rows = runtime_evaluation_rows(Path("results/runtime_service/online_final"))
                except (FileNotFoundError, ValueError) as exc:
                    gr.Markdown(f"运行服务开发检查尚不可用：{html.escape(str(exc))}")
                else:
                    gr.Dataframe(
                        runtime_rows, headers=["数据", "场景数", "执行检查通过", "API请求", "总token"],
                        label="只检查完成状态、必要工具、预算及业务副作用，不是回答正确率",
                        interactive=False,
                    )
                    gr.Markdown("原始回答、数据清单与实现hash保存在 results/runtime_service。"
                                "逐条复核与自动执行检查分别记录，不与下方旧知识库检索指标混比。")
                if SETTINGS.docs_dir != Path("data/docs").resolve():
                    gr.Markdown("**以下为原4型号、8篇知识的历史开发证据，不是当前 v2 知识库成绩。**")
                gr.Markdown("**小知识库与完整目录对照** · 自动检查和回答复核分别报告。")
                try:
                    knowledge_rows = catalog_knowledge_rows(db_path=SETTINGS.db_path)
                except (FileNotFoundError, ValueError) as exc:
                    gr.Markdown(f"小知识库对照尚不可用：{html.escape(str(exc))}")
                else:
                    gr.Dataframe(
                        knowledge_rows, headers=["方案", "场景数", "轮数", "自动检查通过", "事实/入口复核",
                                                 "工具调用", "记账 API 请求", "token",
                                                 "完整处理器耗时 p50 / ms", "估算成本 / 元"],
                        interactive=False, label="小知识库对照（开发场景）",
                    )
                    gr.Markdown("这是单遍、已见开发问题；关键词通过不是完整回答正确率。"
                                "事实/入口复核为逐条阅读诊断，未经外部人工独立审核；全文实验仍有额外事实和页面指引错误。"
                                "修复前未记录完整处理器耗时，不能与修复后三方案混比。"
                                "原始答案、复核与运行条件保存在 results/catalog_knowledge。")
                gr.Markdown("**重排与核验后补位** · 分开比较排序、证据覆盖和新增消耗。")
                try:
                    rerank_report = load_rerank_evaluation()
                except (FileNotFoundError, ValueError) as exc:
                    gr.Markdown(f"重排评测展示暂不可用：{html.escape(str(exc))}")
                else:
                    gr.Dataframe(
                        rerank_comparison_rows(rerank_report),
                        headers=["数据分组", "方案", "问题数", "排序 Recall@3",
                                 "核验后 Recall@3", "全部证据组覆盖", "库外问题接受",
                                 "配对检索 p50 / ms", "核验 token / 问题",
                                 "重排 token / 问题", "执行错误"],
                        interactive=False, label="重排与补位消融对照",
                    )
                    gr.Markdown(
                        "原 52 条已公开问题用于开发，另加 20 条冻结验证。"
                        "补位最多核验 10 个候选，最终交给客服的片段仍最多 3 个。"
                        "同一问题复用 embedding 与重排结果，耗时不含回答生成；"
                        "embedding 和重排未定价，成本统计不完整。"
                        "按冻结章节标签统计，标签可能遗漏充分替代章节，"
                        "未命中不能直接判作回答错误。"
                    )
                    rerank_available = [c["id"] for c in rerank_report["cases"]
                                        if c["split"] in rerank_report["runs"]]
                    rerank_state = gr.State(rerank_report)
                    rerank_selector = gr.Dropdown(
                        choices=rerank_available, value=rerank_available[0], label="回放重排与补位问题",
                    )
                    rerank_replay = gr.JSON(
                        rerank_case_view(rerank_report, rerank_available[0]),
                        label="原始候选、重排排名、核验池与最终证据（历史记录）",
                    )
                    rerank_selector.change(
                        rerank_case_view, inputs=[rerank_state, rerank_selector],
                        outputs=[rerank_replay],
                    )
                gr.Markdown(
                    "**章节级检索对照** · 使用相同问题与知识库，每次提供最多 3 个片段。"
                    "分别检查候选召回、前三名排序和证据核验后的结果。"
                )
                try:
                    retrieval_report = load_retrieval_evaluation()
                except (FileNotFoundError, ValueError) as exc:
                    gr.Markdown(f"检索评测展示暂不可用：{html.escape(str(exc))}")
                else:
                    gr.Dataframe(
                        retrieval_comparison_rows(retrieval_report),
                        headers=["数据分组", "方案", "问题数", "候选 Recall@10", "排序 Recall@3",
                                 "核验后 Recall@3", "有依据问题误拒", "库外问题接受",
                                 "配对检索 p50 / ms", "执行错误"],
                        interactive=False, label="Dense / BM25 / RRF 对照",
                    )
                    gr.Markdown(
                        "耗时复用同一次 embedding 的实测用时，包含证据核验，"
                        "未包含 Agent 回答生成；片段 token 长度可能不同。"
                        "本表衡量检索，不能直接当作回答正确率。验证结果公开后归入开发证据。"
                    )
                    available = [c["id"] for c in retrieval_report["cases"]
                                 if c["split"] in retrieval_report["runs"]]
                    retrieval_state = gr.State(retrieval_report)
                    retrieval_selector = gr.Dropdown(
                        choices=available, value=available[0], label="回放检索问题",
                    )
                    retrieval_replay = gr.JSON(
                        retrieval_case_view(retrieval_report, available[0]),
                        label="所需章节、各路候选与核验结果（历史记录）",
                    )
                    retrieval_selector.change(
                        retrieval_case_view, inputs=[retrieval_state, retrieval_selector],
                        outputs=[retrieval_replay],
                    )
                gr.Markdown(
                    "**新增验证与开发回归** · 首次新增验证集与公开开发集分开报告。"
                    "多轮用例要求每轮全部通过；单次运行不能判断稳定性。"
                )
                try:
                    review_rows = review_run_rows()
                except (FileNotFoundError, ValueError) as exc:
                    gr.Markdown(f"新增评测展示暂不可用：{html.escape(str(exc))}")
                    review_rows = []
                if review_rows:
                    gr.Dataframe(
                        review_rows,
                        headers=["阶段", "用例数", "遍数", "规则成功率", "严格成功率",
                                 "含执行错误的尝试", "多轮会话通过", "评分版本"],
                        interactive=False, label="新增验证与开发集对照",
                    )
                gr.Markdown(
                    "**2026-10-08 评测快照** · 35 条合成用例，每组各跑 3 次，三组共 315 次在线尝试。\n\n"
                    "这里回放已保存的答案，按共同 v5 规则展示，不会调用模型。"
                )
                try:
                    report = load_evaluation()
                except (FileNotFoundError, ValueError) as exc:
                    gr.Markdown(f"评测展示暂不可用：{html.escape(str(exc))}")
                    report = None
                if report:
                    eval_state = gr.State(report)
                    gr.Dataframe(
                        comparison_rows(report),
                        headers=["阶段", "尝试数", "规则成功率", "严格成功率", "工具调用",
                                 "多余调用", "平均 token", "耗时 p50 / ms"],
                        interactive=False, label="三组共同口径对照",
                    )
                    gr.Markdown(
                        "严格成功还要求调用次数符合用例预期。最终调用数与多余调用减少，"
                        "平均 token 增加，耗时中位数基本持平。\n\n"
                        "首轮优化省略了必要政策检查，出现质量回退；修正后补回。"
                        "100% 是这批用例的规则通过率。网络故障保留在记录中；成本仅计已定价部分。"
                    )
                    category_selector = gr.Dropdown(
                        choices=[("全部（35 条）", "all"), *[
                            (label, key) for key, label in CATEGORY_LABELS.items()
                        ]], value="all", label="筛选用例类型",
                    )
                    cases_table = gr.Dataframe(
                        case_rows(report),
                        headers=["用例 ID", "类型", "用户", "问题", "优化前", "首轮优化", "修正后"],
                        interactive=False, wrap=True, max_height=320,
                        label="完整评测集 · 最后三列为该用例通过次数 / 3",
                    )
                    with gr.Row():
                        case_selector = gr.Dropdown(
                            choices=[c["id"] for c in report["cases"]],
                            value="pp-no-difference", label="回放用例",
                        )
                        attempt_selector = gr.Dropdown(
                            choices=[1, 2, 3], value=1, label="第几次尝试",
                        )
                    answers, evidence = replay_case(report, "pp-no-difference", 1)
                    replay_html = gr.HTML(answers)
                    with gr.Accordion("查看用例判据、参数、命中文档与失败原因", open=False):
                        evidence_json = gr.JSON(evidence)
                    try_btn = gr.Button("体验此用例 → 客服对话", variant="primary")
                    gr.Markdown("体验按钮仅填入问题，身份保持为所选模拟用户；其他账户的订单会被拒绝。")

        outputs = [
            chatbot,
            message_box,
            trace_html,
            state,
            stats_html,
            user_card,
            header,
            reply_status,
        ]
        panel_outputs = [preview_html, preview_state, confirm_btn, secondary_box,
                         damage_box, evidence_file, requests_html, preview_btn]
        controls = [message_box, send_btn, history_selector, clear_btn, user_selector,
                    preview_btn, confirm_btn, query_btn, secondary_box, damage_box, evidence_file]
        general_controls = [message_box, send_btn, history_selector, clear_btn, user_selector,
                            query_btn]
        if report:
            controls.append(try_btn)
            general_controls.append(try_btn)
        for submit in (send_btn.click, message_box.submit):
            submit(
                lambda: [gr.update(interactive=False) for _ in controls], outputs=controls,
                queue=False, show_progress="hidden",
            ).then(
                on_send, inputs=[message_box, chatbot, state, user_selector], outputs=outputs,
                show_progress="hidden", stream_every=0.1, concurrency_limit=None,
            ).then(
                lambda: [gr.update(interactive=True) for _ in general_controls], outputs=general_controls,
                queue=False, show_progress="hidden",
            ).then(on_history_choices, inputs=[state, user_selector], outputs=history_selector, show_progress="hidden"
            ).then(return_panel, inputs=[state, user_selector], outputs=panel_outputs, show_progress="hidden")
        clear_btn.click(
            on_clear, inputs=[state, user_selector], outputs=outputs, show_progress="hidden",
        ).then(on_history_choices, inputs=[state, user_selector], outputs=history_selector, show_progress="hidden"
        ).then(return_panel, inputs=[state, user_selector], outputs=panel_outputs, show_progress="hidden")
        history_selector.input(on_restore, inputs=[history_selector, state, user_selector], outputs=outputs,
                               show_progress="hidden"
        ).then(return_panel, inputs=[state, user_selector], outputs=panel_outputs, show_progress="hidden")
        demo.load(on_load, inputs=user_selector, outputs=[*outputs, orders_table, order_selector, history_selector],
                  show_progress="hidden"
        ).then(return_panel, inputs=[state, user_selector], outputs=panel_outputs, show_progress="hidden")
        user_selector.input(
            on_load, inputs=user_selector,
            outputs=[*outputs, orders_table, order_selector, history_selector],
            show_progress="hidden",
        ).then(return_panel, inputs=[state, user_selector], outputs=panel_outputs, show_progress="hidden")
        for trigger, handler, inputs in (
            (secondary_box.input, on_return_declaration,
             [state, user_selector, secondary_box, damage_box]),
            (damage_box.input, on_return_declaration,
             [state, user_selector, secondary_box, damage_box]),
            (preview_btn.click, on_return_preview, [state, user_selector, secondary_box, damage_box]),
            (confirm_btn.click, on_return_confirm, [preview_state, state, user_selector]),
            (evidence_file.upload, on_return_upload, [evidence_file, state, user_selector]),
            (evidence_file.clear, on_return_clear_upload, [state, user_selector]),
            (query_btn.click, on_return_query, [state, user_selector]),
        ):
            trigger(
                lambda: [gr.update(interactive=False) for _ in controls], outputs=controls,
                queue=False, show_progress="hidden",
            ).then(handler, inputs=inputs, outputs=outputs, show_progress="hidden"
            ).then(
                lambda: [gr.update(interactive=True) for _ in general_controls], outputs=general_controls,
                queue=False, show_progress="hidden",
            ).then(return_panel, inputs=[state, user_selector], outputs=panel_outputs, show_progress="hidden"
            ).then(on_history_choices, inputs=[state, user_selector], outputs=history_selector,
                   show_progress="hidden")
        order_selector.change(
            on_order_change, inputs=[order_selector, user_selector],
            outputs=[order_detail, items_table, logistics_table],
            show_progress="hidden",
        )
        product_selector.change(
            price_rows, inputs=product_selector, outputs=prices_table, show_progress="hidden",
        )
        if report:
            category_selector.change(
                on_eval_category, inputs=[category_selector, eval_state],
                outputs=[cases_table, case_selector],
                show_progress="hidden",
            )
            for selector in (case_selector, attempt_selector):
                selector.change(
                    replay_case, inputs=[eval_state, case_selector, attempt_selector],
                    outputs=[replay_html, evidence_json],
                    show_progress="hidden",
                )
            try_btn.click(
                on_try_case, inputs=[case_selector, eval_state, state, user_selector],
                outputs=[*outputs, tabs],
                show_progress="hidden",
            ).then(return_panel, inputs=[state, user_selector], outputs=panel_outputs, show_progress="hidden")

    return demo


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="电商客服工作台")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--scenario", type=Path, help="独立场景目录，保留原数据库与历史")
    args = parser.parse_args()
    if args.scenario:
        SETTINGS = scenario_settings(SETTINGS, args.scenario)
        SHOP_NAME = "客服实验商店"
        SHOP_SUBTITLE = "合成场景 v2 · 商品咨询与售后 · 非真实交易"
        DEMO_CASES = [
            ("退货政策", "无线耳机退货期限多久？"),
            ("查物流", "订单 S20003 现在到哪了？"),
            ("退货判断", "订单 S20001 现在还能退吗？"),
            ("挂耳推荐", "我不喜欢入耳，预算300元以内，有什么可购买的挂耳耳机？"),
            ("颜色库存", "演示O1挂耳耳机的橙色可以购买吗？"),
            ("越权测试", "帮我查询我朋友的订单 S20021。"),
        ]
    # Gradio 6 moved `css` from Blocks() to launch() and removed `show_api`;
    # verified against inspect.signature rather than guessed from the docs.
    build_app().launch(
        server_name=args.host,
        server_port=args.port,
        show_error=True,
        css=CSS,
        theme=gr.themes.Soft(primary_hue="orange", neutral_hue="slate"),
        footer_links=[],
    )
