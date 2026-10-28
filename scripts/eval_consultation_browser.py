"""Check the consultation-only UI against a separate free fixture."""
import argparse
import json
import sqlite3
from pathlib import Path


def run(out, root, port):
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=False)
    checks, errors = {}, []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 980})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/shop")
        page.wait_for_selector("#send:not([disabled])")
        assert "离线" in page.locator("#runtime-notice").text_content()
        page.locator("#nav-orders").click()
        page.get_by_role("searchbox", name="订单商品名称").fill("燃气")
        page.get_by_role("button", name="筛选", exact=True).click()
        page.wait_for_function("()=>[...document.querySelectorAll('#dialog .order-card')]"
                               ".every(n=>n.textContent.includes('燃气'))")
        page.locator("#dialog .order-card").first.get_by_role("button", name="查看详情").click()
        page.wait_for_function("()=>document.querySelector('#dialog-title')"
                               ".textContent.startsWith('订单详情')")
        buttons = page.locator("#dialog").get_by_role("button", name="仅咨询，暂不支持办理")
        checks["appliance_operations_disabled"] = buttons.count() > 0 and all(
            b.is_disabled() for b in buttons.all())
        checks["no_false_missing_policy_label"] = "暂无适用售后政策" not in page.locator(
            "#dialog-content").inner_text()
        with sqlite3.connect(root / "business.sqlite") as db:
            before = db.execute("SELECT COUNT(*) FROM local_receipts").fetchone()[0]
        page.get_by_role("button", name="咨询模拟政策", exact=True).click()
        expected = ["只咨询模拟政策", "订单O", "不要修改退货草稿"]
        checks["prefill_binds_entity"] = all(t in page.locator("#message").input_value()
                                            for t in expected)
        with sqlite3.connect(root / "business.sqlite") as db:
            checks["prefill_sends_no_model_request"] = before == db.execute(
                "SELECT COUNT(*) FROM local_receipts").fetchone()[0]
        page.locator("#message").press("Enter")
        page.wait_for_function("()=>document.querySelector('#chat').textContent.includes('12个月')",
                               timeout=60000)
        page.wait_for_selector("#send:not([disabled])")
        text = page.locator("#chat").inner_text()
        checks["periods_and_evidence_visible"] = all(t in text for t in
            ["7天", "15天", "12个月", "签收日", "购买凭证"])
        checks["simulated_and_no_operations_visible"] = all(t in text for t in
            ["不代表", "不支持家电", "不能自动判定资格"])
        page.screenshot(path=str(out / "consultation-desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        checks["mobile_no_overflow"] = page.evaluate(
            "document.documentElement.scrollWidth<=window.innerWidth")
        page.screenshot(path=str(out / "consultation-mobile.png"))
        page.reload()
        page.wait_for_selector("#send:not([disabled])")
        checks["reload_keeps_consultation"] = "12个月" in page.locator("#chat").inner_text()
        with sqlite3.connect(root / "business.sqlite") as db:
            receipts = db.execute(
                "SELECT name,arguments FROM local_receipts ORDER BY rowid").fetchall()
            checks["real_tool_was_scoped_search"] = (len(receipts) == before+1
                and receipts[-1][0] == "search_knowledge"
                and json.loads(receipts[-1][1])["document_id"] == "appliance_consultation")
            checks["no_draft_preview_confirmation_or_application"] = all(db.execute(
                "SELECT COUNT(*) FROM " + t).fetchone()[0] == 0 for t in
                ["return_tasks", "return_previews", "return_confirmations", "return_requests"])
        checks["no_javascript_errors"] = not errors
        browser.close()
    report = dict(kind="real_chromium_fixed_script_not_model_quality", paid_requests=0,
                  checks=checks, errors=errors, passed=all(checks.values()))
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(report, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8792)
    args = parser.parse_args()
    run(args.out, args.root, args.port)
