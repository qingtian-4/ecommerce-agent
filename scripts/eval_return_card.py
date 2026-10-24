"""Fixed-model Chromium/HTTP/SQLite faults; never calls a paid model.

Run with a Playwright environment; the child server uses this repo's .venv.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT = (
    "重新退O10024的Life P3耳机1件，无理由，未拆封且满足政策列出的全部二次销售条件，"
    "请生成最新预览，不能正式申请。"
)
CORRECTION = (
    "回到退货，把同订单商品更正为磁吸充电宝；数量1件，无理由，未拆封且满足全部二次销售条件。"
    "请生成预览，不能正式申请。"
)
TABLES = ("return_requests", "return_confirmations", "return_audit", "local_submission_receipts")


def sql(root, statement, parameters=()):
    with sqlite3.connect(root / "business.sqlite") as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(statement, parameters)]


def counts(root):
    return {t: sql(root, f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] for t in TABLES}


def settle(root):
    deadline = time.monotonic() + 45
    while True:
        with sqlite3.connect((root / "web.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
            active = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
        if not active:
            return
        assert time.monotonic() < deadline, "job did not settle"
        time.sleep(0.03)


def serve(root, port, baseline_ui):
    import uvicorn
    from v3_web_demo import offline_factory

    import acs.web_service as web
    from acs.local_submission import init_formal_fixture

    if not root.exists():
        init_formal_fixture(root)
    if baseline_ui:
        assets = root / "baseline-assets"
        assets.mkdir(exist_ok=True)
        for name in ("index.html", "app.js", "style.css"):
            (assets / name).write_bytes(
                subprocess.check_output(
                    [
                        "git",
                        "show",
                        "7e6f1f8:src/acs/web_assets/" + name,
                    ],
                    cwd=ROOT,
                )
            )
        web.ASSETS = assets

    def fault(point):
        spec = root / "fault.json"
        if spec.exists() and json.loads(spec.read_text()).get("point") == point:
            raise ConnectionError("isolated browser fault injection")

    uvicorn.run(
        web.create_app(root, offline_factory, origin=f"http://127.0.0.1:{port}", fault=fault),
        host="127.0.0.1",
        port=port,
        timeout_graceful_shutdown=5,
    )


def run(out, port, baseline_ui):
    from playwright.sync_api import sync_playwright

    os.umask(0o077)
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    private = out / ".private-evidence"
    private.mkdir(mode=0o700)
    files = [
        *sorted((ROOT / "src/acs").glob("*.py")),
        *sorted((ROOT / "src/acs/web_assets").glob("*")),
        Path(__file__),
    ]
    fingerprints = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
    }
    result = {
        "source_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "fingerprints": fingerprints,
        "baseline_ui": baseline_ui,
        "kind": "fixed_model_browser_fault_contract",
        "paid_requests": 0,
        "cases": [],
        "completed": False,
    }
    case_names = (
        ["drop_before_dispatch"]
        if baseline_ui
        else [
            "normal",
            "reject",
            "cancel",
            "expired",
            "correction",
            "drop_before_dispatch",
            "lost_after_commit",
            "uncertain_before_commit",
            "database_unavailable",
            "csrf_and_cross_session",
        ]
    )
    server = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            for name in case_names:
                runtime = private / name
                origin = f"http://127.0.0.1:{port}"
                log = (private / (name + ".server.log")).open("w")

                def start(runtime=runtime, log=log, origin=origin):
                    nonlocal server
                    command = [
                        str(ROOT / ".venv/bin/python"),
                        str(Path(__file__).resolve()),
                        "--serve",
                        str(runtime),
                        "--port",
                        str(port),
                    ]
                    if baseline_ui:
                        command += ["--baseline-ui"]
                    server = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log)
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        try:
                            urllib.request.urlopen(origin, timeout=1).close()
                            return
                        except OSError:
                            if server.poll() is not None:
                                raise RuntimeError("server exited; see local private log") from None
                            time.sleep(0.1)
                    raise TimeoutError("server startup")

                def stop():
                    if server and server.poll() is None:
                        server.terminate()
                        server.wait(timeout=60)

                start()
                context = browser.new_context(viewport={"width": 1280, "height": 1100})
                page = context.new_page()
                errors, posts = [], []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                page.on(
                    "request",
                    lambda r, posts=posts: (
                        posts.append(r.url.rsplit("/", 1)[-1]) if r.method == "POST" else None
                    ),
                )

                def state(expected, page=page):
                    page.wait_for_function(
                        "s=>document.querySelector('#card-state').dataset.state===s",
                        arg=expected,
                        timeout=8000 if baseline_ui else 90000,
                    )

                def refresh(page=page):
                    page.locator("#refresh").click()

                def send(text, page=page):
                    page.locator("#message").fill(text)
                    page.locator("#send").click()
                    page.wait_for_function(
                        "()=>!document.querySelector('#send').disabled", timeout=90000
                    )

                def capture(state_name, page=page):
                    page.locator("#return-card").screenshot(path=out / (state_name + ".png"))

                try:
                    page.goto(origin)
                    page.wait_for_function(
                        "()=>document.querySelector('#connection').textContent==='进度已连接'"
                    )
                    send(PROMPT)
                    state("READY")
                    page.locator("#open").click()
                    page.locator("#accept").wait_for(state="visible")
                    page.wait_for_function("()=>!document.querySelector('#accept').disabled")
                    assert all(n == 0 for n in counts(runtime).values())
                    if name == "normal":
                        capture("READY")
                        page.locator("#accept").evaluate("e=>{e.click();e.click()}")
                        state("PENDING_REVIEW")
                        assert all(n == 1 for n in counts(runtime).values())
                        assert posts.count("decision") == 1
                        capture("PENDING_REVIEW")
                        for width in (375, 320):
                            page.set_viewport_size({"width": width, "height": 1000})
                            assert page.evaluate(
                                "()=>document.documentElement.scrollWidth<=innerWidth"
                            )
                        page.screenshot(path=out / "PENDING_REVIEW_mobile.png", full_page=True)
                        page.set_viewport_size({"width": 1280, "height": 1100})
                        settle(runtime)
                        (runtime / "graph.sqlite").write_bytes(b"broken graph mirror")
                        refresh()
                        state("PENDING_REVIEW")
                        page.reload()
                        state("PENDING_REVIEW")
                        assert posts.count("decision") == 1
                        stop()
                        start()
                        page.reload()
                        state("PENDING_REVIEW")
                        assert posts.count("decision") == 1
                    elif name in {"reject", "cancel"}:
                        page.locator("#" + name).click()
                        state("REJECTED")
                        capture("REJECTED")
                        page.reload()
                        state("REJECTED")
                    elif name in {"expired", "correction"}:
                        if name == "expired":
                            sql(
                                runtime,
                                "UPDATE return_previews SET expires_at=?",
                                ("2000-01-01T00:00:00+00:00",),
                            )
                            refresh()
                        else:
                            send(CORRECTION)
                        state("EXPIRED")
                        assert page.locator("#accept").is_hidden()
                        capture("EXPIRED")
                        page.locator("#cancel").click()
                        state("REJECTED" if name == "expired" else "READY")
                        if name == "expired":
                            send(PROMPT)
                            state("READY")
                    elif name in {"drop_before_dispatch", "lost_after_commit"}:
                        # Close only the subscription; the application task remains independent.
                        page.evaluate("()=>source.close()")

                        def lose_response(route, *, name=name, runtime=runtime):
                            if name == "lost_after_commit":
                                response = route.fetch()
                                assert response.status == 202
                                deadline = time.monotonic() + 45
                                while counts(runtime)["return_requests"] != 1:
                                    assert time.monotonic() < deadline
                                    time.sleep(0.02)
                            route.abort("failed")

                        page.route("**/decision", lose_response)
                        page.locator("#accept").click()
                        state("UNKNOWN")
                        capture("UNKNOWN")
                        assert page.locator("#send").is_disabled()
                        assert page.locator("#accept").is_hidden()
                        assert posts.count("decision") == 1
                        if name == "lost_after_commit":
                            settle(runtime)
                            (runtime / "graph.sqlite").write_bytes(b"broken graph mirror")
                            refresh()
                            state("PENDING_REVIEW")
                            assert all(n == 1 for n in counts(runtime).values())
                        else:
                            stop()
                            start()
                            page.reload()
                            state("UNKNOWN")
                            refresh()
                            state("UNKNOWN")
                            assert posts.count("decision") == 1
                    elif name == "uncertain_before_commit":
                        (runtime / "fault.json").write_text(
                            json.dumps({"point": "before_consume_commit"})
                        )
                        page.locator("#accept").click()
                        state("UNKNOWN")
                        page.reload()
                        state("UNKNOWN")
                        refresh()
                        state("UNKNOWN")
                        assert posts.count("decision") == 1
                    elif name == "database_unavailable":
                        db, held = runtime / "business.sqlite", runtime / "business-held.sqlite"
                        db.rename(held)
                        try:
                            refresh()
                            state("UNKNOWN")
                            assert not db.exists()
                            assert page.locator("#send").is_disabled()
                        finally:
                            held.rename(db)
                        refresh()
                        state("READY")
                    elif name == "csrf_and_cross_session":
                        denied = page.evaluate("""async()=>{
                            const b=await(await fetch('/api/bootstrap')).json();
                            const sid=b.session_id;
                            const c=await(await fetch('/return-card')).json();
                            const status=await fetch('/api/sessions/'+sid+'/decision',{
                              method:'POST',headers:{'Content-Type':'application/json',
                                'X-CSRF-Token':'forged'},body:JSON.stringify({
                                request_key:crypto.randomUUID(),wait_id:c.confirmation.wait_id,
                                decision:'accept'})});
                            return {sid,status:status.status};
                        }""")
                        assert denied["status"] == 403
                        other = browser.new_context()
                        try:
                            other_page = other.new_page()
                            other_page.goto(origin)
                            other_page.wait_for_function(
                                "()=>document.querySelector('#connection').textContent==='进度已连接'"
                            )
                            assert (
                                other_page.evaluate(
                                    "async sid=>(await fetch('/api/sessions/'+sid+"
                                    "'/return-card')).status",
                                    denied["sid"],
                                )
                                == 403
                            )
                        finally:
                            other.close()
                    expected = 1 if name in {"normal", "lost_after_commit"} else 0
                    settle(runtime)
                    final = counts(runtime)
                    assert all(n == expected for n in final.values()), final
                    assert not errors, errors
                    result["cases"].append(
                        {
                            "name": name,
                            "passed": True,
                            "formal_sql_counts": final,
                            "decision_http_attempts": posts.count("decision"),
                            "page_errors": errors,
                            "waits": sql(
                                runtime,
                                "SELECT wait_id,preview_id,status,consumptions,decision "
                                "FROM local_confirmation_waits ORDER BY rowid",
                            ),
                            "requests": sql(
                                runtime, "SELECT request_id,preview_id,status FROM return_requests"
                            ),
                        }
                    )
                finally:
                    context.close()
                    stop()
                    log.close()
            browser.close()
        result["completed"] = True
    except Exception as exc:
        result["failure"] = {"case": name, "type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        if server and server.poll() is None:
            server.terminate()
            server.wait(timeout=60)
        result["source_unchanged"] = all(
            hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h
            for p, h in fingerprints.items()
        )
        (out / "scorecard.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--serve", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--baseline-ui", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        serve(args.serve.resolve(), args.port, args.baseline_ui)
    else:
        run(args.out.resolve(), args.port, args.baseline_ui)
