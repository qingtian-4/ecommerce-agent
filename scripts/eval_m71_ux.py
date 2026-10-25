"""Frozen Chromium/HTTP/SQLite UX scenarios, no paid network."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORDER = "我要退O10024，先保存这个订单的退货草稿；顺便说明七天无理由退货的运费规则。"
DETAIL = (
    "退这单的Life P3耳机1件，无理由，未拆封且满足政策列出的全部二次销售条件。"
    "请保存草稿，先不要预览。"
)
CORRECT = (
    "回到退货，把同订单商品更正为磁吸充电宝；数量1件，无理由，未拆封且满足全部二次销售条件。"
    "请生成预览，不能正式申请。"
)
PREVIEW = (
    "重新退O10024的Life P3耳机1件，无理由，未拆封且满足政策列出的全部二次销售条件，"
    "请生成最新预览，不能正式申请。"
)
FROZEN = (
    "已生成最新预览（此前耳机版本的预览已失效）。\n\n"
    "**商品咨询**：充电器接口请以商品查询结果为准。\n\n"
    "预览有效期：12:52左右。\n\n"
    "|字段|说明|\n|---|---|\n|数量|1件|\n\n"
    '<img src=x onerror="window.unsafeMarkdown=true">'
)


def serve(root, port):
    from contextlib import contextmanager

    import uvicorn
    from v3_web_demo import FixedDemoModel

    from acs.local_submission import init_formal_fixture
    from acs.web_service import create_app

    class FrozenModel(FixedDemoModel):
        def _generate(self, messages, *args, **kwargs):
            with (root / "model-calls.jsonl").open("a") as log:
                log.write("model_request\n")
            reply = super()._generate(messages, *args, **kwargs)
            user = next(m for m in reversed(messages) if m.type == "human")
            if user.content == CORRECT and not reply.generations[0].message.tool_calls:
                reply.generations[0].message.content = FROZEN
            return reply

    @contextmanager
    def factory():
        yield FrozenModel()

    def fault(point):
        flag = root / "exit-review"
        if point == "before_answer_review" and flag.exists():
            flag.unlink()
            os._exit(72)

    init_formal_fixture(root) if not root.exists() else None
    uvicorn.run(
        create_app(root, factory, origin=f"http://127.0.0.1:{port}",
                   runtime_mode="fixed_script",fault=fault),
        host="127.0.0.1",
        port=port,
        access_log=False,
        timeout_graceful_shutdown=3,
    )


def run(out, port, baseline):
    from playwright.sync_api import sync_playwright

    os.umask(0o077)
    out.mkdir(parents=True, exist_ok=False)
    private = out / ".private-evidence"
    private.mkdir(mode=0o700)
    server_env = dict(os.environ)
    if baseline:
        snapshot = private / "baseline-src"
        shutil.copytree(
            ROOT / "src/acs", snapshot / "acs", ignore=shutil.ignore_patterns("__pycache__")
        )
        server_env["PYTHONPATH"] = str(snapshot.resolve())
    result = {
        "source_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "baseline": baseline,
        "paid_requests": 0,
        "cases": [],
        "fingerprints": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                *sorted((ROOT / "src/acs").glob("*.py")),
                *sorted((ROOT / "src/acs/web_assets").glob("*")),
                Path(__file__),
            ]
        },
    }
    server = None
    errors = []
    origin = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            for name in [
                "queries",
                "grounding",
                "unknown",
                "rejected_4xx",
                "lost_http",
                "confirmation",
                "sse_refresh",
                *([] if baseline else ["lost_after_commit", "review_exit", "database_unavailable"]),
            ]:
                runtime = private / name
                log = (private / (name + ".log")).open("w")
                server = subprocess.Popen(
                    [
                        str(ROOT / ".venv/bin/python"),
                        str(Path(__file__).resolve()),
                        "--serve",
                        str(runtime),
                        "--port",
                        str(port),
                    ],
                    cwd=ROOT,
                    stdout=log,
                    stderr=log,
                    env=server_env,
                )
                deadline = time.monotonic() + 60
                while True:
                    try:
                        urllib.request.urlopen(origin, timeout=1).close()
                        break
                    except OSError:
                        assert server.poll() is None and time.monotonic() < deadline
                        time.sleep(0.1)
                context = browser.new_context(
                    viewport={"width": 1280, "height": 1000}, timezone_id="Asia/Shanghai"
                )
                page = context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                posts = []
                page.on(
                    "request",
                    lambda r, posts=posts: (
                        posts.append(r.url.rsplit("/", 1)[-1]) if r.method == "POST" else None
                    ),
                )
                page.goto(origin)
                page.wait_for_function(
                    "()=>document.querySelector('#connection').textContent==='进度已连接'"
                )

                def snapshot(page=page):
                    return page.evaluate("""async()=>{
                        let b=await(await fetch('/api/bootstrap')).json();
                        return await(await fetch('/api/sessions/'+b.session_id)).json()}""")

                def send(text, page=page, snapshot=snapshot):
                    previous = len(snapshot()["jobs"])
                    page.locator("#message").fill(text)
                    page.locator("#send").click()
                    page.wait_for_function(
                        """async previous=>{const b=await(await fetch('/api/bootstrap')).json();
                        const s=await(await fetch('/api/sessions/'+b.session_id)).json();
                        return s.jobs.length>previous&&
                        !s.jobs.some(j=>['queued','running'].includes(j.status))}""",
                        arg=previous,
                        timeout=90000,
                    )
                    count = sum(r["role"] == "user" for r in snapshot()["history"])
                    page.wait_for_function(
                        "n=>document.querySelectorAll('#chat .bubble.user').length===n && "
                        "document.querySelectorAll('#chat .bubble.assistant').length===n",
                        arg=count,
                    )
                    page.wait_for_function("()=>!document.querySelector('#send').disabled")

                def sql(runtime=runtime):
                    with sqlite3.connect(runtime / "business.sqlite", timeout=20) as conn:
                        return {
                            t: conn.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
                            for t in ["return_requests", "return_confirmations", "return_audit"]
                        }

                checks = {}
                try:
                    if name == "queries":
                        send("查询P003充电器接口")
                        send("查询我的订单")
                        text = page.locator("#chat").inner_text()
                        checks["facts_visible"] = "P003" in text and "O10024" in text
                        checks["mode_disclosed"] = (
                            "固定脚本" in page.locator("#runtime-mode").inner_text()
                        )
                        checks["order_cards"] = page.locator("#orders article").count() > 0
                    elif name == "grounding":
                        send(ORDER)
                        send(DETAIL)
                        send(CORRECT)
                        content = page.locator("#chat .assistant").last.inner_text()
                        checks["unsupported_history_corrected"] = (
                            "此前耳机版本的预览已失效" not in content
                        )
                        checks["consultation_preserved"] = "充电器接口" in content
                        checks["markdown_rendered"] = (
                            page.locator("#chat .assistant strong").count() > 0
                        )
                        checks["markdown_table"] = (
                            page.locator("#chat .assistant table").count() > 0
                        )
                        checks["xss_safe"] = not page.evaluate("window.unsafeMarkdown===true")
                        checks["explicit_timezone"] = "UTC+08:00" in content
                    elif name == "unknown":
                        page.route(
                            "**/return-card",
                            lambda route: route.fulfill(
                                json={
                                    "state": "UNKNOWN",
                                    "status": "UNKNOWN",
                                    "reason": "submission_result_unknown",
                                    "allowed_actions": ["refresh"],
                                    "authoritative": False,
                                }
                            ),
                        )
                        page.locator("#refresh").click()
                        page.wait_for_function(
                            "()=>document.querySelector('#card-state').textContent==='结果未知'"
                        )
                        page.locator(".orders summary").click()
                        checks["readonly_query_available"] = (
                            page.locator("#orders article").count() > 0
                        )
                        send("查询P003充电器接口")
                        checks["readonly_query_completed"] = (
                            "P003" in page.locator("#chat").inner_text()
                        )
                        checks["chat_not_globally_locked"] = page.locator("#send").is_enabled()
                        checks["refresh_enabled"] = page.locator("#refresh").is_enabled()
                        checks["submit_hidden"] = page.locator("#accept").is_hidden()
                    elif name in ["rejected_4xx", "lost_http", "confirmation", "lost_after_commit"]:
                        send(PREVIEW)
                        page.locator("#open").click()
                        page.locator("#accept").wait_for(state="visible")
                        page.wait_for_function("()=>!document.querySelector('#accept').disabled")
                        if name == "rejected_4xx":
                            page.route(
                                "**/decision",
                                lambda route: route.fulfill(
                                    status=403, json={"detail": "CSRF拒绝：没有执行提交"}
                                ),
                            )
                        elif name == "lost_after_commit":

                            def lose(route, request, sql=sql):
                                response = route.fetch()
                                assert response.status == 202
                                deadline = time.monotonic() + 45
                                while sql()["return_requests"] != 1:
                                    assert time.monotonic() < deadline
                                    time.sleep(0.03)
                                route.abort("failed")

                            page.route("**/decision", lose)
                        elif name == "lost_http":
                            page.route("**/decision", lambda route: route.abort("failed"))
                        page.locator("#accept").click()
                        if name in {"confirmation", "lost_after_commit"}:
                            page.wait_for_function(
                                "()=>document.querySelector('#card-state').textContent==='待审核'",
                                timeout=90000,
                            )
                            checks["one_sql_request"] = all(v == 1 for v in sql().values())
                            checks["one_confirmation_post"] = posts.count("decision") == 1
                        elif name == "rejected_4xx":
                            page.wait_for_function(
                                "()=>document.querySelector('#notice').hidden===false"
                            )
                            checks["known_rejection_not_unknown"] = (
                                page.locator("#card-state").inner_text() != "结果未知"
                            )
                            checks["no_request"] = all(v == 0 for v in sql().values())
                        else:
                            page.wait_for_function(
                                "()=>document.querySelector('#card-state').textContent==='结果未知'"
                            )
                            page.reload()
                            page.wait_for_function(
                                "()=>document.querySelector('#connection').textContent==='进度已连接'"
                            )
                            checks["unknown_retained"] = (
                                page.locator("#card-state").inner_text() == "结果未知"
                            )
                            checks["no_automatic_retry"] = posts.count("decision") == 1
                            checks["no_request"] = all(v == 0 for v in sql().values())
                    elif name == "review_exit":
                        send(ORDER)
                        send(DETAIL)
                        (runtime / "exit-review").touch()
                        page.locator("#message").fill(CORRECT)
                        page.locator("#send").click()
                        deadline = time.monotonic() + 45
                        while server.poll() is None:
                            assert time.monotonic() < deadline
                            time.sleep(0.03)
                        assert server.returncode == 72
                        calls = (runtime / "model-calls.jsonl").read_text().count("model_request")
                        server = subprocess.Popen(
                            [
                                str(ROOT / ".venv/bin/python"),
                                str(Path(__file__).resolve()),
                                "--serve",
                                str(runtime),
                                "--port",
                                str(port),
                            ],
                            cwd=ROOT,
                            stdout=log,
                            stderr=log,
                            env=server_env,
                        )
                        deadline = time.monotonic() + 45
                        while True:
                            try:
                                urllib.request.urlopen(origin, timeout=1).close()
                                break
                            except OSError:
                                assert time.monotonic() < deadline
                                time.sleep(0.1)
                        page.reload()
                        page.wait_for_function(
                            "()=>document.querySelector('#connection').textContent==='进度已连接'"
                        )
                        text = page.locator("#chat .assistant").last.inner_text()
                        checks["unreviewed_success_hidden"] = "此前耳机版本的预览已失效" not in text
                        checks["pending_review_visible"] = "尚未完成核验" in text
                        page.locator("#resume").click()
                        page.wait_for_function("()=>!document.querySelector('#send').disabled")
                        page.wait_for_function(
                            "()=>document.querySelector('#chat .assistant:last-child')"
                            ".textContent.includes('UTC+08:00')"
                        )
                        checks["resume_no_model_request"] = (
                            runtime / "model-calls.jsonl"
                        ).read_text().count("model_request") == calls
                        checks["resume_corrected"] = (
                            "此前耳机版本的预览已失效" not in page.locator("#chat").inner_text()
                        )
                    elif name == "database_unavailable":
                        held = runtime / "held-business.sqlite"
                        (runtime / "business.sqlite").rename(held)
                        page.locator("#refresh").click()
                        page.wait_for_function(
                            "()=>document.querySelector('#card-state').textContent==='结果未知'"
                        )
                        checks["no_empty_database"] = not (runtime / "business.sqlite").exists()
                        checks["writes_disabled_without_database"] = page.locator(
                            "#accept"
                        ).is_hidden()
                        held.rename(runtime / "business.sqlite")
                        page.locator("#refresh").click()
                        page.wait_for_function("()=>!document.querySelector('#send').disabled")
                    else:
                        send("查询P003充电器接口")
                        before = len(snapshot()["jobs"])
                        page.evaluate("source.close()")
                        page.reload()
                        page.wait_for_function(
                            "()=>document.querySelector('#connection').textContent==='进度已连接'"
                        )
                        checks["no_reexecution"] = len(snapshot()["jobs"]) == before
                        checks["history_restored"] = "P003" in page.locator("#chat").inner_text()
                    checks["no_unexpected_sql"] = (
                        all(v == 0 for v in sql().values())
                        if name not in {"confirmation", "lost_after_commit"}
                        else all(v == 1 for v in sql().values())
                    )
                    page.screenshot(path=out / (name + "-desktop.png"), full_page=True)
                    page.set_viewport_size({"width": 375, "height": 900})
                    checks["mobile_no_overflow"] = page.evaluate(
                        "()=>document.documentElement.scrollWidth<=innerWidth"
                    )
                    page.screenshot(path=out / (name + "-mobile.png"), full_page=True)
                    (private / (name + "-snapshot.json")).write_text(
                        json.dumps(snapshot(), ensure_ascii=False)
                    )
                except Exception as exc:
                    checks["completed"] = False
                    (private / (name + "-error.txt")).write_text(str(exc))
                result["cases"].append(
                    {"name": name, "checks": checks, "sql": sql(), "posts": posts}
                )
                context.close()
                server.terminate()
                server.wait(timeout=60)
                server = None
                log.close()
            browser.close()
    finally:
        if server and server.poll() is None:
            server.terminate()
            server.wait(timeout=60)
        result["browser_errors"] = errors
        result["passed"] = all(all(c["checks"].values()) for c in result["cases"]) and not errors
        (out / "scorecard.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"] and not baseline:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--port", type=int, default=8781)
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    if args.serve:
        serve(args.serve, args.port)
    else:
        run(args.out, args.port, args.baseline)
