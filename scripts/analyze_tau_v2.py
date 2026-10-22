"""Summarize saved retail records only; never call a model or change official rewards."""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from decimal import Decimal
from pathlib import Path
from statistics import median

ARMS = ['official', 'acs_v1', 'acs_v2']


def analyze(directory):
    ledger = json.loads((directory / 'requests.json').read_text())
    manifest = json.loads((directory / 'manifest.json').read_text())
    formal = manifest.get('suite') == 'formal'
    planned_per_arm = len(manifest['task_ids'])
    episodes = [json.loads(p.read_text()) for p in sorted(directory.glob('*-0-0.json'))]
    recorded = {(e['task'], e['arm']) for e in episodes}
    attempts_path = directory / 'attempts.jsonl'
    attempts = [json.loads(line) for line in attempts_path.read_text().splitlines()] \
        if attempts_path.exists() else []
    unfinished = [dict(task=t, arm=a) for t, a in sorted(
        {(r['task'], r['arm']) for r in attempts} - recorded)]
    unsettled = [c for c in ledger['calls'] if 'charge_guard_cny' not in c]
    rows = []
    for record in episodes:
        arm, task = record['arm'], record['task']
        if 'request_start' in record and 'request_end' in record:
            start, end = record['request_start'], record['request_end']
            if (type(start) is not int or type(end) is not int
                    or not 1 <= start <= end <= len(ledger['calls'])):
                raise ValueError('Invalid episode request range')
            calls = ledger['calls'][start-1:end]
            if any((c.get('task'),c.get('arm')) != (task,arm) for c in calls):
                raise ValueError('Episode request ownership mismatch')
        else:
            # Legacy records without ranges were created before attempt-aware accounting.
            calls = [c for c in ledger['calls'] if c.get('arm') == arm and c.get('task') == task]
        simulation = record.get('simulation') or {}
        messages = simulation.get('messages', record.get('partial_trajectory', []))
        traces = [e for m in messages for e in (m.get('raw_data') or {}).get('acs_trace', [])]
        routes = [e['detail'] for e in traces if e['type'] == 'route_selected']
        tools = [c for m in messages if m['role'] == 'assistant' for c in m.get('tool_calls') or []]
        tool_results = [m for m in messages if m['role'] == 'tool']
        usages = [c['usage'] for c in calls if c.get('usage')]
        reward = simulation.get('reward_info') or {}
        role_counts = Counter(c.get('role', 'pending') for c in calls)
        status = record.get('evaluation_status',
                            'scored' if record['official_reward'] is not None else 'unscored')
        argument_violations = [{'id': c['id'], 'tool': c['name'],
                                'issues': argument_issues(c, manifest['official_tool_contracts'])}
                               for c in tools] if formal else []
        argument_violations = [v for v in argument_violations if v['issues']]
        row = {'arm': arm, 'task': task,
               'official_reward': record['official_reward'], 'evaluation_status': status,
               'reward': record['official_reward'] if status == 'scored' else None,
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
               'tool_results': len(tool_results),
               'argument_violations': argument_violations,
               'invalid_argument_calls': len(argument_violations),
               'unknown_tool_calls': sum(
                   c['name'] not in manifest.get('official_tool_contracts', {})
                   for c in tools) if formal else None,
               'tool_errors': sum(bool(m.get('error'))
                                                                    for m in tool_results),
               'tools': dict(Counter(c['name'] for c in tools)),
               'protocol_categories': dict(Counter(p['category'] for p in
                                                     record.get('protocol_results', []))),
               'empty_scope_routes': sum(not r['allowed_tools'] for r in routes),
               'scope_denials': sum(e['type'] == 'error' and e['detail'].get('error_type')
                                    == 'route_scope' for e in traces),
               'tool_error_details': [{'id': m['id'], 'content': m['content']}
                                      for m in tool_results if m.get('error')],
               'agent_first_content_ms': [c['first_content_ms'] for c in calls
                                          if c['role'] == 'agent' and 'first_content_ms' in c],
               'agent_request_chars': [len(json.dumps({k: c['request'].get(k) for k in
                                                      ['messages', 'tools']},
                                                     ensure_ascii=False))
                                       for c in calls if c['role'] == 'agent'],
               'artifact': f'{arm}-{task}-0-0.json',
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
                'route_events', 'fallback_routes', 'retries', 'missing_usage', 'unpriced_calls',
                'empty_scope_routes', 'scope_denials', 'invalid_argument_calls']
        totals[arm] = {k: sum(r[k] for r in group) for k in keys}
        totals[arm].update(episodes=len(group), scored=len(scored),
                           success=sum(r['reward'] == 1 for r in scored),
                           pass_1=(sum(r['reward'] for r in scored)/planned_per_arm
                                   if len(scored) == planned_per_arm else None),
                           planned=planned_per_arm,
                           success_over_scored=(sum(r['reward'] == 1 for r in scored)/len(scored)
                                                if scored else None),
                           unscored=len(group)-len(scored),
                           unfinished=sum(r['arm'] == arm for r in unfinished),
                           not_started=planned_per_arm-len(group)
                               -sum(r['arm'] == arm for r in unfinished),
                           pass_3=None,
                           guard_cny=str(sum((Decimal(r['guard_cny']) for r in group), Decimal(0))),
                           configured_price_estimate_cny=str(sum((
                               Decimal(r['configured_price_estimate_cny']) for r in group),
                               Decimal(0))),
                           elapsed_p50_s=median(r['elapsed_s'] for r in group) if group else None,
                           elapsed_p95_s=(sorted(r['elapsed_s'] for r in group)[
                               math.ceil(.95*len(group))-1] if group else None),
                           stop_reasons=dict(sum((Counter(r['stop_reasons']) for r in group),
                                                 Counter())),
                           protocol_categories=dict(sum((Counter(r['protocol_categories'])
                                                         for r in group), Counter())))
    total_calls = ledger['calls']
    completed = [c['usage'] for c in total_calls if c.get('usage')]
    estimate = {'description': 'Proposal only, no authorization to run: 40 test tasks x 3 arms x 3',
                'episodes': 360, 'coverage_warning': (
                    'One trial of 40 tasks; no repeated-trial stability estimate. ' if formal
                    else 'Three selected tasks do not represent full test complexity. ')
                    + '25% headroom is a planning assumption, not an upper bound.',
                'estimated_requests': len(total_calls)/max(len(rows), 1)*360,
                'estimated_tokens': sum(u['total_tokens'] for u in completed)/max(len(rows), 1)*360,
                'guard_rate_mean_projection_cny': str(Decimal(ledger['spent_guard_cny'])
                                                     /max(len(rows), 1)*360),
                'guard_rate_projection_with_25pct_cny': str(Decimal(ledger['spent_guard_cny'])
                                                           /max(len(rows), 1)*450)}
    result = {'execution_complete': len(rows) == planned_per_arm * len(ARMS)
                  and not unfinished and not unsettled
                  and all(r['reward'] is not None for r in rows),
              'attempts_complete':len(rows) == planned_per_arm * len(ARMS) and not unfinished,
              'effective_stop_reason': ledger['halted'] or (
                  'interrupted_unsettled_request' if unsettled else
                  'interrupted_attempt' if unfinished else
                  'incomplete_plan' if len(rows) != planned_per_arm * len(ARMS) else
                  'unscored_episodes' if any(r['reward'] is None for r in rows) else None),
              'unfinished_attempts': unfinished,
              'unsettled_request_sequences': [c['sequence'] for c in unsettled],
              'guard_including_unsettled_cny': str(Decimal(ledger['spent_guard_cny'])
                  + sum((Decimal(c['reserved_cny']) for c in unsettled), Decimal(0))),
              'arms': totals, 'episodes': rows, 'formal_run_estimate': estimate,
              'same_initial_db': len({r['before_db_hash'] for r in rows}) == 1,
              'guard_spent_cny': ledger['spent_guard_cny'], 'halted': ledger['halted'],
              'actual_requests': len(total_calls),
              'roles': dict(Counter(c.get('role', 'pending') for c in total_calls)),
              'role_usage': {role: {
                  'requests': sum(c.get('role') == role for c in total_calls),
                  'prompt_tokens': sum(c.get('usage', {}).get('prompt_tokens', 0)
                                       for c in total_calls if c.get('role') == role),
                  'completion_tokens': sum(c.get('usage', {}).get('completion_tokens', 0)
                                           for c in total_calls if c.get('role') == role),
              } for role in ['agent', 'user', 'judge']},
              'provider_models': sorted({(c.get('response') or {}).get('model') or
                                        c.get('provider_model', 'unknown') for c in total_calls}),
              'limitations': 'One trial, known tasks, no ChatService/UI/RAG/local guards; alias '
                'not weights snapshot; no pass^3; no selection or reruns.'}
    if formal:
        result['formal'] = formal_comparison(directory, manifest, rows)
        for comparison in result['formal']['tasks']:
            for attempt in unfinished:
                if comparison['task'] == attempt['task']:
                    comparison[attempt['arm']+'_status'] = 'interrupted_unscored'
        write_comparison(directory, result['formal']['tasks'])
    (directory / 'analysis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v for k, v in result.items()
                      if k not in {'episodes', 'formal'}}, indent=2))
    return result


def argument_issues(call, contracts):
    """Shape/arity checks for the pinned Retail string/list-of-string parameters only."""
    if call['name'] not in contracts:
        return ['Tool not present in official registry']
    schema = contracts[call['name']]['parameters']
    values = call['arguments']
    if not isinstance(values, dict):
        return ['Arguments are not an object']
    properties = schema.get('properties', {})
    problems = [f'Missing: {k}' for k in schema.get('required', []) if k not in values]
    problems += [f'Unknown parameter: {k}' for k in values if k not in properties]
    for k, prop in properties.items():
        if k not in values:
            continue
        value, kind = values[k], prop.get('type')
        if kind == 'string':
            if not isinstance(value, str):
                problems.append(f'{k} is not a string')
        elif kind == 'array' and prop.get('items', {}).get('type') == 'string':
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                problems.append(f'{k} is not a list of strings')
        else:
            raise ValueError('Unsupported analysis schema; do not silently score it')
    return problems


def formal_comparison(directory, manifest, rows):
    """Index every planned task; diagnostic tags never alter official reward."""
    indexed = {(r['task'], r['arm']): r for r in rows}
    comparisons, badcases = [], []
    for task in manifest['task_ids']:
        group = {arm: indexed.get((task, arm)) for arm in ARMS}
        scores = {arm: (r['reward'] if r else None) for arm, r in group.items()}
        if all(v == 1 for v in scores.values()):
            kind = 'common_success'
        elif all(v == 0 for v in scores.values()):
            kind = 'common_failure'
        elif any(v is None for v in scores.values()):
            kind = 'incomplete_or_unscored'
        else:
            kind = 'divergent'
        comparison = {'task': task, **scores, 'category': kind}
        for arm, row in group.items():
            comparison[arm+'_status'] = row['evaluation_status'] if row else 'not_started'
        comparisons.append(comparison)
        for arm, row in group.items():
            if not row or row['reward'] == 1:
                continue
            tags = []
            if row['evaluation_status'] != 'scored':
                tags.append('adapter_or_evaluator_unscored')
            if row['scope_denials'] or row['empty_scope_routes']:
                tags.append('router_scope')
            if any(k in row['stop_reasons'] for k in
                   ['context_limit', 'budget_exceeded', 'max_steps', 'no_progress']):
                tags.append('runtime_stop_boundary')
            if row['tool_errors']:
                tags.append('tool_or_argument_error_inspect_receipt')
            details = row['reward_details']
            if (details.get('db_check') or {}).get('db_match') is False:
                tags.append('official_db_mismatch')
            if (details.get('reward_breakdown') or {}).get('NL_ASSERTION') == 0:
                tags.append('official_answer_or_policy_check_failed')
            badcases.append({'task': task, 'arm': arm, 'reward': row['reward'],
                             'official_reward': row['official_reward'], 'tags': tags,
                             'cause': 'Evidence tags only; source review required',
                             'artifact': row['artifact'], 'errors': row['tool_error_details'],
                             'stop_reasons': row['stop_reasons']})
    write_comparison(directory, comparisons)
    (directory / 'badcase_index.json').write_text(json.dumps(badcases, ensure_ascii=False,
                                                           indent=2)+'\n')
    matched = [(indexed.get((task, 'acs_v1')), indexed.get((task, 'acs_v2')))
               for task in manifest['task_ids']]
    eligible = [(a, b) for a, b in matched if a and b and
                a['reward'] is not None and b['reward'] is not None]
    return {'tasks': comparisons, 'badcases': len(badcases),
            'outcomes': dict(Counter(c['category'] for c in comparisons)),
            'v1_v2': {'eligible_pairs': len(eligible),
                      'v2_only_success': [a['task'] for a, b in eligible
                                          if a['reward'] == 0 and b['reward'] == 1],
                      'v1_only_success': [a['task'] for a, b in eligible
                                          if a['reward'] == 1 and b['reward'] == 0],
                      'v2_fewer_input_tokens_but_failed': [b['task'] for a, b in eligible
                          if b['reward'] == 0 and b['prompt_tokens'] < a['prompt_tokens']]},
            'warning': ('Tags are not causal conclusions. '
                        'All official scores remain raw in episodes.')}


def write_comparison(directory, comparisons):
    fields = ['task', *ARMS, 'category', *(a+'_status' for a in ARMS)]
    with (directory / 'task_comparison.csv').open('w', newline='') as fp:
        writer = csv.DictWriter(fp, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(comparisons)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    analyze(parser.parse_args().directory)
