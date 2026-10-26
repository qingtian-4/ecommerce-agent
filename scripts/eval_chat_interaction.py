"""Real Chromium keyboard/inline-SSE checks using offline SDK transport only.

Run with isolated Playwright tooling, not the application's paid credentials.
--baseline routes only app.js to the accepted old asset for a genuine red run.
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


def run(out, port, baseline):
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
            "serve(Path(sys.argv[1]),int(sys.argv[2]))",
            str(runtime),
            str(port),
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
            if baseline:
                old = subprocess.check_output(
                    ["git", "show", "d5d2198:src/acs/web_assets/app.js"], cwd=ROOT
                )
                page.route(
                    "**/assets/app.js",
                    lambda r: r.fulfill(body=old, content_type="application/javascript"),
                )
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
            page.wait_for_function(
                "() => streamLog.filter(e=>e.type==='turn_completed').length===1"
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
            send("协议拒绝")
            page.wait_for_function("() => streamLog.some(e=>e.type==='turn_failed')")
            checks["error_inside_chat"] = (
                page.locator("#chat .execution-progress[data-status='error']").count() == 1
            )
            checks["failed_draft_withdrawn"] = page.locator("#chat .draft").count() == 0
            page.screenshot(path=str(out / "error.png"), full_page=True)
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
    args = parser.parse_args()
    result = run(args.out.resolve(), args.port, args.baseline)
    raise SystemExit(0 if result["passed"] else 1)
