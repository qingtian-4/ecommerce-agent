"""Real Gradio action-completeness acceptance with a labelled scripted model, zero API."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]


def serve(scenario, port, admin=False):
    if admin:
        from scripts.check_delivery_ui import serve as existing

        existing(scenario, port, True, True)
        return
    import app
    from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall

    from acs.config import Settings
    from acs.cs_tools import ToolRegistry
    from acs.scenarios import build_scenario, scenario_settings
    from acs.service_agent import CustomerServiceAgent
    from acs.sessions import SessionStore

    if not scenario.exists():
        build_scenario(scenario)
    settings = scenario_settings(
        Settings(_env_file=None, llm_api_key="", embedding_api_key=""), scenario
    ).model_copy(
        update={
            "return_state_check_enabled": True,
            "knowledge_mode": "documents",
            "write_receipt_guard_enabled": True,
        }
    )

    class Model(ScriptedLLM):
        def reset_usage(self):
            self.calls = []

        def chat(self, messages, **kwargs):
            query = next(m["content"] for m in reversed(messages) if m["role"] == "user")
            if "S20002" in query:
                calls = [
                    ScriptedToolCall("o", "get_order", '{"order_id":"S20002"}'),
                    ScriptedToolCall(
                        "w",
                        "update_return_task",
                        json.dumps(
                            dict(
                                order_id="S20002",
                                item_id=3,
                                quantity=1,
                                reason="no_reason",
                                condition="sealed",
                                secondary_sale_ok=True,
                            )
                        ),
                    ),
                ]
                answer = "草稿已保存，尚未提交申请。"
            elif "不是要取消" in query:
                calls = [ScriptedToolCall("cancel", "cancel_return_task", "{}")]
                answer = "已经清空草稿。"
            else:
                calls = [
                    ScriptedToolCall("o", "get_order", '{"order_id":"S20017"}'),
                    ScriptedToolCall(
                        "p", "get_knowledge_documents", '{"document_ids":["return_policy"]}'
                    ),
                ]
                answer = (
                    "无理由7天，需不影响二次销售；质量问题15天，须非人为损坏。"
                    "请选择商品明细，尚未提交申请。"
                )
            self._responses = (
                [ScriptedResponse(wants_tools=True, tool_calls=calls)]
                if not self.calls
                else [ScriptedResponse(text=answer)]
            )
            return super().chat(messages, **kwargs)

    app.SETTINGS = settings
    app._SESSION_STORE = SessionStore(settings.db_path)
    app.SHOP_NAME = "执行完整性验收（脚本模型，零付费）"
    app.ensure_agent = lambda state, key: CustomerServiceAgent(
        settings=settings, llm=Model([]), registry=ToolRegistry(settings)
    )
    app.build_app().launch(server_name="127.0.0.1", server_port=port, css=app.CSS)


def browser(url, admin, out):
    from playwright.sync_api import expect, sync_playwright

    out.mkdir(parents=True, exist_ok=False)
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page(viewport={"width": 1440, "height": 1100})
        page.goto(url, wait_until="domcontentloaded")
        page.locator("#message-input textarea").wait_for()
        page.locator("#return-task").wait_for()
        page.evaluate(
            "window.seen=[]; window.watch=setInterval(()=>window.seen.push("
            "document.querySelector('#customer-chat').innerText),50)"
        )

        def send(text):
            expect(page.locator("#send-message")).to_be_enabled()
            page.locator("#message-input textarea").fill(text)
            page.locator("#send-message").click()

        send("我想退S20017，同时说明退货期限")
        page.wait_for_function(
            "document.querySelector('#return-task').innerText.includes('S20017')"
        )
        page.wait_for_function(
            "document.querySelector('#customer-chat').innerText.includes('状态更正')"
        )
        chat = page.locator("#customer-chat").inner_text()
        assert "7天" in chat and "15天" in chat and "未替您选择商品" in chat
        page.screenshot(path=str(out / "mixed_order_only.png"), full_page=True)
        (out / "frames.json").write_text(
            json.dumps(
                page.evaluate("clearInterval(window.watch);window.seen"),
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        page.reload(wait_until="domcontentloaded")
        page.locator("#conversation-history input").first.wait_for()
        page.locator("#conversation-history input").first.check()
        page.wait_for_function(
            "document.querySelector('#return-task').innerText.includes('S20017')"
        )
        page.screenshot(path=str(out / "restored.png"), full_page=True)
        page.evaluate(
            "window.seen=[]; window.watch=setInterval(()=>window.seen.push("
            "document.querySelector('#customer-chat').innerText),50)"
        )
        send("我想退S20002，耳机退1件，无理由，未拆封，符合二次销售条件。")
        page.wait_for_function(
            "document.querySelector('#return-task').innerText.includes('S20002')"
        )
        send("我不是要取消退货草稿，只是问问。")
        page.wait_for_function(
            "document.querySelector('#customer-chat').innerText.includes('草稿已保留')"
        )
        page.get_by_role("button", name="核查并生成申请预览").click()
        submit = page.get_by_role("button", name="确认提交申请", exact=True)
        expect(submit).to_be_enabled(timeout=60000)
        submit.click()
        page.wait_for_function(
            "document.querySelector('#return-requests').innerText.includes('待审核')"
        )
        page.screenshot(path=str(out / "submitted.png"), full_page=True)
        reviewer = b.new_page()
        reviewer.goto(admin, wait_until="domcontentloaded")
        reviewer.locator("input").nth(0).fill("demo-reviewer")
        reviewer.locator("input[type=password]").fill("demo-test-password")
        reviewer.get_by_role("button", name="Login", exact=False).click()
        reviewer.locator("#review-selector input").wait_for()
        reviewer.locator("#review-selector input").click()
        reviewer.get_by_role("option").filter(has_text="S20002").first.click()
        expect(reviewer.get_by_role("button", name="通过：允许退回")).to_be_enabled()
        reviewer.get_by_label("审核原因（必填，客户可见）").fill("离线验收通过，尚未退款。")
        reviewer.get_by_role("button", name="通过：允许退回").click()
        page.get_by_role("button", name="查询提交结果").click()
        page.wait_for_function(
            "document.querySelector('#return-requests').innerText.includes('允许退回商品')"
        )
        page.screenshot(path=str(out / "reviewed_queried.png"), full_page=True)
        first_frames = json.loads((out / "frames.json").read_text())
        later_frames = page.evaluate("clearInterval(window.watch);window.seen")
        for frame in first_frames + later_frames:
            assert "退款已到账" not in frame and "已经清空草稿。" not in frame
        (out / "all_frames.json").write_text(
            json.dumps(
                dict(order_only=first_frames, workflow=later_frames), ensure_ascii=False, indent=2
            )
            + "\n"
        )
        (out / "summary.json").write_text(
            json.dumps(
                dict(
                    model="scripted",
                    visible_samples=len(first_frames) + len(later_frames),
                    incorrect_completion_samples=0,
                    paid_requests=0,
                    mixed_order_only=True,
                    policy_preserved=True,
                    page_restored=True,
                    correction_preview_submit_review_query=True,
                    negative_cancel_blocked=True,
                    first_answer=chat,
                ),
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        b.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--serve", action="store_true")
    p.add_argument("--admin", action="store_true")
    p.add_argument("--scenario", type=Path)
    p.add_argument("--port", type=int, default=7910)
    p.add_argument("--url", default="http://127.0.0.1:7910")
    p.add_argument("--admin-url", default="http://127.0.0.1:7911")
    p.add_argument("--out", type=Path)
    a = p.parse_args()
    if a.serve:
        serve(a.scenario, a.port, a.admin)
    else:
        browser(a.url, a.admin_url, a.out)
