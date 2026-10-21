"""Actual Gradio with injected optional-diagnostic failure; local scripted model."""
import json
import sqlite3
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

OUT = Path(__file__).parent / 'ui_diagnostics'
OUT.mkdir(exist_ok=False)
rows = []
with sync_playwright() as manager:
    browser = manager.chromium.launch(headless=True)
    for name, port in [('projection', 7940), ('checkpoint', 7941)]:
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.goto(f'http://127.0.0.1:{port}', wait_until='domcontentloaded')
        page.locator('#return-task').wait_for()
        expect(page.locator('#send-message')).to_be_enabled()
        page.locator('#message-input textarea').fill('我想退S20002。')
        page.locator('#send-message').click()
        page.wait_for_function("document.querySelector('#customer-chat').innerText.includes('退货草稿已保存')")
        expect(page.locator('#send-message')).to_be_enabled()
        assert 'S20002' in page.locator('#return-task').inner_text()
        assert 'injected diagnostic' not in page.locator('#customer-chat').inner_text()
        page.screenshot(path=str(OUT / f'{name}.png'), full_page=True)
        page.reload(wait_until='domcontentloaded')
        page.locator('#conversation-history input').first.wait_for()
        page.locator('#conversation-history input').first.check()
        page.wait_for_function("document.querySelector('#return-task')?.innerText.includes('S20002')")
        page.wait_for_function("document.querySelector('#customer-chat').innerText.includes('退货草稿已保存')")
        page.screenshot(path=str(OUT / f'{name}_restored.png'), full_page=True)
        path = Path(f'/tmp/acs-premerge-{name}-20261022/business.db')
        with sqlite3.connect(path) as conn:
            answer, payload, status = conn.execute(
                'SELECT answer,result_json,status FROM conversation_turns '
                'ORDER BY rowid DESC LIMIT 1'
            ).fetchone()
            result = json.loads(payload)
            assert answer == result['answer'] and status != 'running'
            assert result['stop_reason'] == 'answered'
            assert result['totals']['diagnostic_errors']
        rows.append({'fault': name, 'final_history_synchronized': True,
                     'draft_restored': True, 'core_completed': True,
                     'diagnostic_errors': result['totals']['diagnostic_errors']})
        page.close()
    browser.close()
(OUT / 'summary.json').write_text(json.dumps({'rows': rows, 'paid_calls': 0,
    'model': 'scripted', 'page': 'actual Gradio/ChatService/SQLite'}, indent=2)+'\n')
