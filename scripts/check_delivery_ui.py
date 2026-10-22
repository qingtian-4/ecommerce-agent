"""Real Gradio/browser acceptance using deliberately wrong scripted model output (zero API)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def serve(scenario, port, candidate, admin, faults=False):
    import app
    from pydantic import SecretStr
    from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall

    from acs.config import Settings
    from acs.cs_tools import ToolRegistry
    from acs.scenarios import build_scenario, scenario_settings
    from acs.service_agent import CustomerServiceAgent
    from acs.sessions import SessionStore

    if not scenario.exists():
        build_scenario(scenario)
    settings = scenario_settings(Settings(_env_file=None, llm_api_key=""), scenario).model_copy(
        update={
            "return_state_check_enabled": True,
            "workflow_router_enabled": candidate,
            "runtime_recovery_enabled": candidate,
            "execution_progress_enabled": True,
            "order_candidate_render_enabled": True,
            "order_constraint_enabled": candidate,
            "order_aggregation_enabled": candidate,
            "admin_username": "demo-reviewer",
            "admin_password": SecretStr("demo-test-password"),
        }
    )
    if admin:
        import admin_app

        from acs.returns import ReturnService

        admin_app.build_app(settings).launch(
            server_name="127.0.0.1",
            server_port=port,
            auth=ReturnService(settings).authenticate_reviewer,
            show_error=False,
            css=admin_app.CSS,
        )
        return

    class Model(ScriptedLLM):
        def reset_usage(self):
            self.calls = []

        def chat_stream(self, messages, **kwargs):
            query = next(m["content"] for m in reversed(messages) if m["role"] == "user")
            first = not self.calls
            if '重试演示' in query and not getattr(self, 'retried', False):
                self.retried = True
                exc = RuntimeError('injected temporary provider failure')
                exc.status_code = 503
                raise exc
            calls = []
            if faults and "多少笔" in query:
                text = "合计25笔订单。"
            elif faults and "多少条" in query or "多少件" in query or "统计" in query:
                calls = [ScriptedToolCall("orders", "list_orders", "{}")]
                text = "合计25件商品。"
            elif "不是要取消" in query:
                calls = [ScriptedToolCall("cancel", "cancel_return_task", "{}")]
                text = "已经清空退货草稿。"
            elif "商品V201" in query:
                calls = [ScriptedToolCall("product", "get_product", '{"product_id":"V201"}')]
                text = "商品V201的规格请以本轮商品工具记录为准。"
            elif "退货政策" in query:
                calls = [ScriptedToolCall(
                    "policy", "get_knowledge_documents",
                    '{"document_ids":["return_policy"]}',
                )]
                text = "无理由7天，质量问题15天；以return_policy.md政策和实际资格核查为准。"
            elif "查S20008" in query:
                calls = [ScriptedToolCall("order", "get_order", '{"order_id":"S20008"}')]
                text = "S20008的V201-2商品为白色，订单状态为已发货。"
            elif "退S20017" in query:
                calls = [ScriptedToolCall("order", "get_order", '{"order_id":"S20017"}')]
                text = "请补充需要退货的商品明细。"
            elif "退S20002" in query:
                calls = [
                    ScriptedToolCall("order", "get_order", '{"order_id":"S20002"}'),
                    ScriptedToolCall(
                        "task",
                        "update_return_task",
                        json.dumps(
                            {
                                "order_id": "S20002",
                                "item_id": 3,
                                "quantity": 1,
                                "reason": "no_reason",
                                "condition": "sealed",
                                "secondary_sale_ok": True,
                            }
                        ),
                    ),
                ]
                text = "退货草稿已保存，尚未创建申请，请核查预览后明确确认。"
            else:
                text = "您好，请问需要查询哪笔订单？"
            self._responses = [
                ScriptedResponse(wants_tools=True, tool_calls=calls)
                if first and calls
                else ScriptedResponse(text=text)
            ]
            response = self.chat(messages, **kwargs)
            if not response.wants_tools:
                for part in [response.text[:4], response.text[4:]]:
                    yield part
                    if faults and "多少条" in query:
                        raise RuntimeError("injected stream loss")
                    if faults and "统计已签收" in query:
                        from acs.runtime import BudgetExceeded

                        raise BudgetExceeded("injected budget timeout")
                    time.sleep(1 if faults else 0.05)
            yield response

    app.SETTINGS = settings
    app._SESSION_STORE = SessionStore(settings.db_path)
    app.SHOP_NAME = "离线验收商店"
    app.ensure_agent = lambda state, key: CustomerServiceAgent(
        settings=settings, llm=Model([]), registry=ToolRegistry(settings)
    )
    app.build_app().launch(server_name="127.0.0.1", server_port=port, show_error=True, css=app.CSS)


def browser_faults(output, url):
    from playwright.sync_api import sync_playwright

    output.mkdir(parents=True, exist_ok=False)
    with sync_playwright() as manager:
        instance = manager.chromium.launch(headless=True)
        page = instance.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="domcontentloaded")
        page.locator("#message-input textarea").wait_for()
        page.locator("#return-task").wait_for()
        snapshots = {}
        queries = {
            "no_tool": "我的订单一共有多少笔订单？",
            "stream_loss": "我的订单一共有多少条商品明细？",
            "budget_timeout": "统计已签收订单的订单数、商品明细数和商品件数。",
            "client_reload": "我的订单一共买了多少件商品？只给总件数，不要列表。",
        }
        for name, query in queries.items():
            page.locator("#new-conversation").click()
            page.wait_for_function(
                "document.querySelector('#customer-chat').innerText.includes('从一个具体问题开始')"
            )
            page.evaluate("""() => {window.frames=[];
                let el=document.querySelector('#customer-chat');
                window.watcher=new MutationObserver(()=>window.frames.push(el.innerText));
                window.watcher.observe(el,{subtree:true,childList:true,characterData:true});}""")
            page.locator("#message-input textarea").fill(query)
            page.locator("#send-message").click()
            if name == "client_reload":
                page.wait_for_timeout(300)
                snapshots[name] = page.evaluate("window.frames")
                page.reload(wait_until="domcontentloaded")
                page.locator("#conversation-history input").first.wait_for()
                page.locator("#conversation-history input").first.check()
                page.wait_for_timeout(2500)
                snapshots[name].append(page.locator("#customer-chat").inner_text())
            else:
                page.wait_for_function(
                    "document.querySelector('#customer-chat').innerText.includes('未取得可核验')"
                )
                snapshots[name] = page.evaluate("window.frames")
                page.evaluate("window.watcher.disconnect()")
            assert not any("25" in value for value in snapshots[name]), (name, snapshots[name])
            page.screenshot(path=str(output / (name + ".png")), full_page=True)
            (output / "frames.json").write_text(json.dumps(snapshots, ensure_ascii=False, indent=2))
        (output / "summary.json").write_text(
            json.dumps(
                {
                    "cases": list(queries),
                    "wrong_visible_frames": 0,
                    "paid_calls": 0,
                    "client_reload_restore": True,
                },
                indent=2,
            )
        )
        instance.close()


def browser(output, url, baseline, admin, video=False):
    from playwright.sync_api import expect, sync_playwright

    output.mkdir(parents=True, exist_ok=False)
    with sync_playwright() as manager:
        browser = manager.chromium.launch(headless=True)
        options = {"record_video_dir": str(output / "video")} if video else {}
        context = browser.new_context(viewport={"width": 1440, "height": 1100}, **options)
        page = context.new_page()
        frames = {}

        def observe(label):
            page.evaluate("""() => { window.visibleFrames=[]; let el=document.querySelector(
                '#customer-chat'); window.visibleObserver=new MutationObserver(()=>{
                window.visibleFrames.push(el.innerText);});
                window.visibleObserver.observe(el,{subtree:true,childList:true,
                    characterData:true}); }
            """)

        def collect(label):
            frames[label] = page.evaluate("window.visibleFrames")
            page.evaluate("window.visibleObserver.disconnect()")
            (output / "frames.json").write_text(json.dumps(frames, ensure_ascii=False, indent=2))

        def send(text):
            expect(page.locator('#send-message')).to_be_enabled()
            page.locator("#message-input textarea").fill(text)
            page.locator("#send-message").click()

        page.goto(baseline, wait_until="domcontentloaded")
        page.locator("#message-input textarea").wait_for()
        page.locator("#return-task").wait_for()
        observe("baseline")
        send("我的订单一共买了多少件商品？只给总件数，不要列表。")
        page.wait_for_function(
            "document.querySelector('#customer-chat').innerText.includes('25件')"
        )
        collect("baseline")
        page.screenshot(path=str(output / "baseline.png"), full_page=True)
        page.goto(url, wait_until="domcontentloaded")
        page.locator("#message-input textarea").wait_for()
        page.locator("#return-task").wait_for()
        observe("candidate")
        send("我的订单一共买了多少件商品？只给总件数，不要列表。")
        page.wait_for_function(
            "document.querySelector('#customer-chat').innerText.includes('29 件商品')"
        )
        collect("candidate")
        assert frames["candidate"] and not any("25件" in f for f in frames["candidate"])
        page.screenshot(path=str(output / "candidate.png"), full_page=True)
        page.locator("#new-conversation").click()
        page.wait_for_function(
            "document.querySelector('#customer-chat').innerText.includes('从一个具体问题开始')"
        )
        for query, expected in (
            ("查询商品V201的规格。", "商品V201的规格请以本轮商品工具记录为准"),
            ("退货政策的期限是什么？", "质量问题15天"),
            ("查S20008的颜色和状态。", "V201-2商品为白色"),
        ):
            send(query)
            page.wait_for_function(
                "value => document.querySelector('#customer-chat').innerText.includes(value)",
                arg=expected,
            )
        page.screenshot(path=str(output / "knowledge.png"), full_page=True)
        page.locator("#new-conversation").click()
        page.wait_for_function(
            "document.querySelector('#customer-chat').innerText.includes('从一个具体问题开始')"
        )
        send("我想退S20017。")
        page.wait_for_function(
            "document.querySelector('#return-task').innerText.includes('S20017')"
        )
        page.screenshot(path=str(output / "order_only.png"), full_page=True)
        send("我想退S20002，耳机退1件，无理由，未拆封，符合二次销售条件。")
        page.wait_for_function(
            "!document.querySelector('#send-message').disabled"
        )
        page.wait_for_function(
            "document.querySelector('#return-task').innerText.includes('S20002')"
        )
        page.reload(wait_until="domcontentloaded")
        page.locator("#conversation-history input").first.wait_for()
        page.locator("#conversation-history input").first.check()
        page.wait_for_function(
            "document.querySelector('#return-task')?.innerText.includes('S20002')"
        )
        page.screenshot(path=str(output / "restored.png"), full_page=True)
        send("我不是要取消退货草稿，只是问问。")
        page.wait_for_function(
            "document.querySelector('#customer-chat').innerText.includes('草稿已保留')"
        )
        page.get_by_role("button", name="核查并生成申请预览").click()
        page.get_by_role("button", name="确认提交申请", exact=True).wait_for()
        page.wait_for_function(
            "!Array.from(document.querySelectorAll('button')).find(b=>b.innerText==='确认提交申请').disabled"
        )
        page.get_by_role("button", name="确认提交申请", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#return-requests')?.innerText.includes('待审核')"
        )
        page.screenshot(path=str(output / "submitted.png"), full_page=True)
        customer = page
        reviewer = context.new_page()
        reviewer.goto(admin, wait_until="domcontentloaded")
        reviewer.locator("input").nth(0).fill("demo-reviewer")
        reviewer.locator("input[type=password]").fill("demo-test-password")
        reviewer.get_by_role("button", name="Login", exact=False).click()
        reviewer.locator("#review-selector input").wait_for()
        reviewer.locator("#review-selector input").click()
        reviewer.get_by_role("option").filter(has_text="S20002").last.click()
        expect(reviewer.get_by_role("button", name="通过：允许退回")).to_be_enabled()
        reviewer.wait_for_function("document.body.innerText.includes('S20002 / 3')")
        reviewer.get_by_label("审核原因（必填，客户可见）").fill(
            "离线演示核对通过，允许退回，未退款。"
        )
        reviewer.get_by_role("button", name="通过：允许退回").click()
        reviewer.wait_for_timeout(800)
        reviewer.screenshot(path=str(output / "reviewed.png"), full_page=True)
        customer.get_by_role("button", name="查询提交结果").click()
        customer.wait_for_function(
            "document.querySelector('#return-requests')?.innerText.includes('允许退回商品')"
        )
        customer.screenshot(path=str(output / "queried.png"), full_page=True)
        (output / "frames.json").write_text(json.dumps(frames, ensure_ascii=False, indent=2) + "\n")
        (output / "summary.json").write_text(
            json.dumps(
                {
                    "baseline_wrong_visible": any("25件" in f for f in frames["baseline"]),
                    "candidate_wrong_visible": False,
                    "candidate_frame_count": len(frames["candidate"]),
                    "preview_submit_admin_query": True,
                    "negative_cancel_blocked": True,
                    "product_policy_order_tools": True,
                    "order_correction_and_page_restore": True,
                    "knowledge_mode": "documents; not vector RAG",
                    "video_recorded": video,
                    "paid_calls": 0,
                },
                indent=2,
            )
            + "\n"
        )
        context.close()
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--faults", action="store_true")
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--port", type=int, default=7890)
    parser.add_argument("--candidate", action="store_true")
    parser.add_argument("--admin", action="store_true")
    parser.add_argument("--video", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:7890")
    parser.add_argument("--baseline", default="http://127.0.0.1:7892")
    parser.add_argument("--admin-url", default="http://127.0.0.1:7891")
    args = parser.parse_args()
    if args.serve:
        serve(args.scenario, args.port, args.candidate, args.admin, args.faults)
    elif args.faults:
        browser_faults(args.out, args.url)
    else:
        browser(args.out, args.url, args.baseline, args.admin_url, args.video)
