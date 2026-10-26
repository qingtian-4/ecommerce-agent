"""Browser proof of genuine SDK streaming; offline by default, capped live opt-in."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.request
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = [
    'P003有几个接口？分别是什么类型？请依据商品资料说明接口、总功率和适用设备。',
    '查一下我的订单，列出其中包含Life P3耳机的订单号；有多个请先让我选择。',
    '选O10024，只先保存这个订单的退货草稿。顺便告诉我无理由退货运费由谁承担。',
    '退该订单的Life P3耳机1件，无理由，未拆封并满足所有二次销售条件。先不要预览。',
    '插问P003支持多大功率？保留退货草稿。',
    '更正为同订单的磁吸充电宝，1件，未拆封且满足全部二次销售条件，无理由。生成最新预览，不要提交。',
]


def serve(root, port, *, answer_text=None):
    """Only the offline verification process uses these synthetic SDK bytes."""
    import asyncio
    from contextlib import contextmanager

    import httpx
    import uvicorn

    sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
    from acs.config import Settings
    from acs.episode_budget import EpisodeLedger, EpisodeLimits
    from acs.episode_transport import EpisodeCallback, EpisodeSyncTransport
    from acs.langgraph_poc import deepseek_model
    from acs.local_submission import init_formal_fixture
    from acs.web_service import create_app

    if not root.exists():
        init_formal_fixture(root)
    ledger = EpisodeLedger(root / 'fees.jsonl', cap_cny=3, max_requests=24)
    ledger.begin('offline', EpisodeLimits(requests=24, episode_timeout_s=3600))

    def frame(delta=None, finish=None, usage=None):
        data = {'id': 'mock', 'object': 'chat.completion.chunk', 'created': 1,
                'model': 'deepseek-flash', 'choices': []}
        if delta is not None:
            data['choices'] = [{'index': 0, 'delta': delta, 'finish_reason': finish}]
        if usage:
            data['usage'] = usage
        return b'data: ' + json.dumps(data).encode() + b'\n\n'

    class Body(httpx.AsyncByteStream):
        def __init__(self, request):
            self.request = json.loads(request.content)

        async def __aiter__(self):
            messages = self.request['messages']
            text = next(m['content'] for m in reversed(messages) if m['role'] == 'user')
            tools = next(m for m in reversed(messages) if m['role']!='system')['role']=='tool'
            if '工具' in text and not tools:
                # Mixed content must be provisional and withdrawn before a tool executes.
                yield frame({'content': '这是一段即将撤回的工具阶段说明。' * 14})
                await asyncio.sleep(1)
                yield frame({'tool_calls': [{'index': 0, 'id': 'offline-product',
                    'type': 'function', 'function': {'name': 'get_product',
                    'arguments': '{"product_id":"P003"}'}}]}, 'tool_calls')
            else:
                content = answer_text or ('P003商品咨询：以下是生成中的正文片段，'
                           '提交情况请以右侧权威数据库核验为准。') * 12
                for i in range(0, len(content), 24):
                    yield frame({'content': content[i:i+24]})
                    await asyncio.sleep(.06)
                # Give the browser time to refresh while the provider remains unfinished.
                await asyncio.sleep(2)
                yield frame({'content': '本段结束。'}, 'length' if '拒绝' in text else 'stop')
            yield frame(usage={'prompt_tokens': 100, 'completion_tokens': 200})
            yield b'data: [DONE]\n\n'

    @contextmanager
    def factory():
        with httpx.Client(transport=EpisodeSyncTransport(ledger, 'offline', 'agent',
            lambda: httpx.MockTransport(lambda r: httpx.Response(200,
                headers={'content-type': 'text/event-stream'}, stream=Body(r))),
            streaming=True,
        )) as client:
            yield deepseek_model(Settings(_env_file=None, llm_api_key='offline-test',
                llm_model='deepseek-flash', llm_thinking=False, llm_max_output_tokens=1024),
                http_client=client, callbacks=[EpisodeCallback(ledger, 'offline')]
            ).model_copy(update={'streaming': True})

    uvicorn.run(create_app(root, factory, origin=f'http://127.0.0.1:{port}',
                          runtime_mode='fixed_script', answer_streaming=True),
                host='127.0.0.1', port=port, access_log=False, timeout_graceful_shutdown=5)


def run(out, port, deepseek, env_file, price_review, shared_budget_dir=None):
    from playwright.sync_api import sync_playwright

    os.umask(0o077)
    out.mkdir(parents=True, exist_ok=False)
    private = out / '.private-evidence'
    private.mkdir(mode=0o700)
    runtime = private / 'runtime'
    origin = f'http://127.0.0.1:{port}'
    baseline_ids=set()
    shared_rows=[]
    if shared_budget_dir:
        shared_rows=[json.loads(line) for line in
                     (shared_budget_dir/'fees.jsonl').read_text().splitlines()]
        baseline_ids={r['request_id'] for r in shared_rows if r['type']=='reserved'}
    result = {'kind': 'real_deepseek_development_smoke' if deepseek else 'offline_sdk_browser',
              'source_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                                   text=True).strip(),
              'limits': {'cny': 2 if shared_budget_dir else 3,
                         'http_attempts': 18 if shared_budget_dir else 24},
              'inputs': INPUTS if deepseek else
              ['普通咨询', '工具查询', '协议拒绝'], 'stages': [], 'completed': False}
    if shared_budget_dir:
        result['shared_budget_before']={'requests':len(baseline_ids),
            'reserved_cny':str(sum((Decimal(r['reserved_cny']) for r in shared_rows
                                    if r['type']=='reserved'),Decimal(0))),
            'deadline':next(r['deadline'] for r in shared_rows if r['type']=='episode')}
    result['fingerprints'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [*sorted((ROOT/'src/acs').glob('*.py')),
                  *sorted((ROOT/'src/acs/web_assets').glob('*')), Path(__file__),
                  ROOT/'scripts/local_return_poc.py', ROOT/'scripts/v3_web_demo.py']}
    # Freeze before any provider request. Never retry a failed turn.
    (out/'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    server, errors = None, []
    log = (private/'server.log').open('w')

    def start():
        nonlocal server
        if deepseek:
            command = [str(ROOT/'.venv/bin/python'), 'scripts/v3_web_demo.py',
                       '--state-dir', str(runtime), '--port', str(port), '--deepseek',
                       '--env-file', str(env_file)]
            if shared_budget_dir:
                command += ['--shared-budget-dir',str(shared_budget_dir)]
            if not runtime.exists():
                subprocess.run([str(ROOT/'.venv/bin/python'), '-c',
                    'from pathlib import Path;from acs.local_submission import init_formal_fixture;'
                    'import sys;init_formal_fixture(Path(sys.argv[1]))', str(runtime)],
                    cwd=ROOT, check=True, stdout=log, stderr=log)
                (runtime/'price_review.json').write_bytes(price_review.read_bytes())
        else:
            command = [str(ROOT/'.venv/bin/python'), str(Path(__file__).resolve()),
                       '--serve', str(runtime), '--port', str(port)]
        server = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log)
        deadline = time.monotonic()+60
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(origin, timeout=1).close()
                return
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError('server exited; see private evidence') from None
                time.sleep(.1)
        raise TimeoutError('server start timeout')

    def sql():
        with sqlite3.connect(runtime/'business.sqlite') as conn:
            return {t: (conn.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]
                        if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()
                        else 0) for t in
                    ['return_requests', 'return_confirmations', 'return_audit',
                     'local_submission_receipts']}

    try:
        start()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            context = browser.new_context(viewport={'width': 1280, 'height': 1000})
            page = context.new_page()
            page.add_init_script('''window.stream_observations=[];
                new MutationObserver(()=>{
                    const node=document.querySelector('#chat .draft');
                    if(node)window.stream_observations.push({time:Date.now(),
                        length:node.textContent.length});
                }).observe(document,{subtree:true,childList:true,characterData:true});''')
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('response', lambda r: (private/'http.log').open('a').write(
                str(r.status)+' '+r.url+'\n'))
            page.goto(origin)
            page.wait_for_function("()=>!document.querySelector('#send').disabled && "
                                   "document.querySelector('#connection').textContent==='进度已连接'")
            def state():
                return page.evaluate("""async()=>{
                    const b=await(await fetch('/api/bootstrap')).json();
                    return await(await fetch('/api/sessions/'+b.session_id)).json()}""")
            def request_count():
                path=(shared_budget_dir or runtime)/'fees.jsonl'
                return sum(json.loads(line)['type']=='reserved' for line in
                           path.read_text().splitlines())-len(baseline_ids)
            def idle():
                page.wait_for_function("()=>!document.querySelector('#send').disabled",timeout=180000)
                return state()
            for index, text in enumerate(result['inputs'], 1):
                started = time.perf_counter()
                page.locator('#message').fill(text)
                page.locator('#send').click()
                page.wait_for_function("()=>document.querySelector('#send').disabled",timeout=15000)
                if not deepseek and index < 3:
                    try:
                        page.locator('.draft').wait_for(timeout=20000)
                    except Exception:
                        page.screenshot(path=out/'failure.png', full_page=True)
                        (private/'browser-notice.txt').write_text(page.locator('#notice').inner_text())
                        raise
                    before = request_count()
                    page.screenshot(path=out/f'stream-stage-{index}.png', full_page=True)
                    if index == 1:
                        page.reload()
                        page.locator('.draft').wait_for(timeout=15000)
                        assert request_count()==before
                if deepseek:
                    page.wait_for_function("()=>document.querySelector('.draft') || "
                        "!document.querySelector('#send').disabled", timeout=180000)
                    if page.locator('.draft').count():
                        page.screenshot(path=out/f'stream-stage-{index}.png',full_page=True)
                s = idle()
                good = s['jobs'][-1]['status']=='completed'
                if not deepseek and index == 3:
                    assert not good and page.locator('.draft').count()==0
                elif not good:
                    raise RuntimeError('execution incomplete; no automatic model retry')
                assert sql()['return_requests']==0
                result['stages'].append({'stage': index, 'job_status': s['jobs'][-1]['status'],
                    'latency_s': time.perf_counter()-started, 'sql_counts': sql(),
                    'requests': request_count(), 'draft': s['draft'], 'preview': s['preview'],
                    'visible_draft_observations':page.evaluate('window.stream_observations')})
                (private/f'state-{index}.json').write_text(json.dumps(s,ensure_ascii=False))
                page.screenshot(path=out/f'final-stage-{index}.png',full_page=True)
                if not deepseek and index == 2:
                    # Completed while the initial snapshot was in flight: one stale
                    # snapshot, followed by the real terminal events in initial replay.
                    stale = json.loads(json.dumps(s))
                    stale['history'] = stale['history'][:-1]
                    stale['jobs'][-1]['status'] = 'running'
                    calls = [0]
                    def stale_once(route, request, calls=calls, stale=stale):
                        calls[0] += 1
                        if calls[0] == 1:
                            route.fulfill(json=stale)
                        else:
                            route.continue_()
                    pattern = '**/api/sessions/' + page.evaluate('sid')
                    page.route(pattern, stale_once)
                    page.reload()
                    page.wait_for_function("()=>!document.querySelector('#send').disabled && "
                        "document.querySelector('#connection').textContent==='进度已连接'",
                        timeout=10000)
                    assert page.locator('#chat .bubble.assistant').count()==2, (
                        'final history missing')
                    assert request_count()==result['stages'][-1]['requests']
                    page.unroute(pattern, stale_once)
                    result['completion_during_replay_recovered'] = True
            if deepseek:
                assert s['preview'] and s['draft']['item_id'] == 5
                page.locator('#open').click()
                page.locator('#accept').wait_for(state='visible')
                page.wait_for_function("()=>!document.querySelector('#accept').disabled")
                page.locator('#accept').click()
                page.wait_for_function("()=>document.querySelector('#card-state').textContent==='待审核'",
                                       timeout=90000)
                assert sql()=={'return_requests':1,'return_confirmations':1,'return_audit':1,
                              'local_submission_receipts':1}
                result['submission'] = sql()
                page.screenshot(path=out/'pending-review.png',full_page=True)
            before = request_count()
            page.reload()
            page.wait_for_function("()=>document.querySelector('#connection').textContent==='进度已连接'")
            server.terminate()
            server.wait(timeout=90)
            start()
            page.reload()
            page.wait_for_function("()=>document.querySelector('#connection').textContent==='进度已连接'")
            assert request_count()==before
            assert not page.locator('.draft').count()
            result['restart_no_model_replay'] = True
            context.storage_state(path=private/'browser-state.json')
            browser.close()
        result['completed'] = True
    except Exception as exc:
        result['failure_type'] = type(exc).__name__
        (private/'failure.txt').write_text(traceback.format_exc())
    finally:
        if server and server.poll() is None:
            server.terminate()
            server.wait(timeout=90)
        result['browser_errors'] = errors
        result['sql_counts'] = sql() if runtime.exists() else None
        fee_path = (shared_budget_dir or runtime)/'fees.jsonl'
        if fee_path.exists():
            rows = [json.loads(line) for line in fee_path.read_text().splitlines()]
            attempts = [r for r in rows if r['type']=='reserved'
                        and r['request_id'] not in baseline_ids]
            receipts = [r for r in rows if r['type']=='request_end'
                        and r['request_id'] not in baseline_ids]
            result['fees'] = {
                'http_attempts':len(attempts),
                'paid_http_attempts':len(attempts) if deepseek else 0,
                'reserved_cny':str(sum((Decimal(r['reserved_cny']) for r in attempts),Decimal(0))),
                'known_peak_estimated_cny':str(sum((Decimal(r['estimated_cny']) for r in receipts
                    if r.get('estimated_cny') is not None),Decimal(0))),
                'known_timeband_estimated_cny':str(sum((Decimal(r['timeband_estimated_cny'])
                    for r in receipts if r.get('timeband_estimated_cny') is not None),Decimal(0))),
                'unknown_requests':len(attempts)-sum(r.get('estimated_cny') is not None
                                                     for r in receipts),
                'input_tokens':sum((r.get('usage') or {}).get('prompt_tokens',0) for r in receipts),
                'output_tokens':sum((r.get('usage') or {}).get('completion_tokens',0)
                                    for r in receipts),
                'stop_reasons':[r['reason'] for r in rows if r['type']=='stop'],
            }
            if shared_budget_dir:
                result['shared_budget_after']={
                    'requests':sum(r['type']=='reserved' for r in rows),
                    'reserved_cny':str(sum((Decimal(r['reserved_cny']) for r in rows
                        if r['type']=='reserved'),Decimal(0)))}
            (out/'request_ledger.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
        if (runtime/'web.sqlite').exists():
            with sqlite3.connect(runtime/'web.sqlite') as conn:
                public = [{'id':r[0], 'execution_id':r[1], 'type':r[2],
                           'data':json.loads(r[3])} for r in conn.execute(
                    'SELECT id,job_id,kind,data FROM events ORDER BY id')]
            for event in public:
                if 'text' in event['data']:
                    text = event['data'].pop('text')
                    event['data']['text_length'] = len(text)
                    event['data']['text_sha256'] = hashlib.sha256(text.encode()).hexdigest()
            (out/'event_timing.json').write_text(json.dumps(public,ensure_ascii=False,indent=2))
        (out/'scorecard.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        log.close()
    print(json.dumps({'completed':result['completed'],'stages':len(result['stages']),
                      'failure':result.get('failure_type'),'errors':errors}))
    if not result['completed'] or errors:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--serve',type=Path)
    parser.add_argument('--port',type=int,default=8771)
    parser.add_argument('--deepseek',action='store_true')
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--price-review',type=Path)
    parser.add_argument('--shared-budget-dir',type=Path)
    args=parser.parse_args()
    if args.serve:
        serve(args.serve,args.port)
    else:
        run(args.out,args.port,args.deepseek,args.env_file,args.price_review,args.shared_budget_dir)
