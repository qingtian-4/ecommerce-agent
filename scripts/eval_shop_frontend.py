"""Chromium + actual SDK MockTransport + native Graph + fresh synthetic business SQL."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def run(out, port):
    os.umask(0o077)
    out.mkdir(parents=True, exist_ok=False)
    private = out / ".private-evidence"
    private.mkdir(mode=0o700)
    runtime = private / "runtime"
    origin = f"http://127.0.0.1:{port}"
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError("Acceptance port is already in use")
    log = (private / "server.log").open("w")
    env = dict(os.environ, ACS_LLM_API_KEY="", ACS_EMBEDDING_API_KEY="")

    def start(init=False):
        server = subprocess.Popen(
            [
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "scripts/v3_shop_demo.py"),
                "--state-dir",
                str(runtime),
                "--port",
                str(port),
                *(["--init"] if init else []),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
        )
        # Cold imports and durable SQLite fixture creation can exceed ten seconds
        # while the full offline suite is also doing fsyncs on the same disk.
        for _ in range(600):
            try:
                urllib.request.urlopen(origin + "/shop", timeout=1).close()
                return server
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError("Offline service failed to start") from None
                time.sleep(0.1)
        server.terminate()
        server.wait(timeout=15)
        raise RuntimeError("Offline service not ready after 60 seconds")

    server = start(True)
    checks, posts, errors = {}, [], []

    def sql(query, args=(), file="business.sqlite"):
        with sqlite3.connect((runtime / file).as_uri() + "?mode=ro", uri=True) as conn:
            return conn.execute(query, args).fetchall()

    def mutate(query, args=(), file="business.sqlite"):
        assert (
            json.loads((runtime / "fixture.json").read_text())["kind"]
            == "local-return-formal-synthetic-1"
        )
        with sqlite3.connect(runtime / file) as conn:
            conn.execute(query, args)

    def requests():
        path = runtime / "offline-fees.jsonl"
        return sum(json.loads(s)["type"] == "reserved" for s in path.read_text().splitlines())

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])

            def page_new(width=1380):
                context = browser.new_context(
                    viewport={"width": width, "height": 940 if width > 900 else 844}
                )
                page = context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
                page.add_init_script("""window.eventsSeen=[];window.streamVisible=false;
                  const Original=window.EventSource;
                  window.EventSource=function(...args){const s=new Original(...args);
                  for(const t of ['answer_delta','turn_completed','turn_failed','tool_completed'])
                    s.addEventListener(t,e=>{
                      window.eventsSeen.push({type:t,id:e.lastEventId});
                      if(t==='answer_delta')requestAnimationFrame(()=>{
                        if(document.querySelector('#chat .draft .message-text')?.textContent
                          && document.querySelector('#send')?.disabled)window.streamVisible=true;
                      });
                    });
                  return s;};""")
                page.goto(origin + "/shop")
                ready(page)
                return context, page

            def ready(page):
                page.wait_for_selector("#send:not([disabled])", timeout=45000)
                page.wait_for_function("()=>document.body.dataset.stream==='connected'")

            def send(page, text):
                before = len([u for u in posts if u.endswith("/messages")])
                page.locator("#message").fill(text)
                page.locator("#message").press("Enter")
                page.wait_for_function("()=>document.querySelector('#send').disabled")
                ready(page)
                assert len([u for u in posts if u.endswith("/messages")]) == before + 1

            def card(page, state):
                page.wait_for_selector('#return-card[data-state="' + state + '"]', timeout=45000)

            def prepare(page, item=4):
                if not page.locator('#chat [data-order-id="O10024"]').count():
                    send(page, "帮我查一下最近的订单，有多个订单请先展示候选。")
                target = page.locator('#chat [data-order-id="O10024"]').last
                if target.evaluate("n=>n.tagName==='DETAILS'") and not target.get_attribute("open"):
                    target.locator("summary").click()
                target.get_by_role("button", name="申请售后", exact=True).click()
                page.locator("#return-form [name=item_id]").select_option(str(item))
                page.locator("#return-form [name=sale_ok]").check()
                page.get_by_role("button", name="保存草稿并生成预览", exact=True).click()
                ready(page)

            context, page = page_new()
            checks["jump_latest_hidden_without_history"] = page.locator(
                "#jump-latest"
            ).is_hidden()
            page.locator("#message").fill("第一行")
            page.locator("#message").press("Shift+Enter")
            checks["shift_enter_newline_no_post"] = (
                page.locator("#message").input_value() == "第一行\n"
                and not any(url.endswith("/messages") for url in posts)
            )
            page.locator("#message").evaluate(
                "n=>n.dispatchEvent(new KeyboardEvent('keydown',"
                "{key:'Enter',bubbles:true,isComposing:true}))"
            )
            checks["ime_enter_no_post"] = not any(url.endswith("/messages") for url in posts)
            send(page, "测试订单表格，查询最近订单")
            checks["recent_three"] = page.locator("#chat .results .order-card").count() == 3
            checks["no_default_order_selection"] = (
                "尚未关联订单" in page.locator("#context-order").inner_text()
            )
            checks["order_table_collapsed"] = not page.locator("#chat .markdown-table").is_visible()
            checks["mixed_reply_preserved"] = (
                "咨询内容仍然保留" in page.locator("#chat").inner_text()
            )
            checks["real_sse_deltas"] = page.evaluate("eventsSeen.some(e=>e.type==='answer_delta')")
            checks["stream_visible_during_execution"] = page.evaluate("window.streamVisible")
            before = requests()
            page.goto(origin + "/")
            page.wait_for_selector("#send:not([disabled])")
            page.screenshot(path=str(out / "before-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(out / "before-mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1380, "height": 940})
            page.goto(origin + "/shop")
            ready(page)
            checks["navigation_no_model_replay"] = requests() == before
            page.get_by_role("button", name="查看全部订单 ›").click()
            page.wait_for_selector("#dialog[open] .order-card")
            assert page.locator("#dialog .order-card").count() == 3
            first = page.locator("#dialog .order-card").first.get_attribute("data-order-id")
            page.get_by_role("button", name="下一页", exact=True).click()
            page.wait_for_function(
                "()=>document.querySelector('#dialog .pager span').textContent.startsWith('2 /')"
            )
            checks["pagination"] = (
                page.locator("#dialog .order-card").first.get_attribute("data-order-id") != first
            )
            page.locator("#close-dialog").click()
            target = page.locator('#chat [data-order-id="O10024"]')
            target.locator("summary").click()
            target.get_by_role("button", name="查物流", exact=True).click()
            page.wait_for_selector("#dialog .timeline li")
            checks["logistics_from_sql"] = page.locator("#dialog .timeline li").count() == len(
                json.loads(sql("SELECT trace_json FROM logistics WHERE order_id='O10024'")[0][0])
            )
            page.locator("#close-dialog").click()
            page.get_by_role("button", name="有用", exact=True).click()
            page.wait_for_selector(".feedback-status")
            checks["feedback_persisted"] = sql(
                "SELECT vote FROM ui_feedback", file="web.sqlite"
            ) == [("helpful",)]
            page.get_by_role("button", name="没用", exact=True).click()
            page.get_by_role("button", name="事实有误", exact=True).click()
            page.wait_for_function(
                "()=>document.querySelector('[data-vote=unhelpful]').classList.contains('selected')"
            )
            checks["feedback_reason"] = sql(
                "SELECT vote,reason FROM ui_feedback ORDER BY id DESC LIMIT 1", file="web.sqlite"
            ) == [("unhelpful", "incorrect")]
            page.locator("#human").click()
            checks["human_unavailable"] = "人工服务暂未接入" in page.locator("#dialog").inner_text()
            page.locator("#close-dialog").click()
            send(page, "店里有哪些商品？请按实际商品资料介绍。")
            checks["product_grid_matches_sql"] = (
                page.locator("#chat .product-card").count()
                == sql("SELECT COUNT(*) FROM products")[0][0]
            )
            checks["product_images_labeled_illustrations"] = (
                page.locator('#chat .product-card [aria-label="商品类别示意图，非实拍"]').count()
                == 4
            )
            checks["feedback_each_completed_answer"] = page.locator(
                "#chat .assistant-message"
            ).evaluate_all("nodes=>nodes.every(n=>n.querySelector('.feedback'))")
            history = page.evaluate(
                "async()=>{const b=await(await fetch('/api/bootstrap')).json();"
                "return(await fetch('/api/sessions/'+b.session_id+'/messages')).json()}"
            )
            page.route(
                "**/messages",
                lambda r: r.fulfill(json=history) if r.request.method == "GET" else r.continue_(),
            )
            page.evaluate("""()=>{
                for(const n of document.querySelectorAll('#chat .progress-record'))
                  n.dataset.history='1';
                window.historyMoved=false;
                window.layoutObserver=new MutationObserver(()=>{
                  const last=[...document.querySelectorAll('#chat .user-message')].at(-1);
                  if(last?.classList.contains('outgoing'))
                    for(const n of document.querySelectorAll('#chat [data-history="1"]'))
                      if(last.compareDocumentPosition(n)&Node.DOCUMENT_POSITION_FOLLOWING)
                        window.historyMoved=true;
                });
                window.layoutObserver.observe(document.querySelector('#chat'),{childList:true});
            }""")
            old_progress = page.locator('#chat .progress-record').count()
            send(page, "查询订单测试等待状态")
            checks["history_not_moved_below_outgoing"] = not page.evaluate("historyMoved")
            checks["historical_progress_retained"] = (
                page.locator('#chat [data-history="1"]').count() == old_progress
                and old_progress > 0
            )
            page.evaluate("layoutObserver.disconnect()")
            page.unroute("**/messages")
            page.reload()
            ready(page)
            page.screenshot(path=str(out / "products-desktop.png"), full_page=True)
            page.locator("#chat").evaluate("n=>n.scrollTop=0")
            page.screenshot(path=str(out / "orders-desktop.png"), full_page=True)
            geometry = {}
            jump_positions = []
            before_jump = requests()
            for width in [320, 390, 900, 901, 1380]:
                page.set_viewport_size({"width": width, "height": 844 if width <= 900 else 940})
                page.locator("#chat").evaluate("n=>n.scrollTop=0")
                page.locator("#jump-latest").wait_for(state="visible")
                jump = page.locator("#jump-latest").bounding_box()
                composer = page.locator(".composer").bounding_box()
                jump_positions.append(jump["y"] + jump["height"] <= composer["y"])
                if width in [390, 1380]:
                    name = "mobile" if width == 390 else "desktop"
                    page.screenshot(path=str(out / ("jump-latest-" + name + ".png")))
                if width == 390:
                    page.locator("#jump-latest").focus()
                    page.keyboard.press("Enter")
                else:
                    page.locator("#jump-latest").click()
                page.wait_for_function(
                    "()=>{const n=document.querySelector('#chat');"
                    "return n.scrollHeight-n.clientHeight-n.scrollTop<2}"
                )
                page.locator("#jump-latest").wait_for(state="hidden")
                geometry[str(width)] = {
                    "overflow": page.evaluate("document.documentElement.scrollWidth>innerWidth+1"),
                    "composer_bottom": page.locator(".composer").bounding_box()["y"]
                    + page.locator(".composer").bounding_box()["height"],
                }
            checks["jump_latest_mouse_and_keyboard"] = True
            checks["jump_latest_above_composer"] = all(jump_positions)
            checks["jump_latest_no_model_replay"] = requests() == before_jump
            checks["responsive"] = all(not v["overflow"] for v in geometry.values())
            checks["fixed_input"] = all(
                v["composer_bottom"] <= (844 if int(k) <= 900 else 940) for k, v in geometry.items()
            )
            page.set_viewport_size({"width": 390, "height": 844})
            page.locator("#chat").evaluate("n=>n.scrollTop=0")
            page.screenshot(path=str(out / "orders-mobile.png"), full_page=True)
            page.locator("#mobile-service").click()
            checks["mobile_drawer"] = (
                page.locator("#context-drawer").evaluate("n=>n.open")
                and not page.locator("#desktop-context").is_visible()
            )
            page.screenshot(path=str(out / "service-drawer.png"), full_page=True)
            page.locator("#context-drawer").get_by_role("button", name="查看订单详情").click()
            page.wait_for_selector("#dialog[open] .order-card")
            checks["mobile_drawer_action_closes_drawer"] = not page.locator(
                "#context-drawer"
            ).evaluate("n=>n.open")
            page.locator("#close-dialog").click()
            page.set_viewport_size({"width": 1380, "height": 940})
            prepare(page, 4)
            card(page, "READY")
            anchor = page.locator('#return-card').evaluate(
                "n=>n.closest('.assistant-message')?.dataset.messageId"
            )
            send(page, "我插问一下退货政策，保留草稿。")
            checks["return_card_stays_at_business_turn"] = (
                page.locator('#return-card').evaluate(
                    "n=>n.closest('.assistant-message')?.dataset.messageId"
                ) == anchor
                and anchor is not None
            )
            checks["single_return_card_after_consultation"] = (
                page.locator('#return-card').count() == 1
            )
            page.reload()
            ready(page)
            checks["return_anchor_restored_after_refresh"] = (
                page.locator('#return-card').evaluate(
                    "n=>n.closest('.assistant-message')?.dataset.messageId"
                ) == anchor
            )
            preview = sql("SELECT preview_id FROM return_previews ORDER BY rowid DESC LIMIT 1")[0][
                0
            ]
            page.get_by_role("button", name="检查预览并打开确认", exact=True).click()
            ready(page)
            page.wait_for_selector("[data-action=accept]")
            prepare(page, 5)
            card(page, "EXPIRED")
            checks["correction_invalidates_old_wait"] = (
                sql("SELECT json_extract(data_json,'$.revision') FROM return_tasks")[0][0] > 1
                and sql("SELECT COUNT(*) FROM return_requests")[0][0] == 0
            )
            checks["stale_accept_absent"] = page.locator("[data-action=accept]").count() == 0
            page.locator("[data-action=cancel]").click()
            ready(page)
            card(page, "READY")
            checks["new_preview"] = (
                sql("SELECT preview_id FROM return_previews ORDER BY rowid DESC LIMIT 1")[0][0]
                != preview
            )
            page.get_by_role("button", name="检查预览并打开确认", exact=True).click()
            ready(page)
            page.route(
                "**/decision",
                lambda route: route.fulfill(
                    status=409, json={"detail": "injected explicit rejection"}
                ),
            )
            page.locator("[data-action=accept]").click()
            ready(page)
            checks["explicit_4xx_keeps_ready"] = (
                page.locator("#return-card").get_attribute("data-state") == "READY"
                and sql("SELECT COUNT(*) FROM return_requests")[0][0] == 0
            )
            page.unroute("**/decision")
            block = {"value": True}

            def card_read(route):
                if block["value"]:
                    route.fulfill(status=503, json={"detail": "injected read receipt unavailable"})
                else:
                    route.continue_()

            page.route("**/return-card", card_read)
            decision_posts = []

            def lost(route):
                response = route.fetch()
                decision_posts.append(response.status)
                deadline = time.monotonic() + 15
                while sql("SELECT COUNT(*) FROM return_requests")[0][0] != 1:
                    assert time.monotonic() < deadline
                    time.sleep(0.02)
                route.abort("connectionreset")

            page.route("**/decision", lost)
            page.locator("[data-action=accept]").evaluate("b=>{b.click();b.click()}")
            card(page, "UNKNOWN")
            page.wait_for_timeout(300)
            checks["unknown_no_accept"] = page.locator("[data-action=accept]").count() == 0
            main_sid = page.evaluate(
                "async()=>(await(await fetch('/api/bootstrap')).json()).session_id"
            )
            checks["legacy_pending_key_reused"] = page.evaluate(
                "sid=>!!sessionStorage.getItem('v3-confirm-attempt-'+sid)", main_sid
            )
            page.screenshot(path=str(out / "unknown.png"), full_page=True)
            block["value"] = False
            page.get_by_role("button", name="只读核对结果", exact=True).click()
            card(page, "PENDING_REVIEW")
            checks["single_submission_and_confirmation"] = (
                sql("SELECT COUNT(*) FROM return_requests")[0][0]
                == sql("SELECT COUNT(*) FROM return_confirmations")[0][0]
                == sql("SELECT COUNT(*) FROM return_audit")[0][0]
                == 1
            )
            checks["double_click_one_post"] = len(decision_posts) == 1
            request_id = sql("SELECT request_id FROM return_requests")[0][0]
            checks["authoritative_request_id"] = (
                page.locator(".verified-request").get_attribute("data-request-id") == request_id
            )
            checks["verified_transport_error_cleared"] = page.locator("#global-error").is_hidden()
            page.screenshot(path=str(out / "pending-review.png"), full_page=True)
            before = requests()
            page.reload()
            ready(page)
            card(page, "PENDING_REVIEW")
            checks["refresh_restores_feedback"] = (
                page.locator(".feedback [data-vote=unhelpful].selected").count() == 1
            )
            checks["refresh_no_replay"] = requests() == before
            server.terminate()
            server.wait(timeout=15)
            page.wait_for_function("()=>document.body.dataset.stream==='reconnecting'")
            checks["sse_disconnect_keeps_sql"] = (
                sql("SELECT COUNT(*) FROM return_requests")[0][0] == 1
            )
            server = start()
            ready(page)
            card(page, "PENDING_REVIEW")
            checks["sse_reconnect_no_replay"] = requests() == before
            checks["sse_reconnect_keeps_receipt"] = (
                page.locator(".verified-request").get_attribute("data-request-id") == request_id
            )
            sid = page.evaluate(
                "async()=> (await (await fetch('/api/bootstrap')).json()).session_id"
            )
            mutate("UPDATE checkpoints SET checkpoint=?", (b"broken-mirror",), file="graph.sqlite")
            page.reload()
            ready(page)
            card(page, "PENDING_REVIEW")
            checks["sql_receipt_survives_graph_corruption"] = (
                page.locator(".verified-request").get_attribute("data-request-id") == request_id
            )
            server.terminate()
            server.wait(timeout=15)
            server = start()
            page.reload()
            ready(page)
            card(page, "PENDING_REVIEW")
            checks["server_restart"] = (
                requests() == before
                and page.locator(".verified-request").get_attribute("data-request-id") == request_id
            )
            other_context, other = page_new(390)
            other_sid = other.evaluate(
                "async()=> (await (await fetch('/api/bootstrap')).json()).session_id"
            )
            checks["cross_session_rejected"] = (
                other.evaluate(
                    "async sid=>(await fetch('/api/sessions/'+sid+'/orders')).status", sid
                )
                == 403
            )
            checks["csrf_rejected"] = (
                other.evaluate(
                    "async sid=>(await fetch('/api/sessions/'+sid+'/feedback',{method:'POST',"
                    "headers:{'Content-Type':'application/json'},body:JSON.stringify({turn_id:'x',"
                    "vote:'helpful',request_key:'forged-csrf-test'})})).status",
                    other_sid,
                )
                == 403
            )
            prepare(other, 4)
            card(other, "READY")
            other.get_by_role("button", name="检查预览并打开确认", exact=True).click()
            ready(other)
            other.locator("[data-action=reject]").click()
            ready(other)
            card(other, "REJECTED")
            checks["reject_no_application"] = sql("SELECT COUNT(*) FROM return_requests")[0][0] == 1
            prepare(other, 4)
            card(other, "READY")
            other.get_by_role("button", name="检查预览并打开确认", exact=True).click()
            ready(other)
            other.locator("[data-action=cancel]").click()
            ready(other)
            card(other, "REJECTED")
            checks["cancel_no_application"] = sql("SELECT COUNT(*) FROM return_requests")[0][0] == 1
            prepare(other, 4)
            card(other, "READY")
            other.get_by_role("button", name="检查预览并打开确认", exact=True).click()
            ready(other)
            mutate(
                "UPDATE return_previews SET expires_at='2000-01-01T00:00:00+00:00' "
                "WHERE session_id=?",
                (other_sid,),
            )
            other.reload()
            ready(other)
            card(other, "EXPIRED")
            checks["expired_no_accept"] = (
                other.locator("[data-action=accept]").count() == 0
                and sql("SELECT COUNT(*) FROM return_requests")[0][0] == 1
            )
            other.screenshot(path=str(out / "expired-mobile.png"), full_page=True)
            cached_snapshot = other.evaluate(
                "async sid=>(await(await fetch('/api/sessions/'+sid)).json())", other_sid
            )
            snapshot_url = origin + "/api/sessions/" + other_sid
            other.route(snapshot_url, lambda route: route.fulfill(json=cached_snapshot))
            other.locator("#message").fill("测试等待状态")
            other.locator("#message").press("Enter")
            other.wait_for_selector(".progress-record[data-status=running]")
            checks["sse_running_overrides_stale_snapshot"] = other.locator("#send").is_disabled()
            other.unroute(snapshot_url)
            other.wait_for_function(
                "()=>document.querySelectorAll('.progress-record[data-status=running]').length===0"
            )
            ready(other)
            send(other, "触发离线协议错误")
            other.wait_for_selector(".progress-record[data-status=failed]")
            checks["protocol_error_visible"] = (
                other.locator(".progress-record[data-status=failed]").count() > 0
            )
            checks["failed_draft_withdrawn"] = other.locator("#chat .draft").count() == 0
            checks["script_errors_absent"] = not errors
            other_context.close()
            context.close()
            browser.close()
        (out / "geometry.json").write_text(json.dumps(geometry, indent=2) + "\n")
        result = {
            "source_base_sha": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "kind": "real_chromium_native_graph_mock_sdk",
            "paid_requests": 0,
            "mock_sdk_requests": requests(),
            "checks": checks,
            "passed": all(checks.values()),
            "sql_counts": {
                t: sql("SELECT COUNT(*) FROM " + t)[0][0]
                for t in [
                    "return_requests",
                    "return_confirmations",
                    "return_audit",
                    "local_submission_receipts",
                ]
            },
        }
        (out / "scorecard.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False))
    finally:
        server.terminate()
        server.wait(timeout=15)
        log.close()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8781)
    args = parser.parse_args()
    result = run(args.out.resolve(), args.port)
    raise SystemExit(0 if result["passed"] else 1)
