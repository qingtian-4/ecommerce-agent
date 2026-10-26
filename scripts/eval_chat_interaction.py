"""Real Chromium keyboard/inline-SSE checks using offline SDK transport only.

Run with isolated Playwright tooling, not the application's paid credentials.
--baseline pins old assets for a genuine red run (full assets in presentation mode).
"""

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


def run(out, port, baseline, presentation=False):
    os.umask(0o077)
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError("Browser test port is already in use")
    out.mkdir(parents=True, exist_ok=False)
    private = out / ".private-evidence"
    private.mkdir(mode=0o700)
    runtime = private / "runtime"
    origin = f"http://127.0.0.1:{port}"
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "src") + ":" + str(ROOT / "scripts"),
        ACS_LLM_API_KEY="",
        ACS_EMBEDDING_API_KEY="",
    )
    log = (private / "server.log").open("w")
    server = subprocess.Popen(
        [
            str(ROOT / ".venv/bin/python"),
            "-c",
            "import sys;from pathlib import Path;from eval_answer_stream import serve;"
            "serve(Path(sys.argv[1]),int(sys.argv[2]),answer_text=sys.argv[3] or None)",
            str(runtime),
            str(port),
            (
                "已查询商品资料。\n\n**Anker 安克 65W GaN 充电器**\n\n"
                "| 项目 | 规格 |\n|---|---|\n| 总功率 | 65W |\n"
                "| 接口 | 2 个 USB-C、1 个 USB-A |\n\n"
                "支持 PD3.0 / QC4+，采用折叠插脚。需要的话，还可以继续查订单或物流。"
                if presentation
                else ""
            ),
        ],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=log,
    )
    checks, observed, network, page_errors = {}, [], [], []

    def ledger_count():
        p = runtime / "fees.jsonl"
        return sum(json.loads(s)["type"] == "reserved" for s in p.read_text().splitlines())

    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(origin, timeout=1).close()
                break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError("Offline server failed; see private log") from None
                time.sleep(0.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("response", lambda r: network.append([r.url.split(origin)[-1], r.status]))
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.add_init_script("""window.streamLog=[];const Original=window.EventSource;
              window.EventSource=function(...args){const source=new Original(...args);
              for(const type of ['answer_delta','turn_completed','turn_failed','tool_requested',
               'tool_completed'])source.addEventListener(type,e=>window.streamLog.push({
                 type,id:e.lastEventId,time:performance.now()}));return source;};""")
            if presentation:

                def metadata(route):
                    response = route.fetch()
                    value = response.json()
                    value["runtime"].update(
                        mode="deepseek",
                        label="离线 SDK 流式验收",
                        notice="合成数据与 MockTransport，无付费模型请求。",
                    )
                    route.fulfill(response=response, json=value)

                page.route("**/api/bootstrap", metadata)
            if baseline:
                reference = "de86dd0" if presentation else "d5d2198"

                def pinned_asset(content):
                    def handle(route):
                        response = route.fetch()
                        route.fulfill(response=response, body=content)

                    return handle

                for name in ["index.html", "app.js", "style.css"] if presentation else ["app.js"]:
                    old = subprocess.check_output(
                        ["git", "show", f"{reference}:src/acs/web_assets/{name}"], cwd=ROOT
                    )
                    url = origin + ("/" if name == "index.html" else "/assets/" + name)
                    page.route(url, pinned_asset(old))
            page.on(
                "request",
                lambda r: (
                    observed.append(r.url)
                    if r.method == "POST" and r.url.endswith("/messages")
                    else None
                ),
            )
            page.goto(origin)
            page.wait_for_selector("#send:not([disabled])")
            if presentation:
                checks["examples_visible_in_model_mode"] = page.locator("#examples").is_visible()
                count_before = len(observed)
                for name in ["商品咨询", "订单查询"]:
                    if page.get_by_role("button", name=name, exact=True).is_visible():
                        page.get_by_role("button", name=name, exact=True).click()
                checks["examples_only_fill_input"] = (
                    bool(page.locator("#message").input_value()) and len(observed) == count_before
                )
                checks["compact_composer"] = (
                    page.locator("#compose").bounding_box()["height"] <= 100
                )
                page.screenshot(path=str(out / "empty.png"), full_page=True)
            field = page.locator("#message")
            field.fill("第一行")
            field.press("Shift+Enter")
            checks["shift_enter_newline"] = field.input_value() == "第一行\n" and not observed
            for before in [True, False]:
                field.dispatch_event("compositionstart")
                if before:
                    field.dispatch_event("compositionend")
                field.dispatch_event(
                    "keydown", {"key": "Enter", "keyCode": 229, "isComposing": not before}
                )
                if not before:
                    field.dispatch_event("compositionend")
            checks["ime_commit_does_not_send"] = not observed

            def send(text):
                old_count = len(observed)
                field.fill(text)
                field.press("Enter")
                page.wait_for_timeout(100)
                sent = len(observed) == old_count + 1
                if baseline and not sent:
                    # Keep the failed keyboard assertion; use the button only to
                    # inspect the other baseline symptoms without faking a pass.
                    page.locator("#send").click()
                return sent

            checks["enter_sends_once"] = send("普通咨询")
            page.wait_for_timeout(250)
            (private / "first-request.json").write_text(
                json.dumps(
                    {
                        "requests": observed,
                        "network": network,
                        "page_errors": page_errors,
                        "activity": page.locator("#activity").inner_text(),
                        "notice": page.locator("#notice").inner_text(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            page.wait_for_selector("#chat .draft")
            checks["draft_before_completion"] = not page.evaluate(
                "streamLog.some(e=>e.type==='turn_completed')"
            )
            if presentation:
                checks["single_focus_ring"] = page.locator("#compose").evaluate(
                    "n=>getComputedStyle(n).outlineStyle==='solid'"
                ) and field.evaluate("n=>getComputedStyle(n).outlineStyle==='none'")
                checks["three_waiting_dots"] = (
                    page.locator(
                        '#chat .execution-progress[data-status="running"] .waiting-dots span'
                    ).count()
                    == 3
                )
                checks["waiting_animation_active"] = page.locator(
                    "#chat .waiting-dots span"
                ).evaluate_all("ns=>ns.some(n=>getComputedStyle(n).animationName!=='none')")
                page.emulate_media(reduced_motion="reduce")
                checks["reduced_motion_is_static"] = page.locator(
                    "#chat .waiting-dots span"
                ).evaluate_all(
                    "ns=>ns.length>0&&ns.every(n=>getComputedStyle(n).animationName==='none')"
                )
                page.emulate_media(reduced_motion="no-preference")
                page.screenshot(path=str(out / "loading.png"), full_page=True)
                before_reconnect = ledger_count()
                page.evaluate("source.close();source.onerror(new Event('error'))")
                checks["disconnect_stops_animation"] = page.locator(
                    "#chat .waiting-dots span"
                ).evaluate_all(
                    "ns=>ns.length>0&&ns.every(n=>getComputedStyle(n).animationName==='none')"
                )
                page.evaluate("connect()")
                page.wait_for_function(
                    "()=>document.getElementById('connection').textContent==='进度已连接'"
                )
                checks["reconnect_no_requests"] = ledger_count() == before_reconnect
            page.wait_for_function(
                "() => Array.from(document.querySelectorAll('#trace li'))"
                ".filter(n=>n.textContent.includes('执行完成')).length===1"
            )
            page.wait_for_selector("#send:not([disabled])")
            send("工具查询")
            page.wait_for_function("() => streamLog.some(e=>e.type==='tool_completed')")
            page.wait_for_selector("#chat .draft")
            checks["tool_progress_inside_chat_before_reply"] = (
                page.locator("#chat .execution-tools li").count() == 1
            )
            page.wait_for_function(
                "() => streamLog.filter(e=>e.type==='turn_completed').length===2"
            )
            page.wait_for_selector("#send:not([disabled])")
            if presentation:
                checks["no_visible_role_labels"] = page.locator("#chat .speaker").count() == 0
                checks["user_message_has_accessible_identity"] = (
                    page.locator('#chat .user[aria-label="用户消息"]').count() == 2
                )
                checks["completed_animation_stopped"] = page.locator(
                    "#chat .waiting-dots span"
                ).evaluate_all(
                    "ns=>ns.length>0&&ns.every(n=>getComputedStyle(n).animationName==='none')"
                )
            before = ledger_count()
            page.reload()
            page.wait_for_selector("#send:not([disabled])")
            page.wait_for_function(
                "() => Array.from(document.querySelectorAll('#trace li'))"
                ".filter(n=>n.textContent.includes('执行完成')).length===2"
            )
            checks["refresh_does_not_repeat_model_calls"] = ledger_count() == before
            checks["replay_has_one_tool_row"] = (
                page.locator("#chat .execution-tools li").count() == 1
            )
            page.locator("#chat .execution-progress").evaluate_all(
                "nodes=>nodes.forEach(n=>n.open=true)"
            )
            page.screenshot(path=str(out / "desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            checks["mobile_no_horizontal_overflow"] = page.evaluate(
                "document.documentElement.scrollWidth<=innerWidth+1"
            )
            page.screenshot(path=str(out / "mobile.png"), full_page=True)
            if presentation:
                geometry = {}
                for width in [320, 390, 850, 851, 1280]:
                    page.set_viewport_size({"width": width, "height": 900})
                    geometry[str(width)] = {
                        "overflow": page.evaluate(
                            "document.documentElement.scrollWidth>innerWidth+1"
                        ),
                        "composer_height": page.locator("#compose").bounding_box()["height"],
                    }
                checks["responsive_matrix_no_overflow"] = all(
                    not row["overflow"] for row in geometry.values()
                )
                (out / "geometry.json").write_text(json.dumps(geometry, indent=2) + "\n")
                page.set_viewport_size({"width": 390, "height": 844})
            send("协议拒绝")
            page.wait_for_function("() => streamLog.some(e=>e.type==='turn_failed')")
            checks["error_inside_chat"] = (
                page.locator("#chat .execution-progress[data-status='error']").count() == 1
            )
            checks["failed_draft_withdrawn"] = page.locator("#chat .draft").count() == 0
            page.screenshot(path=str(out / "error.png"), full_page=True)
            if presentation:
                checks["failed_animation_stopped"] = page.locator(
                    "#chat .waiting-dots span"
                ).evaluate_all(
                    "ns=>ns.length>0&&ns.every(n=>getComputedStyle(n).animationName==='none')"
                )
                checks["no_browser_script_errors"] = not page_errors
            checks["three_messages_only"] = len(observed) == 3
            browser.close()
        with sqlite3.connect((runtime / "business.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
            checks["no_formal_application"] = (
                conn.execute("SELECT COUNT(*) FROM return_requests").fetchone()[0] == 0
            )
    finally:
        server.terminate()
        server.wait(timeout=15)
        log.close()
    result = dict(
        kind="offline_real_sdk_and_chromium",
        baseline=baseline,
        presentation=presentation,
        assets_reference=("de86dd0" if presentation else "d5d2198") if baseline else "working_tree",
        paid_model_requests=0,
        checks=checks,
        passed=all(checks.values()),
    )
    (out / "scorecard.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--presentation", action="store_true")
    args = parser.parse_args()
    result = run(args.out.resolve(), args.port, args.baseline, args.presentation)
    raise SystemExit(0 if result["passed"] else 1)
