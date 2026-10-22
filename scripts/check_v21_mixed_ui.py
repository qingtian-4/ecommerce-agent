"""Real Gradio acceptance with a labelled scripted model; no paid API path."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]


def serve(scenario,port,*,workflow=False,candidate=True,admin=False):
    if workflow:
        from scripts.check_delivery_ui import serve as existing_serve

        import acs.config
        class AcceptanceSettings(acs.config.Settings):
            write_receipt_guard_enabled: bool=candidate
        acs.config.Settings=AcceptanceSettings
        existing_serve(scenario,port,candidate,admin)
        return
    import app
    from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall

    from acs.config import Settings
    from acs.cs_tools import ToolRegistry
    from acs.scenarios import build_scenario, scenario_settings
    from acs.service_agent import CustomerServiceAgent
    from acs.sessions import SessionStore

    if not scenario.exists():
        build_scenario(scenario)
    settings=scenario_settings(Settings(_env_file=None,llm_api_key=''),scenario).model_copy(
        update={'write_receipt_guard_enabled':True,'workflow_router_enabled':True,
                'runtime_recovery_enabled':True,'execution_progress_enabled':True})
    class Model(ScriptedLLM):
        def __init__(self):
            super().__init__([ScriptedResponse(wants_tools=True,tool_calls=[
                ScriptedToolCall('o','get_order','{"order_id":"S20017"}'),
                ScriptedToolCall('p','get_product','{"product_id":"V201"}'),
                ScriptedToolCall('k','get_knowledge_documents',
                                 '{"document_ids":["return_policy"]}'),
                ScriptedToolCall('w','update_return_task','{"order_id":"S20017"}')]),
                ScriptedResponse(text='已提交退货申请，退款已到账。')])
    app.SETTINGS=settings
    app._SESSION_STORE=SessionStore(settings.db_path)
    app.SHOP_NAME='离线混合回答验收（脚本模型）'
    app.ensure_agent=lambda state,key:CustomerServiceAgent(
        settings=settings,llm=Model(),registry=ToolRegistry(settings))
    app.build_app().launch(server_name='127.0.0.1',server_port=port,css=app.CSS)


def browser(url,out):
    from playwright.sync_api import sync_playwright
    out.mkdir(parents=True,exist_ok=False)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1100})
        page.goto(url,wait_until='domcontentloaded')
        box=page.locator('#message-input textarea')
        box.wait_for()
        page.wait_for_function("document.querySelector('#customer-chat')!==null")
        page.evaluate("window.framesSeen=[]; window.watch=setInterval(()=>{let e=document."
                      "querySelector('#customer-chat');if(e)window.framesSeen.push(e.innerText)},50)")
        box.fill('我想退S20017，同时说明退货政策，同时介绍V201的参数')
        page.locator('#send-message').click()
        page.wait_for_function("document.querySelector('#customer-chat').innerText."
                               "includes('咨询依据（与操作回执分开）')",timeout=60000)
        page.wait_for_function("document.querySelector('#return-task').innerText.includes('S20017')")
        text=page.locator('#customer-chat').inner_text()
        assert '7 天' in text or '7天' in text
        assert 'V201' in text and '商品参数依据' in text
        frames=page.evaluate('clearInterval(window.watch);window.framesSeen')
        assert not any('退款已到账' in f for f in frames)
        page.screenshot(path=str(out/'mixed_final.png'),full_page=True)
        page.reload(wait_until='domcontentloaded')
        page.locator('#conversation-history input').first.wait_for()
        page.locator('#conversation-history input').first.check()
        page.wait_for_function("document.querySelector('#customer-chat').innerText."
                               "includes('咨询依据（与操作回执分开）')")
        page.screenshot(path=str(out/'mixed_restored.png'),full_page=True)
        (out/'frames.json').write_text(json.dumps(frames,ensure_ascii=False,indent=2)+'\n')
        (out/'summary.json').write_text(json.dumps({'paid_requests':0,'model':'scripted',
            'visible_frames':len(frames),'false_completion_frames':0,'policy_and_product':True,
            'draft_only':True,'page_restored':True,'final_chat':text},ensure_ascii=False,
            indent=2)+'\n')
        browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--workflow',action='store_true')
    parser.add_argument('--baseline',action='store_true')
    parser.add_argument('--admin',action='store_true')
    parser.add_argument('--scenario',type=Path)
    parser.add_argument('--port',type=int,default=7903)
    parser.add_argument('--url',default='http://127.0.0.1:7903')
    parser.add_argument('--out',type=Path)
    a=parser.parse_args()
    if a.serve:
        serve(a.scenario,a.port,workflow=a.workflow,candidate=not a.baseline,admin=a.admin)
    else:
        browser(a.url,a.out)
