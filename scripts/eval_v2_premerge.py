"""One frozen paired V1/V2 trial; offline preflight precedes any paid request."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import socket
import subprocess
import sys
from contextlib import nullcontext
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from scripts.eval_delivery_live import RecordedLLM  # noqa: E402
from scripts.eval_p1_live import Ledger  # noqa: E402
from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall  # noqa: E402

from acs.chat_service import ChatService  # noqa: E402
from acs.config import Settings, get_settings  # noqa: E402
from acs.cs_tools import ToolRegistry  # noqa: E402
from acs.llm import LLMClient  # noqa: E402
from acs.scenarios import build_scenario, data_fingerprint, scenario_settings  # noqa: E402
from acs.service_agent import AgentResult, CustomerServiceAgent  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402


def score(turn, record):
    if 'result' not in record:
        return {'complete': False, 'reason': 'turn_exception'}
    answer = record['result']['answer']
    facts = all(any(word in answer for word in group) for group in turn['checks'])
    for oid, color in turn.get('bindings', {}).items():
        spans = re.findall(oid + r'((?:(?!S\d{5}).){0,150})', answer, flags=re.S)
        facts = facts and any(color in span for span in spans)
    actual = (record['task'].get('task') or {}).get('order_id')
    task_ok = actual == turn['expected_draft_order']
    return {'facts': facts, 'task_state': task_ok,
            'complete': bool(facts and task_ok and record['synchronized']
                             and record['result']['stop_reason'] == 'answered')}


def run(cases_path, out, *, live=False, preflight=None):
    cases = json.loads(cases_path.read_text())
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    sources = {str(p.relative_to(ROOT)): digest(p) for p in [
        *sorted((ROOT / 'src/acs').glob('*.py')), Path(__file__),
        ROOT / 'scripts/eval_p1_live.py', ROOT / 'scripts/eval_delivery_live.py',
        ROOT / 'tests/conftest.py', *sorted((ROOT / 'data/scenarios/v2/docs').glob('*.md')),
    ]}
    original = get_settings() if live else Settings(_env_file=None)
    if live:
        proof = json.loads((preflight / 'summary.json').read_text())
        manifest = json.loads((preflight / 'manifest.json').read_text())
        if (proof['live'] or proof['case_sha256'] != digest(cases_path)
                or not proof['infrastructure_ok'] or manifest['source_sha256'] != sources):
            raise ValueError('Matching completed no-network preflight required')
        if (not original.has_llm_key or original.llm_model != 'deepseek-flash'
                or urlsplit(original.llm_base_url).hostname != 'api.deepseek.com'):
            raise ValueError('Only configured official DeepSeek Flash is authorized')
    out.mkdir(parents=True, exist_ok=False)
    ledger = Ledger(out / 'requests.json', limit=Decimal('1'), max_requests=100,
                    input_guard=Decimal('4'), output_guard=Decimal('16'))
    manifest = {'case_sha256': digest(cases_path), 'source_sha256': sources,
                'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                'live': live, 'protocol': cases['protocol'], 'model': original.llm_model,
                'seed': None, 'budget_cny': '1', 'guard_input_output_cny_per_million': [4, 16],
                'price_source': 'https://api-docs.deepseek.com/quick_start/pricing/',
                'price_checked_shanghai': '2026-10-22',
                'guard_note': 'Above peak cache-miss USD rates even at 10 CNY/USD; no cache '
                              'discount assumed. Missing usage halts the entire run.'}
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    rows = []

    def denied(*args, **kwargs):
        raise AssertionError('No dry-run network or embedding/rerank/non-stream paid calls')

    with TemporaryDirectory() as directory, patch.object(LLMClient, 'embed', denied), \
            patch.object(LLMClient, 'rerank', denied), patch.object(LLMClient, 'chat', denied), \
            (nullcontext() if live else patch.object(socket.socket, 'connect', denied)), \
            (nullcontext() if live else patch.object(socket.socket, 'connect_ex', denied)):
        source = Path(directory) / 'source'
        build_scenario(source)
        manifest['scenario'] = data_fingerprint(source / 'business.db')
        (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
        for index, case in enumerate(cases['cases']):
            for enabled in ([False, True] if index % 2 else [True, False]):
                if ledger.halted:
                    break
                local = Path(directory) / f'{index}-{enabled}'
                shutil.copytree(source, local)
                settings = scenario_settings(original, local).model_copy(update={
                    'workflow_router_enabled': enabled, 'runtime_recovery_enabled': enabled,
                    'execution_progress_enabled': False, 'return_state_check_enabled': True,
                    'order_candidate_render_enabled': True, 'order_constraint_enabled': True,
                    'order_aggregation_enabled': True, 'llm_max_output_tokens': 512,
                    'llm_thinking': False, 'llm_temperature': 0, 'llm_timeout_s': 45,
                    'turn_timeout_s': 90, 'max_steps': 4, 'max_api_requests': 5,
                    'max_tool_calls': 6, 'reranker_enabled': False, 'embedding_api_key': '',
                })
                store = SessionStore(settings.db_path)
                sid = store.create('USER001')
                if case.get('preload_draft'):
                    tid = store.begin_turn(sid, 'USER001', '我想退S20017')
                    store.update_return_task(sid, 'USER001', tid, {'order_id': 'S20017'})
                    store.finish_turn(sid, 'USER001', tid, [
                        {'role': 'user', 'content': '我想退S20017'},
                        {'role': 'assistant', 'content': '草稿已存，尚未提交'},
                    ], asdict(AgentResult(answer='草稿已存，尚未提交')))
                with store._connect() as conn:
                    before = {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                              for t in ['return_requests', 'return_audit', 'return_reviews']}
                row = {'case': case['id'], 'enabled': enabled, 'live': live,
                       'fault': case.get('fault'), 'turns': []}
                start_request = len(ledger.calls)
                for turn in case['turns']:
                    store = SessionStore(settings.db_path)  # Restore each turn from actual SQLite.
                    delegate = RecordedLLM(settings, ledger) if live else ScriptedLLM([
                        ScriptedResponse(wants_tools=True, tool_calls=[ScriptedToolCall(
                            f'tool-{i}', c['name'], json.dumps(c['args'], ensure_ascii=False),
                        ) for i, c in enumerate(turn['script_calls'])]),
                        ScriptedResponse(text=turn['script_answer']),
                    ])
                    if case.get('fault'):
                        original_stream = delegate.chat_stream
                        attempts = []

                        def stream_with_fault(*args, attempts=attempts, fault=case['fault'],
                                              original_stream=original_stream, **kwargs):
                            attempts.append(1)
                            trigger = 1 if fault == 'initial_503' else 2
                            if len(attempts) == trigger:
                                error = RuntimeError('controlled pre-send HTTP 503')
                                error.status_code = 503
                                raise error
                            yield from original_stream(*args, **kwargs)

                        delegate.chat_stream = stream_with_fault
                    agent = CustomerServiceAgent(settings=settings, llm=delegate,
                                                 registry=ToolRegistry(settings))
                    started, first = perf_counter(), None
                    frames = []
                    try:
                        for update in ChatService(settings, store).run_turn(
                            turn['query'], 'USER001', sid, agent=agent,
                        ):
                            if update.result.answer and first is None:
                                first = (perf_counter() - started) * 1000
                            frames.append({'phase': update.phase, 'answer': update.result.answer})
                        saved = store.load(sid, 'USER001')
                        record = {'query': turn['query'], 'result': asdict(update.result),
                                  'task': update.task_view, 'frames': frames,
                                  'elapsed_ms': (perf_counter() - started) * 1000,
                                  'first_visible_ms': first,
                                  'synchronized': saved['last_result']['answer']
                                  == agent.messages[-1]['content'] == update.result.answer}
                    except Exception as exc:
                        record = {'query': turn['query'], 'error_type': type(exc).__name__,
                                  'frames': frames}
                        ledger.halted = ledger.halted or 'turn_exception'
                    finally:
                        if live:
                            delegate._client.close()
                    record['score'] = score(turn, record)
                    row['turns'].append(record)
                    if ledger.halted:
                        break
                with store._connect() as conn:
                    after = {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                             for t in before}
                row['business_safe'] = before == after
                if not row['business_safe']:
                    ledger.halted = 'unsafe_business_write'
                row['complete'] = (len(row['turns']) == len(case['turns'])
                                   and all(t['score']['complete'] for t in row['turns']))
                row['request_sequences'] = list(range(start_request + 1, len(ledger.calls) + 1))
                rows.append(row)
                encoded = json.dumps(row, ensure_ascii=False)
                if original.llm_api_key:
                    encoded = encoded.replace(original.llm_api_key, '<redacted>')
                with (out / 'raw.jsonl').open('a') as raw:
                    raw.write(encoded+'\n')
                print(case['id'], enabled, 'complete', row['complete'],
                      'guard', str(ledger.spent), 'halt', ledger.halted, flush=True)
            if ledger.halted:
                break
    ledger.save()
    summary = {'live': live, 'case_sha256': digest(cases_path), 'rows': len(rows),
               'actual_requests': len(ledger.calls), 'guard_spent_cny': str(ledger.spent),
               'halted': ledger.halted,
               'infrastructure_ok': len(rows) == 2 * len(cases['cases']) and all(
                   r['business_safe'] and all('error_type' not in t and t['synchronized']
                                             for t in r['turns']) for r in rows),
               'arms': {str(v): {'cases': sum(r['enabled'] == v for r in rows),
                                 'complete': sum(r['enabled'] == v and r['complete'] for r in rows)}
                        for v in [False, True]},
               'note': 'Lexical/source-state checks are limited; source-grounded manual review '
                       'required. Controlled faults reported separately, no general causal claim.'}
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--preflight', type=Path)
    args = parser.parse_args()
    if args.live and not args.preflight:
        parser.error('--live requires --preflight')
    run(args.cases, args.out, live=args.live, preflight=args.preflight)
