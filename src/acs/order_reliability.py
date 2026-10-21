"""Finite user constraints and explicit counts; no inference from assistant text."""

from __future__ import annotations

import re

from .cs_tools import TraceEvent
from .order_candidates import _requested_scope, order_candidate_response, order_lookup_result


def _text(value: str) -> str:
    return value.strip().rstrip('。.!！?？')


def color_constraint(history: list[dict]) -> dict:
    """Rebuild from owned, untrimmed user turns; unfamiliar turns suspend enforcement."""
    state = {'status': 'absent'}
    turn = 0
    for message in history:
        if message['role'] != 'user':
            continue
        turn += 1
        raw = message['content']
        text = _text(raw)
        match = re.fullmatch(
            r'(?:后续(?:一直)?|改为)只看(白色|黑色|蓝色)耳机'
            r'(?:，不要列其他颜色，直到我明确更改)?', text,
        )
        if match:
            state = {'status': 'active', 'color': match[1], 'source': raw, 'user_turn': turn}
        elif text in {'取消颜色限制', '改成所有颜色都看'}:
            state = {'status': 'cancelled', 'source': raw, 'user_turn': turn}
        elif (text in {'谢谢', '好的', '收到'} or _requested_scope(raw) == 'headphones'
              or re.fullmatch(r'查一下S[0-9]+的订单详情', text)):
            continue
        else:
            # Could be an unsupported correction. Never apply an old colour across it.
            state = {'status': 'uncertain', 'source': raw, 'user_turn': turn}
    return state


def constrained_response(trace: list[TraceEvent], task: dict, stop: str,
                         query: str, state: dict) -> tuple[str, dict] | None:
    if state['status'] != 'active' or _requested_scope(query) != 'headphones':
        return None
    source = order_lookup_result(trace, task, stop)
    if source is None:
        return None
    return order_candidate_response(trace, task, stop, user_text=query, has_history=True,
                                    color_constraint=state)


def _count_request(query: str) -> dict | None:
    text = _text(query)
    simple = {
        '我的订单一共买了多少件商品？只给总件数，不要列表': 'units',
        '我的订单一共有多少笔订单': 'orders',
        '我的订单一共有多少条商品明细': 'lines',
    }
    if text in simple:
        return {'unit': simple[text], 'scope': 'all'}
    match = re.fullmatch(
        r'统计(我的|已签收订单的|待发货订单的|已发货订单的|已取消订单的|'
        r'订单S[0-9]+(?:、S[0-9]+)*的)订单数、商品明细数和商品件数', text,
    )
    if not match:
        return None
    scope = match[1]
    if scope == '我的':
        return {'unit': 'all', 'scope': 'all'}
    if scope.startswith('订单S'):
        return {'unit': 'all', 'scope': 'orders', 'order_ids': scope[2:-1].split('、')}
    status = {'已签收': 'delivered', '待发货': 'pending', '已发货': 'shipped',
              '已取消': 'cancelled'}[scope[:3]]
    return {'unit': 'all', 'scope': 'status', 'status': status}


def aggregate_response(trace: list[TraceEvent], task: dict, stop: str, query: str, *,
                       has_history: bool) -> tuple[str, dict] | None:
    """Only explicit first-turn counts; implicit historical filters stay with the Loop."""
    request = _count_request(query)
    if has_history or request is None:
        return None
    source = order_lookup_result(trace, task, stop)
    if source is None:
        return None
    index, payload = source
    orders = payload['orders']
    if request['scope'] == 'orders':
        ids = set(request['order_ids'])
        if not ids <= {o['order_id'] for o in orders}:
            return None
        orders = [o for o in orders if o['order_id'] in ids]
    elif request['scope'] == 'status':
        if any(o.get('status') not in {'pending', 'shipped', 'delivered', 'cancelled'}
               for o in orders):
            return None
        orders = [o for o in orders if o['status'] == request['status']]
    items = [i for o in orders for i in o['items']]
    counts = {'orders': len(orders), 'lines': len(items),
              'units': sum(i['quantity'] for i in items)}
    labels = {'orders': '笔订单', 'lines': '条商品明细', 'units': '件商品'}
    keys = labels if request['unit'] == 'all' else [request['unit']]
    answer = '，'.join(f'{counts[k]} {labels[k]}' for k in keys)
    return '统计更正（以本轮查询记录为准）：' + answer + '。', {
        'version': 1, 'tool_result_trace_index': index, 'request': request,
        'order_ids': [o['order_id'] for o in orders], 'counts': counts,
        'unit_semantics': 'orders=count(orders);lines=count(items);units=sum(quantity)',
    }
