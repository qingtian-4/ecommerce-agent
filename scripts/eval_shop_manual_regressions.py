"""Screenshot regressions through Chromium, native Graph and mocked SDK SSE."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def serve(root, port):
    import asyncio
    from contextlib import contextmanager

    import httpx
    import uvicorn

    from acs.config import Settings
    from acs.episode_budget import EpisodeLedger, EpisodeLimits
    from acs.episode_transport import EpisodeSyncTransport
    from acs.langgraph_poc import deepseek_model
    from acs.shop_demo_data import materialize
    from acs.shop_frontend import create_shop_app

    materialize(root)
    ledger = EpisodeLedger(root / "replay-fees.jsonl")
    ledger.begin("replay", EpisodeLimits(requests=12, episode_timeout_s=3600))

    def frame(delta=None, finish=None, usage=None):
        return b"data: " + json.dumps(dict(
            id="mock-manual", object="chat.completion.chunk", created=1,
            model="deepseek-flash", choices=[] if delta is None else [dict(
                index=0, delta=delta, finish_reason=finish)], usage=usage,
        )).encode() + b"\n\n"

    class Body(httpx.AsyncByteStream):
        def __init__(self, request):
            self.messages = json.loads(request.content)["messages"]

        async def __aiter__(self):
            i = max(i for i, m in enumerate(self.messages) if m["role"] == "user")
            text = self.messages[i]["content"]
            receipts = [m for m in self.messages[i+1:] if m["role"] == "tool"]
            if not receipts:
                query = "无匹配商品" if "无匹配" in text else "入耳"
                args = {} if "历史回放" in text else {"name_contains": query}
                await asyncio.sleep(.4)
                yield frame({"tool_calls": [dict(index=0, id=f"mock-{i}", type="function",
                    function=dict(name="list_orders", arguments=json.dumps(args)))]}, "tool_calls")
            else:
                result = json.loads(receipts[-1]["content"])["result"]
                orders = [o for o in result["orders"] if any(
                    "入耳" in item["product_name"] for item in o["items"])]
                answer = (f"入耳耳机订单共 {len(orders)} 笔，请选择一笔。\n\n"
                          + "\n".join(f"- {o['order_id']}：入耳耳机，状态 {o['status']}。"
                                      for o in orders)) if orders else "没有符合名称条件的订单。"
                for at in range(0, len(answer), 5):
                    yield frame({"content": answer[at:at+5]})
                    await asyncio.sleep(.025)
                yield frame({}, "stop")
            yield frame(usage={"prompt_tokens": 100, "completion_tokens": 30})
            yield b"data: [DONE]\n\n"

    @contextmanager
    def factory():
        with httpx.Client(transport=EpisodeSyncTransport(ledger, "replay", "agent",
                lambda: httpx.MockTransport(lambda request: httpx.Response(200,
                    headers={"content-type": "text/event-stream"}, stream=Body(request))),
                streaming=True)) as client:
            yield deepseek_model(Settings(_env_file=None, llm_api_key="offline-test",
                llm_model="deepseek-flash", llm_thinking=False, llm_max_output_tokens=1024),
                http_client=client).model_copy(update={"streaming": True})

    uvicorn.run(create_shop_app(root, factory, origin=f"http://127.0.0.1:{port}",
                               runtime_mode="fixed_script", answer_streaming=True),
                host="127.0.0.1", port=port, access_log=False, timeout_graceful_shutdown=3)


def run(out, port):
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=False)
    private = out / ".private-evidence"
    private.mkdir()
    root = private / "runtime"
    origin = f"http://127.0.0.1:{port}"
    checks, errors = {}, []
    with (private / "server.log").open("w") as log:
        server = subprocess.Popen([str(ROOT / ".venv/bin/python"), __file__, "--serve",
            "--out", str(root), "--port", str(port)], cwd=ROOT, stdout=log, stderr=log,
            env=dict(os.environ, ACS_LLM_API_KEY="", ACS_EMBEDDING_API_KEY=""))
        try:
            for _ in range(600):
                try:
                    urllib.request.urlopen(origin + "/shop", timeout=1).close()
                    break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError("Offline regression server failed") from None
                    time.sleep(.1)
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 980})
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.add_init_script("""window.observed=[];window.disconnects=0;
                  const ES=window.EventSource;
                  window.EventSource=function(...a){const s=new ES(...a);
                    s.addEventListener('error',()=>window.disconnects++);
                    for(const k of ['tool_requested','tool_completed','answer_delta'])
                      s.addEventListener(k,e=>window.observed.push(JSON.parse(e.data)));
                    return s;};""")
                page.goto(origin + "/shop")
                page.wait_for_selector("#send:not([disabled])")
                for turn, text in enumerate(["历史回放：我有买过入耳耳机的订单吗",
                                              "按名称筛选入耳耳机订单", "按无匹配商品查询"]):
                    page.locator("#message").fill(text)
                    page.locator("#message").press("Enter")
                    page.wait_for_selector(".assistant-message.draft .bot-icon")
                    page.evaluate("window.startBubble=document.querySelector('.draft');"
                                  "window.startIcon=window.startBubble.querySelector('.bot-icon')")
                    if turn < 2:
                        page.wait_for_function("()=>document.querySelector('.draft .message-text')"
                                               "?.textContent.includes('生成中')")
                        checks[f"same_avatar_during_stream_{turn}"] = page.evaluate(
                            "window.startBubble===document.querySelector('.draft') && "
                            "window.startIcon===document.querySelector('.draft .bot-icon')")
                        checks[f"white_bubble_{turn}"] = page.locator(
                            ".draft .message-body").evaluate(
                                "n=>getComputedStyle(n).backgroundColor==='rgb(255, 255, 255)'"
                            )
                        page.screenshot(path=str(out / f"stream-{turn}.png"))
                    page.wait_for_selector("#send:not([disabled])", timeout=60000)
                    checks[f"no_leftover_draft_{turn}"] = page.locator("#chat .draft").count() == 0
                    message = page.locator(".assistant-message:not(.draft)").last
                    if turn < 2:
                        checks[f"only_in_ear_cards_{turn}"] = all("入耳" in t for t in
                            message.locator(".order-card").all_text_contents())
                        message.get_by_role("button", name="查看本轮全部候选 ›").click()
                        checks[f"scoped_paging_{turn}"] = "15 笔" in page.locator(
                            "#dialog-content").inner_text()
                        page.get_by_role("button", name="下一页", exact=True).click()
                        checks[f"scoped_next_page_{turn}"] = all("入耳" in t for t in
                            page.locator("#dialog .order-card").all_text_contents())
                        page.locator("#close-dialog").click()
                    else:
                        checks["empty_not_all_orders"] = message.locator(".order-card").count() == 0
                observed = page.evaluate("window.observed")
                requested = [e["data"]["call_id"] for e in observed
                             if e["type"] == "tool_requested"]
                completed = [e["data"]["call_id"] for e in observed
                             if e["type"] == "tool_completed"]
                import sqlite3
                with sqlite3.connect(root / "business.sqlite") as conn:
                    actual = [r[0] for r in conn.execute("SELECT call_id FROM local_receipts")]
                checks["sse_matches_actual_receipts"] = (
                    set(requested) == set(completed) == set(actual))
                checks["no_stream_disconnects"] = page.evaluate("window.disconnects") == 0
                page.screenshot(path=str(out / "final-desktop.png"))
                page.set_viewport_size({"width": 390, "height": 844})
                checks["mobile_no_overflow"] = page.evaluate(
                    "document.documentElement.scrollWidth<=window.innerWidth")
                page.screenshot(path=str(out / "final-mobile.png"))
                page.reload()
                page.wait_for_selector("#send:not([disabled])")
                with sqlite3.connect(root / "business.sqlite") as conn:
                    after = [r[0] for r in conn.execute("SELECT call_id FROM local_receipts")]
                checks["reload_no_replay"] = actual == after and len(requested) == 3
                checks["no_javascript_errors"] = not errors
                browser.close()
            report = dict(kind="offline_sdk_native_graph_chromium", paid_requests=0,
                          checks=checks, errors=errors, passed=all(checks.values()))
            (out / "scorecard.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report), flush=True)
            if not report["passed"]:
                raise SystemExit(1)
        finally:
            server.terminate()
            server.wait(timeout=20)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8782)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if args.serve:
        serve(args.out, args.port)
    else:
        run(args.out.resolve(), args.port)
