"""Browser checks for a separately started, FREE shop-realism-v1.2 fixture."""
import argparse
import json
import re
from pathlib import Path


def run(out, port):
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=False)
    checks, errors = {}, []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 980})
        page = context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/shop")
        page.wait_for_selector("#send:not([disabled])")
        # Refuse to send to a real-model workbench accidentally.
        notice = page.locator("#runtime-notice").text_content()
        assert "离线" in notice, "Browser regression requires an offline fixture"
        page.locator("#message").fill("店里有哪些商品")
        page.locator("#message").press("Enter")
        page.wait_for_function("()=>document.querySelectorAll('.product-card').length===64",
                               timeout=60000)
        page.wait_for_selector("#send:not([disabled])")
        checks["all64_product_cards"] = page.locator(".product-card").count() == 64
        checks["last_added_product"] = "JSQ27-MK1S" in page.locator("#chat").inner_text()
        checks["no_false_incomplete"] = "查询尚未完成" not in page.locator("#chat").inner_text()
        page.screenshot(path=str(out / "catalog-desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        checks["mobile_no_overflow"] = page.evaluate(
            "document.documentElement.scrollWidth<=window.innerWidth")
        page.screenshot(path=str(out / "catalog-mobile.png"))
        page.reload()
        page.wait_for_selector("#send:not([disabled])")
        checks["reload_retains64"] = page.locator(".product-card").count() == 64
        page.set_viewport_size({"width": 1440, "height": 980})
        page.locator("#nav-orders").click()
        page.wait_for_selector("#dialog[open]")
        checks["all38_owned_orders"] = "共 38 笔" in page.locator("#dialog-content").inner_text()
        page.get_by_role("searchbox", name="订单商品名称").fill("燃气")
        page.get_by_role("button", name="筛选", exact=True).click()
        page.wait_for_function("()=>[...document.querySelectorAll('#dialog .order-card')]"
                               ".every(n=>n.textContent.includes('燃气'))")
        page.locator("#dialog .order-card").first.get_by_role("button", name="查看详情").click()
        page.wait_for_function("()=>document.querySelector('#dialog-title')"
                               ".textContent.startsWith('订单详情')")
        disabled = page.locator("#dialog").get_by_role(
            "button", name=re.compile(r"^(暂无适用售后政策|仅咨询，暂不支持办理)$"))
        checks["appliance_submission_disabled"] = (
            disabled.count() > 0 and all(b.is_disabled() for b in disabled.all()))
        page.screenshot(path=str(out / "policy-boundary.png"))
        context.close()
        for oid, count, signed in [("O72001", 6, True), ("O72003", 3, False)]:
            context = browser.new_context(viewport={"width": 1440, "height": 980})
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/shop")
            page.wait_for_selector("#send:not([disabled])")
            page.locator("#message").fill(f"查询订单{oid}的物流")
            page.locator("#message").press("Enter")
            page.wait_for_selector(".timeline li", timeout=60000)
            page.wait_for_selector("#send:not([disabled])")
            timeline = page.locator(".timeline")
            checks[oid+"_nodes"] = timeline.locator("li").count() == count
            checks[oid+"_status"] = ("已签收" in timeline.inner_text()) == signed
            checks[oid+"_timezone"] = "+08:00" in timeline.inner_text()
            checks[oid+"_regional_route"] = "上海" in timeline.inner_text()
            page.screenshot(path=str(out / (oid+"-logistics.png")))
            context.close()
        browser.close()
    checks["no_javascript_errors"] = not errors
    report = dict(kind="real_chromium_mock_sdk", paid_requests=0,
                  checks=checks, errors=errors, passed=all(checks.values()))
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(report, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    run(args.out, args.port)
