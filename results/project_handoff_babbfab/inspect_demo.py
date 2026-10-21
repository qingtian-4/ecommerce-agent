"""Read synthetic acceptance receipts and capture the diagnostic trace in the real UI."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = Path("/tmp/acs-handoff-demo-20261021/candidate-v2/business.db")


def receipts():
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        turns = [
            dict(r)
            for r in conn.execute(
                "SELECT sequence,user_text,answer,status,result_json FROM conversation_turns "
                "ORDER BY created_at"
            )
        ]
        for row in turns:
            row["result"] = json.loads(row.pop("result_json") or "{}")
        requests = [
            dict(r)
            for r in conn.execute(
                "SELECT request_id,order_id,item_id,quantity,status FROM return_requests "
                "WHERE order_id='S20002'"
            )
        ]
        reviews = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM return_reviews WHERE request_id IN "
                "(SELECT request_id FROM return_requests WHERE order_id='S20002')"
            )
        ]
        tasks = [json.loads(r[0]) for r in conn.execute("SELECT data_json FROM return_tasks")]
    assert len(requests) == 1 and requests[0]["status"] == "approved"
    assert len(reviews) == 1
    calls = {
        e["label"] for t in turns for e in t["result"].get("trace", []) if e["type"] == "tool_call"
    }
    assert {
        "get_product",
        "get_knowledge_documents",
        "get_order",
        "update_return_task",
        "cancel_return_task",
    } <= calls
    first = next(t for t in turns if t["user_text"] == "我想退S20017。")
    assert any(
        e["type"] == "state_check" and e["detail"]["status"] == "repaired"
        for e in first["result"]["trace"]
    )
    negative = next(t for t in turns if "不是要取消" in t["user_text"])
    assert any(
        e["type"] == "error" and e["label"] == "cancel_return_task"
        for e in negative["result"]["trace"]
    )
    assert all(t["status"] == "completed" for t in turns)
    result = {
        "model": "scripted, deliberately wrong; not real LLM quality",
        "paid_calls": 0,
        "requests": requests,
        "reviews": reviews,
        "tasks": tasks,
        "tool_names": sorted(calls),
        "turns": turns,
        "p0_order_only_repaired": True,
        "negative_cancel_tool_rejected": True,
        "single_approved_request": True,
        "all_turns_completed": True,
    }
    (ROOT / "backend_receipts_v2.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )


def capture():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as manager:
        browser = manager.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1400})
        page.goto("http://127.0.0.1:7910", wait_until="domcontentloaded")
        page.locator("#message-input textarea").wait_for()
        page.get_by_label("显示开发观察", exact=True).check()
        page.locator("#execution-trace").wait_for(state="visible")
        page.locator("#message-input textarea").fill(
            "我的订单一共买了多少件商品？只给总件数，不要列表。"
        )
        page.locator("#send-message").click()
        page.wait_for_function(
            "document.querySelector('#customer-chat').innerText.includes('29 件商品')"
        )
        page.locator("#execution-trace").wait_for(state="visible")
        panel = page.locator("#execution-trace")
        text = panel.inner_text()
        assert "25件" in text and "输出核验（原答仅供诊断）" in text
        assert "quantity" in text and "order_aggregation" in text
        panel.locator(".output-gate").scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT / "trace_diagnostic.png"), full_page=True)
        (ROOT / "trace_diagnostic.txt").write_text(text)
        browser.close()


if __name__ == "__main__":
    receipts()
    capture()
