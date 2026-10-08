"""Gradio web UI: an e-commerce CS console with a live execution-trace panel.

One command:
    uv run python app.py

Layout mirrors a conventional e-commerce customer-service console so the agent
can be judged in a familiar frame:

    header   shop name, model badge, KB/tool counts
    sidebar  logged-in user, the orders that user can ask about
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

import html
import json
import sys
import traceback
from pathlib import Path
from time import perf_counter
from typing import Any

import gradio as gr

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from acs.config import get_settings  # noqa: E402
from acs.cs_tools import ToolRegistry  # noqa: E402
from acs.database import BusinessDB  # noqa: E402
from acs.eval_view import (  # noqa: E402
    CATEGORY_LABELS,
    case_rows,
    comparison_rows,
    load_evaluation,
    replay_case,
    review_run_rows,
)
from acs.rag import INDEX_FILE, KnowledgeBase  # noqa: E402
from acs.service_agent import AgentResult, CustomerServiceAgent  # noqa: E402

SETTINGS = get_settings()

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


# --------------------------------------------------------------------------- #
# pure helpers (unit-tested without a browser)
# --------------------------------------------------------------------------- #
def user_id_from_key(user_key: str | None) -> str:
    """'USER001 · 张伟 · ...' -> 'USER001'."""
    key = user_key or USER_CHOICES[0]
    return key.split(" · ")[0].strip()


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


_KB_STATS: dict[str, int] | None = None


def _kb_stats() -> dict[str, int]:
    """Chunk and tool counts for the header, computed once."""
    global _KB_STATS
    if _KB_STATS is None:
        try:
            chunks = len(KnowledgeBase(SETTINGS).load_documents())
        except Exception:  # noqa: BLE001 - header must never break the page
            chunks = 0
        try:
            tools = len(ToolRegistry(SETTINGS).names)
        except Exception:  # noqa: BLE001
            tools = 0
        _KB_STATS = {"chunks": chunks, "tools": tools}
    return _KB_STATS


def render_header(user_key: str) -> str:
    stats = _kb_stats()
    return (
        "<div class='cs-header'>"
        "<div>"
        f"<div class='shop'>{html.escape(SHOP_NAME)}</div>"
        f"<div class='sub'>{html.escape(SHOP_SUBTITLE)}</div>"
        "</div>"
        "<div class='meta'>"
        f"在线客服 · 当前用户 <b>{html.escape(user_id_from_key(user_key))}</b>"
        f"<span class='cs-badge'>{html.escape(SETTINGS.llm_model)}</span>"
        "<br>"
        f"知识库 {stats['chunks']} chunks · 可用工具 {stats['tools']} 个 · "
        f"top_k={SETTINGS.top_k} · max_steps={SETTINGS.max_steps}"
        "</div>"
        "</div>"
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
        for order in user_orders[:5]
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
        f"当前用户共 {len(user_orders)} 个订单，此处展示最近 5 笔。</div>"
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
    items = [[i["product_id"], i["product_name"], i["quantity"], i["purchase_price"],
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


def price_rows(product_id: str) -> list[list]:
    history = BusinessDB(SETTINGS).get_price_history(product_id)
    return [[r["changed_at"], r["price"]] for r in history.get("records", [])]


def on_eval_category(category: str, report: dict) -> tuple:
    rows = case_rows(report, category)
    return rows, gr.update(choices=[r[0] for r in rows], value=rows[0][0])


def on_try_case(case_id: str, report: dict, state_dict: dict) -> tuple:
    case = next(c for c in report["cases"] if c["id"] == case_id)
    user_key = USER_CHOICES[USER_IDS.index(case["user"])]
    packed = list(on_user_change(user_key, state_dict))
    packed[1] = case["query"]
    return (*packed, user_key, gr.update(selected="chat"))


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
            "· 知识检索命中了哪些文档、余弦分数多少<br>"
            "· 每步实际进展和最终结果<br><br>"
            "这里展示的是<b>可观测的执行过程</b>，不包含模型的隐藏思维过程。"
            "</div></div>"
        )

    running = result.stop_reason == "running"
    badge = {"running": "warn", "answered": "ok", "max_steps": "warn", "llm_error": "bad"}.get(
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
        else:
            title = f"Step {step_no} · 最终回答"

        detail = event.detail
        if event.type == "tool_call":
            body = _truncate(detail.get("arguments", ""))
        elif event.type == "retrieval":
            docs = detail.get("documents", [])
            body = (
                "\n".join(
                    f"[{d.get('score', 0):.4f}] {d.get('title', '')} "
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
        if css_class not in {"call", "result", "retrieval", "error", "final"}:
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
                f"[{d.get('score', 0):.4f}] {html.escape(str(d.get('title', '')))} "
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
            ("总 token", f"{totals.get('total_tokens', 0):,}"),
            (cost_label, f"¥{totals.get('cost_cny', 'n/a')}"),
            ("工具调用", str(result.tool_calls)),
            ("检索次数", str(len(result.retrieval_queries))),
            ("命中文档", str(len(result.retrieved_documents))),
        ]
    cards = "".join(
        f"<div class='stat'><small>{label}</small><strong>{value}</strong></div>"
        for label, value in cells
    )
    return f"<div class='stat-grid'>{cards}</div>"


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


def _card_for(user_key: str) -> str:
    user_id = user_id_from_key(user_key)
    return render_user_card(user_id, label_for_id(user_id))


def on_send(message: str, history: list[dict] | None, state_dict: dict, user_key: str):
    history = list(history or [])
    card = _card_for(user_key)
    if not (message or "").strip():
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
    history.append({"role": "user", "content": message})
    reply_index = len(history)
    yield pack(
        history, render_trace(AgentResult(answer="", stop_reason="running")), state_dict,
        render_stats(None), card, render_header(user_key), render_reply_status(0, running=True),
    )
    try:
        agent = ensure_agent(state_dict, user_key)
        for result in agent.chat_stream(message):
            running = result.stop_reason == "running"
            history[reply_index:] = (
                [{"role": "assistant", "content": result.answer}] if result.answer else []
            )
            yield pack(
                history, render_trace(result), state_dict, render_stats(result), card,
                render_header(user_key), render_reply_status(
                    (perf_counter() - started) * 1000, running=running,
                    failed=result.stop_reason in {"llm_error", "max_steps"},
                ),
            )
    except Exception as exc:  # noqa: BLE001 - the UI must never go blank
        # Surfaced in the panel, not only in the server log. A silent gradio 500
        # is how the keyword-only-argument bug stayed invisible in the browser.
        detail = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc(limit=6)}"
        partial = history[reply_index]["content"] + "\n\n" if len(history) > reply_index else ""
        history[reply_index:] = [{
            "role": "assistant",
            "content": partial + "抱歉，系统出现异常，本次回答未完成，请稍后重试或联系人工客服。",
        }]
        yield pack(
            history,
            render_trace(None, error=detail),
            state_dict,
            render_stats(None),
            card,
            render_header(user_key),
            render_reply_status((perf_counter() - started) * 1000, failed=True),
        )


def on_clear(state_dict: dict, user_key: str):
    agent = state_dict.get("agent")
    if agent is not None:
        agent.reset()
    return pack(
        [],
        render_trace(None),
        state_dict,
        render_stats(None),
        _card_for(user_key),
        render_header(user_key),
    )


def on_user_change(user_key: str, state_dict: dict):
    # A different user is a different session: history is dropped so one user's
    # order details can never leak into another user's context.
    user_id = user_id_from_key(user_key)
    state_dict["agent"] = CustomerServiceAgent(settings=SETTINGS, current_user_id=user_id)
    state_dict["user_id"] = user_id
    state_dict["user_key"] = user_key
    return pack(
        [],
        render_trace(None),
        state_dict,
        render_stats(None),
        _card_for(user_key),
        render_header(user_key),
    )


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #
CSS = """
.gradio-container { max-width: 1560px !important; }
#customer-chat { overflow: hidden !important; }
#customer-chat .bubble-wrap { overscroll-behavior: contain; }

.cs-header {
  background: linear-gradient(93deg, #ff6a00 0%, #ee0979 100%);
  color: #fff; border-radius: 10px; padding: 12px 18px; margin-bottom: 10px;
  display: flex; align-items: center; justify-content: space-between;
}
.cs-header .shop { font-size: 19px; font-weight: 700; letter-spacing: .3px; }
.cs-header .sub  { font-size: 12px; opacity: .9; margin-top: 2px; }
.cs-header .meta { font-size: 11.5px; opacity: .95; text-align: right; line-height: 1.7; }
.cs-badge {
  display:inline-block; background: rgba(255,255,255,.22);
  border:1px solid rgba(255,255,255,.35); border-radius: 99px;
  padding: 1px 9px; margin-left: 6px; font-size: 11px;
}

.cs-card {
  background: #fff; border: 1px solid #e8eaed; border-radius: 10px;
  padding: 12px 13px; margin-bottom: 10px;
}
.cs-card h4 { margin: 0 0 8px 0; font-size: 13px; color: #1f2937; font-weight: 700; }
.cs-user { display:flex; align-items:center; gap:10px; }
.cs-avatar {
  width: 38px; height: 38px; border-radius: 50%; background: #ffe4d1; color: #d94f00;
  display:flex; align-items:center; justify-content:center; font-weight:700;
  font-size:15px; flex: 0 0 38px;
}
.cs-user .nm { font-size: 13.5px; font-weight: 600; color: #111827; }
.cs-user .ph { font-size: 11.5px; color: #6b7280; }
.cs-hint { font-size: 11.5px; color: #6b7280; line-height: 1.6; }
.cs-orders { display:flex; flex-direction:column; gap:6px; }
.cs-order {
  border:1px solid #eef0f3; border-radius:8px; padding:6px 9px; background:#fafbfc;
  font-size:12px; color:#374151; display:flex; justify-content:space-between; gap:8px;
}
.cs-order b { color:#111827; }
.cs-order span { color:#6b7280; font-size:11px; text-align:right; }

.cs-panel-title {
  display:flex; align-items:center; justify-content:space-between;
  border-bottom:1px solid #eef0f3; padding:0 2px 8px 2px; margin-bottom:8px;
}
.cs-panel-title b { font-size:14px; color:#111827; }
.cs-panel-title span { font-size:11.5px; color:#6b7280; }
.cs-online::before { content:"●"; color:#22c55e; margin-right:5px; font-size:10px; }

.trace-panel { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px;
               padding-right: 6px; }
.trace-step { border-left: 3px solid #94a3b8; padding: 7px 10px; margin: 7px 0;
              background: #f8fafc; border-radius: 0 7px 7px 0; }
.trace-step.call      { border-left-color: #2563eb; background: #eff6ff; }
.trace-step.result    { border-left-color: #16a34a; background: #f0fdf4; }
.trace-step.retrieval { border-left-color: #7c3aed; background: #f5f3ff; }
.trace-step.error     { border-left-color: #dc2626; background: #fef2f2; }
.trace-step.final     { border-left-color: #0f172a; background: #f1f5f9; }
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
    # Build the index on first run so `python app.py` is genuinely the only step.
    if not (SETTINGS.index_dir / INDEX_FILE).exists():
        print("knowledge index missing - building it now (one-off) ...")
        KnowledgeBase(SETTINGS).build()

    with gr.Blocks(title="Adaptive Customer Service Agent") as demo:
        header = gr.HTML(render_header(USER_CHOICES[0]))
        state = gr.State({"user_key": USER_CHOICES[0], "agent": None})

        user_selector = gr.Dropdown(
            choices=USER_CHOICES, value=USER_CHOICES[0], label="模拟用户（非真实登录）"
        )
        with gr.Tabs(selected="chat", elem_id="project-tabs") as tabs:
            with gr.Tab("客服对话", id="chat"), gr.Row():
                # ---------------- sidebar ---------------- #
                with gr.Column(scale=2, min_width=240):
                    user_card = gr.HTML(render_user_card(USER_IDS[0], USERS[0][1]))
                    clear_btn = gr.Button("结束会话 / 清空对话")

                # ---------------- chat ---------------- #
                with gr.Column(scale=5, min_width=380):
                    gr.HTML(
                        "<div class='cs-panel-title'><b>在线客服</b>"
                        "<span class='cs-online'>客服在线 · 可查看查询依据</span></div>"
                    )
                    reply_status = gr.HTML("", elem_id="reply-status", js_on_load=REPLY_CLOCK_JS)
                    chatbot = gr.Chatbot(
                        height=440,
                        elem_id="customer-chat",
                        show_label=False,
                        placeholder=(
                            "<div style='text-align:center;color:#9ca3af;padding:26px 10px'>"
                            "您好，这里是声阔优选旗舰店在线客服。<br>"
                            "可以咨询商品、订单、物流、退换货与价保问题。<br><br>"
                            "点击下方示例问题快速体验 →"
                            "</div>"
                        ),
                    )
                    with gr.Row():
                        message_box = gr.Textbox(
                            show_label=False,
                            placeholder="请输入您的问题，例如：订单 O10023 现在到哪了？",
                            lines=1,
                            scale=5,
                            container=False,
                        )
                        send_btn = gr.Button("发送", variant="primary", scale=1)

                    gr.Markdown(
                        "<small>点击示例填入问题；订单示例对应 USER001，"
                        "其他用户可在业务数据页查看自己的订单号。</small>"
                    )
                    with gr.Row():
                        for label, text in DEMO_CASES[:3]:
                            btn = gr.Button(label, size="sm")
                            btn.click(lambda t=text: t, outputs=message_box, show_progress="hidden")
                    with gr.Row():
                        for label, text in DEMO_CASES[3:]:
                            btn = gr.Button(label, size="sm")
                            btn.click(lambda t=text: t, outputs=message_box, show_progress="hidden")

                # ---------------- trace ---------------- #
                with gr.Column(scale=5, min_width=380):
                    gr.HTML(
                        "<div class='cs-panel-title'><b>运行指标</b>"
                        "<span>token / 成本</span></div>"
                    )
                    stats_html = gr.HTML(render_stats(None))
                    gr.HTML(
                        "<div class='cs-panel-title'><b>Agent Execution Trace</b>"
                        "<span>可观测执行过程</span></div>"
                    )
                    trace_html = gr.HTML(
                        render_trace(None), elem_id="execution-trace", max_height=500,
                        js_on_load=TRACE_FOLLOW_JS,
                    )

            with gr.Tab("业务数据", id="data"):
                gr.Markdown("浏览当前 SQLite 合成数据。切换上方用户后，只展示该用户的订单。")
                gr.HTML(render_database_overview())
                initial_orders, initial_choice = on_data_user_change(USER_CHOICES[0])
                orders_table = gr.Dataframe(
                    initial_orders, headers=["订单号", "状态", "实付 / 元", "下单", "发货", "签收"],
                    interactive=False, max_height=300,
                )
                order_selector = gr.Dropdown(
                    choices=initial_choice["choices"], value=initial_choice["value"],
                    label="查看订单详情",
                )
                detail, items, logistics = on_order_change(initial_choice["value"], USER_CHOICES[0])
                order_detail = gr.HTML(detail)
                items_table = gr.Dataframe(
                    items, headers=["商品 ID", "商品", "数量", "购买价", "当前价", "单件价差"],
                    label="订单明细 · 价差仅为金额事实，能否价保还需核对政策",
                    interactive=False, wrap=True,
                )
                logistics_table = gr.Dataframe(
                    logistics, headers=["日期", "物流节点"], label="物流时间线",
                    interactive=False, wrap=True,
                )
                products = product_rows()
                gr.Dataframe(
                    products, headers=["商品 ID", "商品", "分类", "当前价 / 元", "最近调价"],
                    label="商品目录", interactive=False, wrap=True,
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
                    gr.Markdown("体验按钮会切换到用例对应的模拟用户并填入问题；点击发送后运行当前 Agent。")

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
        controls = [message_box, send_btn, user_selector, clear_btn]
        if report:
            controls.append(try_btn)
        for submit in (send_btn.click, message_box.submit):
            submit(
                lambda: [gr.update(interactive=False) for _ in controls], outputs=controls,
                queue=False, show_progress="hidden",
            ).then(
                on_send, inputs=[message_box, chatbot, state, user_selector], outputs=outputs,
                show_progress="hidden", stream_every=0.1,
            ).then(
                lambda: [gr.update(interactive=True) for _ in controls], outputs=controls,
                queue=False, show_progress="hidden",
            )
        clear_btn.click(
            on_clear, inputs=[state, user_selector], outputs=outputs, show_progress="hidden",
        )
        user_selector.input(
            on_user_change, inputs=[user_selector, state], outputs=outputs, show_progress="hidden",
        )
        user_selector.change(
            on_data_user_change, inputs=user_selector, outputs=[orders_table, order_selector],
            show_progress="hidden",
        )
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
                on_try_case, inputs=[case_selector, eval_state, state],
                outputs=[*outputs, user_selector, tabs],
                show_progress="hidden",
            )

    return demo


if __name__ == "__main__":
    # Gradio 6 moved `css` from Blocks() to launch() and removed `show_api`;
    # verified against inspect.signature rather than guessed from the docs.
    build_app().launch(
        server_name="0.0.0.0",
        server_port=7860,
        show_error=True,
        css=CSS,
        theme=gr.themes.Soft(primary_hue="orange", neutral_hue="slate"),
    )
