"""Summarize saved pilot records only; never call a model or change official rewards."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from statistics import median

ARMS = ['official', 'acs_v1', 'acs_v2']


def analyze(directory):
    ledger = json.loads((directory / 'requests.json').read_text())
    episodes = [json.loads(p.read_text()) for p in sorted(directory.glob('*-0-0.json'))]
    rows = []
    for record in episodes:
        arm, task = record['arm'], record['task']
        calls = [c for c in ledger['calls'] if c['arm'] == arm and c['task'] == task]
        simulation = record.get('simulation') or {}
        messages = simulation.get('messages', record.get('partial_trajectory', []))
        traces = [e for m in messages for e in (m.get('raw_data') or {}).get('acs_trace', [])]
        routes = [e['detail'] for e in traces if e['type'] == 'route_selected']
        tools = [c for m in messages if m['role'] == 'assistant' for c in m.get('tool_calls') or []]
        tool_results = [m for m in messages if m['role'] == 'tool']
        usages = [c['usage'] for c in calls if c.get('usage')]
        reward = simulation.get('reward_info') or {}
        role_counts = Counter(c['role'] for c in calls)
        row = {'arm': arm, 'task': task, 'reward': record['official_reward'],
               'reward_details': reward, 'error_type': record.get('error_type'),
               'termination': simulation.get('termination_reason'), 'requests': len(calls),
               'roles': dict(role_counts), 'prompt_tokens': sum(u['prompt_tokens'] for u in usages),
               'completion_tokens': sum(u['completion_tokens'] for u in usages),
               'guard_cny': str(sum((Decimal(c.get('charge_guard_cny', '0')) for c in calls),
                                    Decimal(0))),
               'configured_price_estimate_cny': str(sum((Decimal(u['cost_cny']) for u in usages
                                                         if u.get('cost_cny') is not None),
                                                        Decimal(0))),
               'unpriced_calls': sum(u.get('cost_cny') is None for u in usages),
               'missing_usage': sum(not c.get('usage') for c in calls),
               'elapsed_s': record['elapsed_s'], 'tool_calls': len(tools),
               'tool_results': len(tool_results), 'tool_errors': sum(bool(m.get('error'))
                                                                    for m in tool_results),
               'tools': dict(Counter(c['name'] for c in tools)),
               'route_events': len(routes),
               'fallback_routes': sum(r['confidence'] == 'fallback' for r in routes),
               'retries': sum(e['type'] == 'retry_scheduled' for e in traces),
               'failures': dict(Counter(e['detail']['category'] for e in traces
                                        if e['type'] == 'runtime_failure')),
               'stop_reasons': dict(Counter((m.get('raw_data') or {})['acs_stop_reason']
                                            for m in messages if
                                            'acs_stop_reason' in (m.get('raw_data') or {}))),
               'loop_class_module': record.get('loop_class_module'),
               'before_db_hash': record['before_db_hash'],
               'after_db_hash': record['after_db_hash']}
        rows.append(row)
    totals = {}
    for arm in ARMS:
        group = [r for r in rows if r['arm'] == arm]
        scored = [r for r in group if r['reward'] is not None]
        keys = ['requests', 'prompt_tokens', 'completion_tokens', 'tool_calls', 'tool_errors',
                'route_events', 'fallback_routes', 'retries', 'missing_usage', 'unpriced_calls']
        totals[arm] = {k: sum(r[k] for r in group) for k in keys}
        totals[arm].update(episodes=len(group), scored=len(scored),
                           success=sum(r['reward'] == 1 for r in scored),
                           pass_1=sum(r['reward'] for r in scored)/len(scored) if scored else None,
                           pass_3=None,
                           guard_cny=str(sum((Decimal(r['guard_cny']) for r in group), Decimal(0))),
                           configured_price_estimate_cny=str(sum((
                               Decimal(r['configured_price_estimate_cny']) for r in group),
                               Decimal(0))),
                           elapsed_p50_s=median(r['elapsed_s'] for r in group) if group else None)
    total_calls = ledger['calls']
    completed = [c['usage'] for c in total_calls if c.get('usage')]
    estimate = {'description': 'Proposal only, no authorization to run: 40 test tasks x 3 arms x 3',
                'episodes': 360, 'coverage_warning': 'Three selected tasks do not represent full '
                    'test complexity. 25% headroom is a planning assumption, not an upper bound.',
                'estimated_requests': len(total_calls)/max(len(rows), 1)*360,
                'estimated_tokens': sum(u['total_tokens'] for u in completed)/max(len(rows), 1)*360,
                'guard_rate_mean_projection_cny': str(Decimal(ledger['spent_guard_cny'])
                                                     /max(len(rows), 1)*360),
                'guard_rate_projection_with_25pct_cny': str(Decimal(ledger['spent_guard_cny'])
                                                           /max(len(rows), 1)*450)}
    result = {'arms': totals, 'episodes': rows, 'formal_run_estimate': estimate,
              'same_initial_db': len({r['before_db_hash'] for r in rows}) == 1,
              'guard_spent_cny': ledger['spent_guard_cny'], 'halted': ledger['halted'],
              'actual_requests': len(total_calls),
              'roles': dict(Counter(c['role'] for c in total_calls)),
              'role_usage': {role: {
                  'requests': sum(c['role'] == role for c in total_calls),
                  'prompt_tokens': sum(c.get('usage', {}).get('prompt_tokens', 0)
                                       for c in total_calls if c['role'] == role),
                  'completion_tokens': sum(c.get('usage', {}).get('completion_tokens', 0)
                                           for c in total_calls if c['role'] == role),
              } for role in ['agent', 'user', 'judge']},
              'provider_models': sorted({(c.get('response') or {}).get('model') or
                                        c.get('provider_model', 'unknown') for c in total_calls}),
              'limitations': 'One trial, known tasks, no ChatService/UI/RAG/local guards; alias '
                'not weights snapshot; no pass^3; no selection or reruns.'}
    (directory / 'analysis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'episodes'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    analyze(parser.parse_args().directory)
