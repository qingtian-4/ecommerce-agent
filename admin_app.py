"""Authenticated operator workbench, separate from the customer simulation."""

from __future__ import annotations

import base64
import html
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gradio as gr

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from acs.config import Settings, get_settings  # noqa: E402
from acs.returns import ReturnService  # noqa: E402
from acs.sessions import TaskValidationError  # noqa: E402

STATUS = {"pending_review": "待审核", "approved": "允许退回", "rejected": "已拒绝", "all": "全部"}
CSS = """
.gradio-container {max-width:1200px!important; font-family:Arial,'Microsoft YaHei',sans-serif;}
#review-header {padding:18px 0; border-bottom:1px solid #e2e8f0;}
#review-header h1 {font-size:24px; margin:0; color:#172033;}
#review-header p {color:#536174; line-height:1.75; margin:8px 0 0;}
.review-detail {line-height:1.8; color:#172033; overflow-wrap:anywhere;}
.review-detail h2 {font-size:20px; margin:12px 0;}
.review-detail h3 {font-size:15px; margin:20px 0 8px;}
.review-detail dl {display:grid;grid-template-columns:100px 1fr;gap:6px 12px;}
.review-detail dt {color:#536174;} .review-detail dd {margin:0;}
.review-detail small {color:#536174;}
.review-detail img,.review-detail video {max-width:100%;max-height:360px;object-fit:contain;}
#review-message {min-height:44px;line-height:1.8;}
button:active {transform:scale(.98);}
button:focus-visible {outline:2px solid #c35008;outline-offset:3px;}
@media(max-width:600px) {.review-detail dl {grid-template-columns:80px 1fr;}}
"""


def render_detail(detail: dict) -> str:
    esc = lambda v: html.escape(str(v))  # noqa: E731
    def timestamp(value):
        return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M",
        )

    def claim(value):
        return "未声明" if value is None else ("是" if value else "否")

    content, order = detail["content"], detail["current_order"]
    order_status = {"delivered": "已签收", "shipped": "已发货", "paid": "待发货",
                    "cancelled": "已取消"}.get(order["status"], order["status"])
    lines = [("申请号", detail["request_id"]), ("客户", detail["user_id"]),
             ("订单 / 明细", f"{detail['order_id']} / {detail['item_id']}"),
             ("商品 / 数量", f"{content['product_name']} × {detail['quantity']}"),
             ("原因", "质量问题" if content["reason"] == "quality" else "无理由"),
             ("提交时间", timestamp(detail["created_at"])),
             ("当前订单", f"{order_status} · 购买 {order['quantity']} 件"),
             ("签收日期", order["delivered_at"]),
             ("故障自述", content.get("problem_description") or "未提供"),
             ("商品状况自述", content.get("condition") or "未提供"),
             ("二次销售声明", claim(content.get("secondary_sale_ok"))),
             ("非人为声明", claim(content.get("quality_non_user_damage"))),
             ("提交时政策", f"{content['policy']['source']} / {content['policy']['version']}")]
    body = (f"<section class='review-detail' lang='zh'><h2>{STATUS[detail['status']]}</h2>"
            "<p>通过表示允许退回商品，不执行退款。提交材料中的声明保留为用户自述。</p><dl>"
            + "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in lines) + "</dl>")
    if detail["review"]:
        review = detail["review"]
        body += (f"<h3>审核结论</h3><p>{esc(review['reason'])}</p>"
                 f"<small>{esc(review['reviewer'])} · "
                 f"{timestamp(review['reviewed_at'])}（北京时间）</small>")
    body += "<h3>操作记录</h3>" + "".join(
        f"<p>{timestamp(a['created_at'])} · {esc(a['actor'])} · "
        f"{esc('创建申请' if a['action']=='created' else '审核申请')}</p>" for a in detail["audit"]
    )
    return body + "</section>"


def build_app(settings: Settings | None = None) -> gr.Blocks:
    settings = settings or get_settings()
    if not settings.admin_username or not settings.admin_password.get_secret_value():
        raise RuntimeError("请在.env配置ACS_ADMIN_USERNAME和ACS_ADMIN_PASSWORD后启动审核工作台")
    service = ReturnService(settings)

    def principal(request: gr.Request) -> str | None:
        return request.username if request else None

    def refresh(status: str, request: gr.Request):
        rows = service.list_for_review(reviewer=principal(request), status=status)
        choices = [(f"{r['order_id']} · {r['request_id']}", r["request_id"])
                   for r in rows]
        return gr.update(choices=choices, value=None), f"当前列表 {len(rows)} 张申请，请选择核对。"

    def select(rid: str | None, request: gr.Request):
        service._require_reviewer(principal(request))
        if not rid:
            return ("<p>选择申请后查看材料。</p>", "", "",
                    gr.update(interactive=False), gr.update(interactive=False))
        detail = service.get_for_review(rid, reviewer=principal(request))
        pending = detail["status"] == "pending_review"
        return (render_detail(detail), "点击下方按钮查看已保存的原始凭证。", "",
                gr.update(interactive=pending), gr.update(interactive=pending))

    def evidence(rid: str, request: gr.Request):
        try:
            media = service.review_evidence(rid, reviewer=principal(request))
            if media is None:
                return "<p>本申请未附照片或视频。</p>"
            data = base64.b64encode(media["data"]).decode("ascii")
            url = f"data:{media['media_type']};base64,{data}"
            # Inline data avoids exposing evidence through a shared public file cache.
            tag = (f"<video controls src='{url}'></video>" if media["media_type"] == "video/mp4"
                   else f"<img src='{url}' alt='客户上传的原始退货凭证'>")
            return "<div class='review-detail'>" + tag + "</div>"
        except TaskValidationError as exc:
            return f"<p>{html.escape(str(exc))}</p>"

    def decide(rid: str, reason: str, decision: str, request: gr.Request):
        try:
            service.review(rid, decision, reason, reviewer=principal(request))
            message = "审核已保存。客户可查询结果；本次未退款。刷新列表可查看下一张待审申请。"
        except TaskValidationError as exc:
            message = str(exc)
        detail = service.get_for_review(rid, reviewer=principal(request))
        pending = detail["status"] == "pending_review"
        return (render_detail(detail), message,
                gr.update(interactive=pending), gr.update(interactive=pending))

    with gr.Blocks(title="售后审核工作台") as demo:
        gr.HTML("<header id='review-header' lang='zh'><h1>售后审核工作台</h1>"
                "<p>核对材料，记录原因，客户查询真实结果。此入口仅供管理员使用。</p></header>")
        with gr.Row():
            with gr.Column(scale=2, min_width=260):
                status = gr.Dropdown([(v, k) for k, v in STATUS.items()],
                                     value="pending_review", label="申请状态")
                refresh_btn = gr.Button("刷新申请列表")
                selector = gr.Dropdown([], label="选择申请", elem_id="review-selector")
                message = gr.Markdown("", elem_id="review-message")
                gr.HTML("<a href='/logout'>退出管理账号</a>")
            with gr.Column(scale=3, min_width=300):
                detail = gr.HTML("<p>选择申请后查看材料。</p>", elem_id="review-detail")
                media = gr.HTML("", elem_id="review-media")
                media_btn = gr.Button("查看原始凭证")
                reason = gr.Textbox(label="审核原因（必填，客户可见）", lines=3, max_length=1000)
                with gr.Row():
                    approve_btn = gr.Button("通过：允许退回", variant="primary", interactive=False)
                    reject_btn = gr.Button("拒绝申请", interactive=False)
        demo.load(refresh, inputs=status, outputs=[selector, message], api_name="refresh")
        for event in (refresh_btn.click, status.change):
            event(refresh, inputs=status, outputs=[selector, message], api_name=False)
        selector.change(select, inputs=selector,
                        outputs=[detail, media, reason, approve_btn, reject_btn], api_name="select")
        media_btn.click(evidence, inputs=selector, outputs=media, api_name="evidence")
        for button, decision in ((approve_btn, "approved"), (reject_btn, "rejected")):
            button.click(decide, inputs=[selector, reason, gr.State(decision)],
                         outputs=[detail, message, approve_btn, reject_btn], api_name=decision,
                         concurrency_id="review_write", concurrency_limit=2)
    return demo


if __name__ == "__main__":
    settings = get_settings()
    demo = build_app(settings)
    service = ReturnService(settings)
    demo.launch(server_name="127.0.0.1", server_port=7861, auth=service.authenticate_reviewer,
                auth_message="使用.env中单独配置的管理账号登录", css=CSS,
                theme=gr.themes.Soft(primary_hue="orange", neutral_hue="slate"),
                footer_links=[], show_error=False)
