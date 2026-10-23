"""Allowlisted public pilot projections; full messages stay in private evidence.

No model text, tool argument values, receipt contents, arbitrary exception messages,
or provider metadata are copied to a public artifact. Do not use a blacklist redactor.
"""
from __future__ import annotations

import hashlib
import json

ROW_FIELDS = {
    'type', 'episode', 'role', 'request_id', 'time', 'model', 'input_token_bound',
    'output_token_bound', 'body_sha256', 'reserved_cny', 'status', 'latency_ms',
    'estimated_cny', 'estimated_usd', 'timeband', 'timeband_estimated_cny', 'cached_tokens',
    'reason', 'number', 'predicate', 'has_text', 'text_length', 'tool_call_count',
    'argument_parse_states', 'invalid_tool_calls', 'mixed_content', 'stream_complete',
    'evidence_sha256', 'error_type', 'layer', 'thread_id', 'checkpoint_id', 'model_call',
}
USAGE_FIELDS = {'prompt_tokens', 'completion_tokens', 'total_tokens',
                'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'}


def public_usage(usage):
    return {k: v for k, v in (usage or {}).items()
            if k in USAGE_FIELDS and type(v) is int and v >= 0}


def public_id(value):
    """Preserve receipt linkage without publishing provider-generated identifiers."""
    return hashlib.sha256(str(value).encode()).hexdigest() if value is not None else None


def public_rows(rows):
    projected = []
    for row in rows:
        clean = {k: v for k, v in row.items() if k in ROW_FIELDS}
        if 'usage' in row:
            clean['usage'] = public_usage(row['usage'])
        if 'finish_reason' in row:
            value = row['finish_reason']
            clean['finish_reason'] = value if value in ('stop', 'tool_calls', 'length', None) else (
                '<unexpected>')
        if 'tool_names' in row:
            clean['tool_names'] = row['tool_names']  # Official allowlist, applied at inspection.
        if row['type'] in {'tool_intent', 'tool_execute_started'}:
            call = row['call']
            clean['call'] = {'id': public_id(call['id']), 'name': call['name']}
        if row['type'] in {'tool_receipt', 'tool_execute_returned'}:
            receipt = row['receipt']
            clean['receipt'] = {'id': public_id(receipt['id']), 'error': receipt.get('error')}
        projected.append(clean)
    return projected


def public_trace(raw, known_tools):
    messages = []
    for message in raw.get('messages', []):
        messages.append({'role': message['role'], 'has_text': bool(message.get('content')),
            'text_length': len(message.get('content') or ''),
            'tool_calls': [{'id': public_id(c['id']),
                'name': c['name'] if c['name'] in known_tools else '<unknown>',
                'argument_field_count': len(c['arguments'])}
                for c in message.get('tool_calls') or []],
            'tool_call_id': public_id(message.get('id')) if message['role'] == 'tool' else None,
            'error': message.get('error'), 'usage': public_usage(message.get('usage')),
            'cost_usd': message.get('cost')})
    return {'scope': 'Redacted structural trace; raw output/SDK/checkpoints retained locally only',
            'messages': messages, 'termination_reason': raw.get('termination_reason')}


def export_ledger(ledger, out):
    rows = public_rows(ledger.rows())
    (out / 'ledger.public.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
