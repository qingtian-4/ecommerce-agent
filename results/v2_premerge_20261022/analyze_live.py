"""Describe frozen checks and measured usage; never edit scores or rerun a case."""
import json
import math
import statistics
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parent
rows = [json.loads(line) for line in (ROOT / 'live/raw.jsonl').read_text().splitlines()]
ledger = json.loads((ROOT / 'live/requests.json').read_text())
requests = {c['sequence']: c for c in ledger['calls']}


def quantiles(values):
    if not values:
        return None
    values = sorted(values)
    return {'n': len(values), 'p50': statistics.median(values),
            'p95': values[math.ceil(len(values) * .95) - 1], 'max': values[-1]}


summary = {'arms': {}, 'pairs': [], 'guard_spent_cny': ledger['spent_guard_cny'],
           'halted': ledger['halted'], 'all_attempts_retained': True,
           'note': 'Single trial on author-frozen tasks, controlled faults separate. '
                   'No population success-rate or stable causal inference.'}
for enabled in [False, True]:
    arm = [r for r in rows if r['enabled'] is enabled]
    calls = [requests[s] for r in arm for s in r['request_sequences']]
    turns = [t for r in arm for t in r['turns']]
    events = [e for t in turns for e in t.get('result', {}).get('trace', [])]
    counts = Counter(e['type'] for e in events)
    totals = {k: sum(c.get('usage', {}).get(k, 0) for c in calls)
              for k in ['prompt_tokens', 'completion_tokens', 'total_tokens',
                        'cached_prompt_tokens']}
    cost = sum((Decimal(c.get('usage', {}).get('cost_cny') or '0') for c in calls), Decimal(0))
    summary['arms'][str(enabled)] = {
        'cases': len(arm), 'complete': sum(r['complete'] for r in arm),
        'natural_cases': sum(not r['fault'] for r in arm),
        'natural_complete': sum(not r['fault'] and r['complete'] for r in arm),
        'controlled_fault_cases': sum(bool(r['fault']) for r in arm),
        'controlled_fault_complete': sum(bool(r['fault']) and r['complete'] for r in arm),
        'turns': len(turns), 'facts_checks': sum(t['score'].get('facts', False) for t in turns),
        'state_checks': sum(t['score'].get('task_state', False) for t in turns),
        'paid_requests': len(calls), 'tokens': totals,
        'estimated_cost_cny': str(cost),
        'guard_charge_cny': str(sum((Decimal(c['charge_guard_cny']) for c in calls), Decimal(0))),
        'model_aliases_returned': sorted({str(c.get('provider_model')) for c in calls}),
        'tools_attempted': sum(t['result'].get('tool_calls', 0) for t in turns if 'result' in t),
        'route_scope_rejections': sum(e['type'] == 'error'
                                      and e['detail'].get('error_type') == 'route_scope'
                                      for e in events),
        'retry_events': counts['retry_scheduled'],
        'mechanism_events': {name: counts[name] for name in [
            'route_selected', 'runtime_failure', 'fact_render', 'state_check', 'output_gate',
        ]},
        'all_business_safe': all(r['business_safe'] for r in arm),
        'all_synchronized': all(t.get('synchronized', False) for t in turns),
        'conversation_ms': quantiles([
            sum(t.get('elapsed_ms', 0) for t in r['turns']) for r in arm
        ]),
        'turn_ms': quantiles([t['elapsed_ms'] for t in turns if 'elapsed_ms' in t]),
        'first_visible_ms': quantiles([t['first_visible_ms'] for t in turns
                                       if t.get('first_visible_ms') is not None]),
        'model_input_chars': [v for t in turns for v in
                              t.get('result', {}).get('totals', {}).get('model_input_chars', [])],
    }
for case in dict.fromkeys(r['case'] for r in rows):
    group = {r['enabled']: r for r in rows if r['case'] == case}
    if len(group) == 2:
        summary['pairs'].append({'case': case, 'fault': group[False]['fault'],
                                 'baseline': group[False]['complete'],
                                 'candidate': group[True]['complete']})
(ROOT / 'live/analysis.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(summary, ensure_ascii=False, indent=2))
