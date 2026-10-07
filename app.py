"""Gradio web UI: chat on the left, observable execution trace on the right.

One command:
    uv run python app.py

No React, no Vue, no FastAPI, no separate frontend build. Gradio is enough for
the MVP and keeps the whole thing in one process.

The right-hand panel shows only *observable* execution: tool calls, their
arguments, their results, retrieval queries and scores, latency and token usage.
It never shows the model's hidden reasoning. The one exception is a `decision
summary`, which is derived mechanically from which tools ran -- it states what
the agent did, which the trace already shows, not why the model chose it.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path
from typing import Any

import gradio as gr

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from acs.config import get_settings  # noqa: E402
from acs.rag import INDEX_FILE, KnowledgeBase  # noqa: E402
from acs.service_agent import AgentResult, CustomerServiceAgent  # noqa: E402

SETTINGS = get_settings()

DEMO_CASES = [
    "无线耳机退货期限多久？",
    "订单 O10023 现在到哪了？",
    "订单 O10024 是我昨天收到的耳机，现在还能退吗？",
    "帮我查一下我朋友的订单 O10030。",
    "充电宝能带上飞机吗？",
    "我买的耳机降价了，能退差价吗？",
]

USERS = ["USER001", "USER002", "USER003"]

# --------------------------------------------------------------------------- #
# styling
# --------------------------------------------------------------------------- #
CSS = """
.trace-panel { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
.trace-step { border-left: 3px solid #94a3b8; padding: 6px 10px; margin: 8px 0;
              background: #f8fafc; border-radius: 0 6px 6px 0; }
.trace-step.call { border-left-color: #2563eb; background: #eff6ff; }
.trace-step.result { border-left-color: #16a34a; background: #f0fdf4; }
.trace-step.retrieval { border-left-color: #7c3aed; background: #f5f3ff; }
.trace-step.error { border-left-color: #dc2626; background: #fef2f2; }
.trace-step.final { border-left-color: #0f172a; background: #f1f5f9; }
.trace-head { font-weight: 600; margin-bottom: 3px; }
.trace-body { white-space: pre-wrap; word-break: break-word; color: #334155; }
.mono-key { color: #64748b; }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
             gap: 8px; margin-top: 6px; }
.stat { background: #f1f5f9; border-radius: 6px; padding: 6px 9px; }
.stat small { display: block; color: #64748b; font-size: 11px; }
.stat strong { font-size: 14px; }
.pill { display:inline-block; padding:1px 7px; border-radius:99px; font-size:11px;
        background:#e2e8f0; color:#334155; margin-right:4px; }
"""


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _truncate(text: Any, limit: int = 900) -> str:
    rendered = (
        text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=1)
    )
    if len(rendered) <= limit:
        return rendered
    return rendered[:limit] + f"\n… (+{len(rendered) - limit} chars)"


def render_trace(result: AgentResult | None) -> str:
    if result is None:
        return (
            "<div class='trace-panel'><p style='color:#64748b'>"
            "尚未发送消息。左侧提问后，这里会显示 Agent 的完整执行轨迹："
            "工具调用、参数、返回结果、知识检索命中文档与分数。</p></div>"
        )

    parts: list[str] = ["<div class='trace-panel'>"]
    parts.append(
        f"<div style='margin-bottom:6px'>"
        f"<span class='pill'>stop: {html.escape(result.stop_reason)}</span>"
        f"<span class='pill'>steps: {result.steps}</span>"
        f"<span class='pill'>tool calls: {result.tool_calls}</span>"
        f"</div>"
    )
    parts.append(
        f"<div class='trace-step final'><div class='trace-head'>决策摘要</div>"
        f"<div class='trace-body'>{html.escape(result.decision_summary())}</div></div>"
    )

    for step_no, event in enumerate(result.trace, start=1):
        css_class = event.type.replace("_", "-")
        if event.type == "tool_call":
            title = f"Step {step_no} · 工具调用 <code>{html.escape(event.label)}</code>"
        elif event.type == "tool_result":
            title = f"Step {step_no} · 工具返回 <code>{html.escape(event.label)}</code>"
        elif event.type == "retrieval":
            docs = event.detail.get("documents", [])
            title = (
                f"Step {step_no} · 知识检索 "
                f"<code>{html.escape(str(event.detail.get('query', '')))}</code> "
                f"· {len(docs)} 条命中"
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
            lines = [
                f"[{d.get('score', 0):.4f}] {d.get('title', '')} "
                f"({d.get('chunk_id', '')})\n    {_truncate(d.get('content', ''), 220)}"
                for d in docs
            ]
            body = "\n".join(lines) or "（无命中）"
        elif event.type == "final":
            body = _truncate(detail.get("text", ""))
        elif event.type == "error":
            body = _truncate(detail)
        else:
            body = _truncate(detail.get("result", detail))

        if css_class not in {"call", "result", "retrieval", "error", "final"}:
            css_class = ""
        parts.append(
            f"<div class='trace-step {css_class}'>"
            f"<div class='trace-head'>{title}</div>"
            f"<div class='trace-body'>{html.escape(body)}</div></div>"
        )

    if result.retrieved_documents:
        parts.append(
            "<div style='margin-top:8px'><b>累计命中文档</b>"
            + "".join(
                f"<div class='trace-body'>· [{d.get('score', 0):.4f}] "
                f"{html.escape(str(d.get('title', '')))} "
                f"<span class='mono-key'>({html.escape(str(d.get('chunk_id', '')))})</span></div>"
                for d in result.retrieved_documents
            )
            + "</div>"
        )

    parts.append("</div>")
    return "".join(parts)


def render_stats(result: AgentResult | None) -> str:
    if result is None:
        return "<div class='stat-grid'><div class='stat'><small>状态</small><strong>待输入</strong></div></div>"

    totals = result.totals or {}
    cost = totals.get("cost_cny", "n/a")
    items = [
        ("延迟", f"{totals.get('latency_ms', 0) / 1000:.2f} s"),
        ("输入 token", f"{totals.get('prompt_tokens', 0):,}"),
        ("输出 token", f"{totals.get('completion_tokens', 0):,}"),
        ("总 token", f"{totals.get('total_tokens', 0):,}"),
        ("估算成本", f"¥{cost}"),
        ("工具调用", str(result.tool_calls)),
        ("检索次数", str(len(result.retrieval_queries))),
        ("命中文档", str(len(result.retrieved_documents))),
    ]
    cards = "".join(
        f"<div class='stat'><small>{label}</small><strong>{value}</strong></div>"
        for label, value in items
    )
    return f"<div class='stat-grid'>{cards}</div>"


# --------------------------------------------------------------------------- #
# app
# --------------------------------------------------------------------------- #
def build_app() -> gr.Blocks:
    # Build the index on first run so `python app.py` is genuinely the only step.
    index_path = SETTINGS.index_dir / INDEX_FILE
    if not index_path.exists():
        print("knowledge index missing - building it now (one-off) ...")
        KnowledgeBase(SETTINGS).build()

    with gr.Blocks(title="Adaptive Customer Service Agent") as demo:
        gr.Markdown(
            "# Adaptive Customer Service Agent\n"
            "电商客服 Agent · 自主决定是否检索知识库、是否调用业务工具，并展示完整执行轨迹。"
        )

        state = gr.State({"user_id": "USER001", "agent": None})

        with gr.Row():
            with gr.Column(scale=5):
                user_selector = gr.Dropdown(
                    choices=USERS, value="USER001", label="当前登录用户（用于权限校验）"
                )
                chatbot = gr.Chatbot(label="Customer Chat", height=430)
                message_box = gr.Textbox(
                    label="输入消息", placeholder="例如：订单 O10024 是我昨天收到的耳机，现在还能退吗？",
                    lines=2,
                )
                with gr.Row():
                    send_btn = gr.Button("发送", variant="primary")
                    clear_btn = gr.Button("清空对话")
                gr.Examples(examples=[[c] for c in DEMO_CASES], inputs=message_box, label="Demo Cases")

            with gr.Column(scale=6):
                gr.Markdown("### Agent Execution Trace")
                trace_html = gr.HTML(render_trace(None))
                gr.Markdown("### 运行指标")
                stats_html = gr.HTML(render_stats(None))

        # ---------------------------------------------------------------- #
        def _ensure_agent(state_dict: dict, user_id: str) -> CustomerServiceAgent:
            agent = state_dict.get("agent")
            if agent is None or state_dict.get("user_id") != user_id:
                agent = CustomerServiceAgent(SETTINGS, current_user_id=user_id)
                state_dict["agent"] = agent
                state_dict["user_id"] = user_id
            else:
                agent.set_user(user_id)
            return agent

        def on_send(
            message: str, history: list[dict], state_dict: dict, user_id: str
        ) -> tuple[list[dict], str, str, dict, str]:
            history = list(history or [])
            if not (message or "").strip():
                return history, "", render_trace(None), state_dict, ""

            history.append({"role": "user", "content": message})
            agent = _ensure_agent(state_dict, user_id)

            try:
                result = agent.chat(message)
                answer = result.answer
            except Exception as exc:  # noqa: BLE001 - the UI must never go blank
                answer = f"系统错误：{type(exc).__name__}: {exc}"
                result = None

            history.append({"role": "assistant", "content": answer})
            trace = render_trace(result)
            stats = render_stats(result)
            return history, "", trace, state_dict, stats

        def on_clear(state_dict: dict):
            agent = state_dict.get("agent")
            if agent is not None:
                agent.reset()
            return [], render_trace(None), render_stats(None), state_dict

        def on_user_change(user_id: str, state_dict: dict):
            # A different user means a different session: history is dropped so
            # one user's order details can never leak into another's context.
            state_dict["agent"] = CustomerServiceAgent(SETTINGS, current_user_id=user_id)
            state_dict["user_id"] = user_id
            return [], render_trace(None), render_stats(None), state_dict

        send_btn.click(
            on_send,
            inputs=[message_box, chatbot, state, user_selector],
            outputs=[chatbot, message_box, trace_html, state, stats_html],
        )
        message_box.submit(
            on_send,
            inputs=[message_box, chatbot, state, user_selector],
            outputs=[chatbot, message_box, trace_html, state, stats_html],
        )
        clear_btn.click(
            on_clear, inputs=[state], outputs=[chatbot, trace_html, stats_html, state]
        )
        user_selector.change(
            on_user_change,
            inputs=[user_selector, state],
            outputs=[chatbot, trace_html, stats_html, state],
        )

    return demo


if __name__ == "__main__":
    # Gradio 6 moved `css` from the Blocks constructor to launch(), and dropped
    # `show_api`. Both were verified against the installed signature rather than
    # guessed.
    build_app().launch(
        server_name="127.0.0.1", server_port=7860, show_error=True, css=CSS
    )
