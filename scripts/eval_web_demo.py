"""Real Chromium interaction evidence, fixed model or one explicitly capped paid smoke."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import time
import urllib.request
from contextlib import closing
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PYTHON = ROOT / ".venv/bin/python"


def run(out, port, *, deepseek=False, env_file=None, price_review=None):
    os.umask(0o077)
    out.mkdir(parents=True, exist_ok=False)
    private = out / ".private-evidence"
    private.mkdir(mode=0o700)
    runtime = private / "runtime"
    origin = f"http://127.0.0.1:{port}"
    files = [
        *sorted((ROOT / "src/acs").glob("*.py")),
        *sorted((ROOT / "src/acs/web_assets").glob("*")),
        ROOT / "scripts/v3_web_demo.py",
        ROOT / "scripts/local_return_poc.py",
        Path(__file__),
        ROOT / "uv.lock",
    ]
    fingerprints = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
    }
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    records, errors = [], []
    server = None
    log = (private / "server.log").open("w")

    def start(init=False):
        nonlocal server
        command = [
            str(RUNTIME_PYTHON),
            "scripts/v3_web_demo.py",
            "--state-dir",
            str(runtime),
            "--port",
            str(port),
        ]
        if init:
            command.append("--init")
        if deepseek:
            command += ["--deepseek", "--env-file", str(env_file)]
        server = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(origin, timeout=1).close()
                if init and price_review:
                    (runtime / "price_review.json").write_bytes(price_review.read_bytes())
                return
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError("Web server exited, see private server.log") from None
                time.sleep(0.1)
        raise TimeoutError("Web server startup timeout")

    def sql():
        with closing(sqlite3.connect(runtime / "business.sqlite")) as conn:
            conn.row_factory = sqlite3.Row
            return {
                t: [dict(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]
                for t in (
                    "return_tasks",
                    "return_previews",
                    "return_requests",
                    "return_confirmations",
                    "return_audit",
                    "local_receipts",
                    "local_confirmation_waits",
                    "local_submission_receipts",
                )
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()
            }

    completed = False
    try:
        start(True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1280, "height": 1000})
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin)
            page.screenshot(path=out / "initial-desktop.png", full_page=True)
            page.wait_for_function(
                "()=>document.querySelector('#send').disabled===false && "
                "document.querySelector('#connection').textContent==='进度已连接'"
            )
            context.storage_state(path=private / "browser-state.json")

            def state():
                return page.evaluate(
                    """async()=>{let b=await (await fetch('/api/bootstrap')).json();
                    return await(await fetch('/api/sessions/'+b.session_id)).json()}"""
                )

            def idle():
                page.wait_for_function(
                    "()=>document.querySelector('#send').disabled===false", timeout=180000
                )
                s = state()
                if s["jobs"][-1]["status"] != "completed":
                    raise RuntimeError("Execution incomplete; no model retry allowed")
                return s

            def send(text):
                before = len(state()["jobs"])
                page.locator("#message").fill(text)
                page.locator("#send").click()
                page.wait_for_function(
                    """async n=>{let b=await(await fetch('/api/bootstrap')).json();
                       let s=await(await fetch('/api/sessions/'+b.session_id)).json();
                       return s.jobs.length>n}""",
                    arg=before,
                    timeout=180000,
                )
                return idle()

            # Exact prior frozen inputs; browser uses visible helper buttons, not direct DB writes.
            page.locator("#examples").evaluate("(e)=>e.open=true")
            stages = json.loads(
                subprocess.check_output(
                    [
                        str(RUNTIME_PYTHON),
                        "-c",
                        'import sys,json;sys.path.insert(0,"scripts");'
                        "from local_formal_submission import STAGES;print(json.dumps(STAGES))",
                    ],
                    cwd=ROOT,
                    text=True,
                )
            )
            inputs = stages if not deepseek else [stages[i] for i in (0, 1, 2, 3, 4, 7)]
            if not deepseek:
                inputs = ["查询P003充电器接口", "查询我的订单", *inputs]
            previous = None
            old_preview = None
            first_query_answer = None
            for index, text in enumerate(inputs, 1):
                start_time = time.perf_counter()
                if text is None:
                    page.reload()
                    page.wait_for_function("()=>document.querySelector('#send').disabled===false")
                    current = state()
                    # Genuine service process restart, persisted Cookie/CSRF/session/Graph/SSE.
                    server.terminate()
                    server.wait(timeout=90)
                    start()
                    page.reload()
                    page.wait_for_function("()=>document.querySelector('#send').disabled===false")
                    s = state()
                    assert s["draft"] == current["draft"] and s["preview"] == current["preview"]
                else:
                    s = send(text)
                checks = {"no_formal_requests": not s["requests"]}
                if not deepseek and index in (1, 2):
                    answer = page.locator("#chat .bubble.assistant").last.inner_text()
                    checks["fixed_mode_disclosed"] = "固定脚本" in page.locator(
                        "#runtime-mode"
                    ).inner_text()
                    checks["answer_stream_limit_disclosed"] = "不是逐字流式" in page.locator(
                        "#runtime-notice"
                    ).inner_text()
                    if index == 1:
                        checks["product_facts_visible"] = "P003" in answer
                        first_query_answer = answer
                    else:
                        checks["owned_order_facts_visible"] = bool(s["orders"]) and all(
                            o["order_id"] in answer for o in s["orders"]
                        )
                        checks["query_answers_distinct"] = answer != first_query_answer
                        page.screenshot(path=out / "query-results-desktop.png", full_page=True)
                if text in stages:
                    number = stages.index(text) + 1
                    task, preview = s["draft"], s["preview"]
                    if number == 1:
                        checks["order_saved"] = task.get("order_id") == "O10024"
                    if number in (2, 8):
                        checks["earphone_quantity"] = (
                            task.get("item_id") == 4 and task.get("quantity") == 1
                        )
                    if number == 2:
                        checks["no_premature_preview"] = preview is None
                    if number in (3, 5, 6):
                        checks["consult_preserves_revision"] = (
                            previous is not None and task["revision"] == previous["revision"]
                        )
                    if number == 4:
                        checks["corrected_item"] = (
                            task.get("item_id") == 5 and task.get("quantity") == 1
                        )
                        checks["corrected_preview"] = bool(preview)
                        old_preview = preview["preview_id"] if preview else None
                    if number == 7:
                        checks["explicit_cancel"] = (
                            task.get("status") == "cancelled" and preview is None
                        )
                    if number == 8:
                        checks["new_latest_preview"] = bool(
                            preview and preview["preview_id"] != old_preview
                        )
                assert all(checks.values()), checks
                previous = s["draft"]
                records.append(
                    {
                        "stage": index,
                        "input": text,
                        "passed": all(checks.values()),
                        "checks": checks,
                        "draft": s["draft"],
                        "preview": s["preview"],
                        "latency_ms": (time.perf_counter() - start_time) * 1000,
                    }
                )
                (private / f"stage-{index}.sql.json").write_text(
                    json.dumps(sql(), ensure_ascii=False, indent=2)
                )
            assert s["preview"] and s["draft"]["item_id"] == 4 and s["draft"]["quantity"] == 1
            page.screenshot(path=out / "preview-desktop.png", full_page=True)
            # Disconnect subscriptions, then reconnect/refresh. Reads cannot create jobs/tools.
            before = sql()
            page.evaluate("()=>source.close()")
            context.set_offline(True)
            context.set_offline(False)
            page.reload()
            page.wait_for_function("()=>document.querySelector('#send').disabled===false")
            assert sql() == before
            page.locator("#open").click()
            page.wait_for_selector("#confirmation:not([hidden])", timeout=90000)
            idle()
            wait = state()["confirmation"]["wait_id"]
            page.locator("#accept").click()
            s = idle()
            assert len(s["requests"]) == 1 and s["requests"][0]["status"] == "pending_review"
            page.wait_for_function(
                "()=>document.querySelector('#requests').textContent.includes('pending_review')"
            )
            verified = sql()
            assert [
                len(verified[t])
                for t in (
                    "return_requests",
                    "return_confirmations",
                    "return_audit",
                    "local_submission_receipts",
                )
            ] == [1] * 4
            # Duplicate application click through same-origin HTTP, original wait binding.
            duplicate = page.evaluate(
                """async wid=>{let b=await(await fetch('/api/bootstrap')).json();
                return(await fetch('/api/sessions/'+b.session_id+'/decision',{
                method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':b.csrf},
                body:JSON.stringify({wait_id:wid,decision:'accept',request_key:crypto.randomUUID()})
                })).status}""",
                wait,
            )
            assert duplicate == 202
            page.wait_for_timeout(200)
            idle()
            assert sql() == verified
            page.reload()
            page.wait_for_function(
                "()=>document.querySelector('#requests').textContent.includes('pending_review')"
            )
            page.wait_for_function("()=>document.querySelectorAll('#trace li').length>0")
            page.screenshot(path=out / "submitted-desktop.png", full_page=True)
            page.set_viewport_size({"width": 375, "height": 900})
            page.screenshot(path=out / "submitted-mobile.png", full_page=True)
            for width in (320, 375, 1280):
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("()=>document.documentElement.scrollWidth<=window.innerWidth")
            assert not errors, errors
            (out / "sql_final.json").write_text(json.dumps(verified, ensure_ascii=False, indent=2))
            browser.close()
            completed = True
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            server.wait(timeout=90)
        log.close()
        final_fingerprints = {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
        }
        score = {
            "kind": "real-deepseek-development-smoke"
            if deepseek
            else "fixed-model-real-browser-contract",
            "source_sha": source,
            "source_fingerprints": fingerprints,
            "source_unchanged": fingerprints == final_fingerprints,
            "completed": completed,
            "stages": records,
            "browser_errors": errors,
            "paid_cap_cny": 3 if deepseek else 0,
            "paid_http_limit": 24 if deepseek else 0,
            "raw_evidence": ".private-evidence local only",
        }
        (out / "scorecard.json").write_text(json.dumps(score, ensure_ascii=False, indent=2) + "\n")
    assert completed and score["source_unchanged"]
    print(json.dumps({"completed": completed, "stages": len(records), "browser_errors": errors}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--deepseek", action="store_true")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--price-review", type=Path)
    args = parser.parse_args()
    if args.deepseek and not (args.env_file and args.price_review):
        parser.error("Paid smoke requires explicit env-file and verified price-review")
    run(
        args.out,
        args.port,
        deepseek=args.deepseek,
        env_file=args.env_file,
        price_review=args.price_review,
    )
