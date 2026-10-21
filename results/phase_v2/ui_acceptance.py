"""V2 desktop/mobile progress, selection and feedback; local scripted server only."""

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).parent / "ui_layout_final"
OUT.mkdir(exist_ok=False)
with sync_playwright() as manager:
    browser = manager.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1100})
    page.goto("http://127.0.0.1:7930", wait_until="domcontentloaded")
    page.locator("#message-input textarea").wait_for()
    page.locator("#return-task").wait_for()
    page.locator("#message-input textarea").fill("重试演示")
    page.locator("#send-message").click()
    page.wait_for_function("document.querySelector('#reply-status').innerText.includes('正在重试')")
    page.wait_for_function(
        "document.querySelector('#reply-status').innerText.includes('本轮回复已完成')"
    )
    progress = page.locator(".execution-progress").inner_text()
    assert "private-sentinel" not in progress and "tool_calls" not in progress
    page.get_by_role("button", name="有帮助", exact=True).click()
    page.wait_for_function("document.body.innerText.includes('反馈已记录')")
    page.screenshot(path=str(OUT / "retry_feedback.png"), full_page=True)
    page.get_by_text("手动选择退货对象（本人完整列表，非推荐）", exact=True).click()
    page.get_by_label("订单及商品明细").click()
    page.get_by_role("option").filter(has_text="S20017").first.click()
    page.get_by_role("button", name="明确选择此退货对象", exact=True).click()
    page.wait_for_function("document.querySelector('#return-task').innerText.includes('S20017')")
    assert "尚未提交" in page.locator("#return-task").inner_text()
    page.screenshot(path=str(OUT / "selection.png"), full_page=True)
    page.reload(wait_until="domcontentloaded")
    page.locator("#conversation-history input").first.wait_for()
    page.locator("#conversation-history input").first.check()
    page.wait_for_function("document.querySelector('#return-task')?.innerText.includes('S20017')")
    page.wait_for_function("document.querySelector('.execution-progress') !== null")
    page.screenshot(path=str(OUT / "restored.png"), full_page=True)
    mobile = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
    mobile.goto("http://127.0.0.1:7930", wait_until="domcontentloaded")
    mobile.locator("#message-input textarea").wait_for()
    mobile.locator("#return-task").wait_for()
    mobile.locator("#message-input textarea").fill("查询商品V201的规格。")
    mobile.locator("#send-message").click()
    mobile.wait_for_function(
        "document.querySelector('#customer-chat').innerText.includes('商品V201的规格请以本轮商品工具记录为准')"
    )
    mobile.wait_for_function(
        "document.querySelector('#reply-status').innerText.includes('本轮回复已完成')"
    )
    mobile.screenshot(path=str(OUT / "mobile.png"), full_page=True)
    boxes = mobile.evaluate(
        """() => {let a=document.querySelector('#reply-status').getBoundingClientRect();
        let b=document.querySelector('#customer-chat').getBoundingClientRect();
        return {progressBottom:a.bottom,chatTop:b.top}}"""
    )
    assert boxes["chatTop"] >= boxes["progressBottom"] - 2, boxes
    overflow = mobile.evaluate("document.documentElement.scrollWidth > window.innerWidth + 2")
    assert not overflow
    browser.close()
(OUT / "summary.json").write_text(
    json.dumps(
        {
            "retry_visible": True,
            "feedback_saved": True,
            "explicit_candidate_selection": True,
            "page_restored": True,
            "mobile_width": 390,
            "mobile_horizontal_overflow": False,
            "paid_calls": 0,
            "scripted_model_not_real_quality": True,
        },
        indent=2,
    )
    + "\n"
)
