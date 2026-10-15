"""Authenticated operator workbench, separate from the customer simulation."""

from __future__ import annotations

import argparse
import base64
import html
import io
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gradio as gr
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from acs.config import Settings, get_settings  # noqa: E402
from acs.returns import ReturnService  # noqa: E402
from acs.scenarios import scenario_settings  # noqa: E402
from acs.sessions import TaskValidationError  # noqa: E402

STATUS = {"pending_review": "待审核", "approved": "允许退回", "rejected": "已拒绝", "all": "全部"}
CSS = """
.gradio-container {max-width:1440px!important; padding:clamp(8px,2vw,24px)!important;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',
    'Microsoft YaHei',sans-serif;
  background:#edf0f4; color:#172033;}
.gradio-container .main {padding:0!important;}
#review-header {padding:8px 0 20px; border-bottom:1px solid #dbe1e8;}
#review-header h1 {font-size:22px; font-weight:650; margin:0; color:#172033;}
#review-header p {color:#536174; line-height:1.75; margin:8px 0 0;}
#review-workspace {gap:0!important; border:1px solid #dbe1e8; border-radius:12px; overflow:hidden;}
#review-list {padding:16px!important; background:#e5e9ef;}
#review-reader {padding:24px!important; background:#fff;}
#review-workspace [data-testid='block-info'] {
  background:transparent; color:#536174; font-size:12px;}
button {min-height:40px; border-radius:8px!important;}
button.primary {background:#c64b06!important; color:#fff!important;}
button:active:not(:disabled) {transform:scale(.96);}
button:focus-visible,input:focus-visible,textarea:focus-visible {
  outline:2px solid #c64b06!important;}
.review-detail {line-height:1.8; color:#172033; overflow-wrap:anywhere;}
.review-detail h2 {font-size:20px; margin:12px 0;}
.review-detail h3 {font-size:15px; margin:20px 0 8px;}
.review-detail dl {display:grid;grid-template-columns:100px 1fr;gap:6px 12px;}
.review-detail dt {color:#536174;} .review-detail dd {margin:0;}
.review-detail small {color:#536174;}
.review-detail img,.review-detail video {max-width:100%;max-height:360px;object-fit:contain;}
.review-detail .evidence-image,.review-detail .evidence-video {
  display:block;width:100%;height:320px;object-fit:contain;
  background:#e2e8f0;border:1px solid #cbd5e1;border-radius:6px;
}
.evidence-meta {font-variant-numeric:tabular-nums;}
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
    order_status = {"delivered": "已签收", "shipped": "已发货",
                    "paid": "待发货", "pending": "待发货",
                    "cancelled": "已取消"}.get(order["status"], order["status"])
    lines = [("申请号", detail["request_id"]), ("客户", detail["user_id"]),
             ("订单 / 明细", f"{detail['order_id']} / {detail['item_id']}"),
             ("商品 / 数量", f"{content['product_name']} × {detail['quantity']}"),
             ("所购颜色", (content.get("sku") or {}).get("color") or "旧数据未提供"),
             ("原因", "质量问题" if content["reason"] == "quality" else "无理由"),
             ("提交时间", timestamp(detail["created_at"])),
             ("当前订单", f"{order_status} · 购买 {order['quantity']} 件"),
             ("签收日期", order["delivered_at"]),
             ("故障自述", content.get("problem_description") or "未提供"),
             ("商品状况自述", {"sealed": "未拆封", "opened": "已拆封"}.get(
                 content.get("condition"), content.get("condition") or "未提供")),
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
            suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
                      "video/mp4": "mp4"}[media["media_type"]]
            # Inline data avoids exposing evidence through a shared public file cache.
            if media["media_type"] == "video/mp4":
                tag = f"<video class='evidence-video' controls src='{url}'></video>"
                description = "MP4"
                note = "若视频无法播放，请下载原文件核对。上传不代表内容已审核。"
            else:
                with Image.open(io.BytesIO(media["data"])) as image:
                    width, height = image.size
                tag = f"<img class='evidence-image' src='{url}' alt='客户上传的原始退货凭证'>"
                description = f"{suffix.upper()} · {width} × {height} 像素"
                note = "若原图为空白、纯色或模糊图片，放大不会增加细节，需补充可辨认的凭证。"
            return ("<div class='review-detail'><h3>原始凭证预览</h3>"
                    f"<p class='evidence-meta'>{description} · {len(media['data'])} 字节</p>"
                    + tag + f"<p>{note}</p><p><a href='{url}' "
                    f"download='return-evidence.{suffix}'>下载原始凭证</a></p></div>")
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
        with gr.Row(elem_id="review-workspace"):
            with gr.Column(scale=2, min_width=260, elem_id="review-list"):
                status = gr.Dropdown([(v, k) for k, v in STATUS.items()],
                                     value="pending_review", label="申请状态")
                refresh_btn = gr.Button("刷新申请列表")
                selector = gr.Dropdown([], label="选择申请", elem_id="review-selector")
                message = gr.Markdown("", elem_id="review-message")
                gr.HTML("<a href='/logout'>退出管理账号</a>")
            with gr.Column(scale=5, min_width=280, elem_id="review-reader"):
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
    parser = argparse.ArgumentParser(description="售后审核工作台")
    parser.add_argument("--port", type=int, default=7861)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--scenario", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    if args.scenario:
        settings = scenario_settings(settings, args.scenario)
    demo = build_app(settings)
    service = ReturnService(settings)
    demo.launch(server_name=args.host, server_port=args.port, auth=service.authenticate_reviewer,
                auth_message="使用.env中单独配置的管理账号登录", css=CSS,
                theme=gr.themes.Soft(primary_hue="orange", neutral_hue="slate"),
                footer_links=[], show_error=False)
