"""Frozen real failures through the actual Loop, SQLite and restored ChatService."""

import copy
import json
from contextlib import closing
from pathlib import Path

import pytest

from acs.chat_service import ChatService
from acs.cs_tools import ToolRegistry
from acs.scenarios import data_fingerprint
from acs.service_agent import CustomerServiceAgent
from acs.sessions import SessionStore

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall
from .test_order_candidate_facts import local as local
from .test_order_candidate_facts import no_network as no_network
from .test_order_candidate_facts import p1_scenario as p1_scenario

ROOT = Path(__file__).resolve().parents[1]
FROZEN = json.loads((ROOT / 'data/eval/reliability_v1.json').read_text())
SOURCES = json.loads((ROOT / 'results/reliability_v1_offline/source_badcases.json').read_text())
WHITE = '后续一直只看白色耳机，不要列其他颜色，直到我明确更改。'
HEAD = '我想退最近买的耳机。'


def sql_truth(settings, uid, case):
    with closing(SessionStore(settings.db_path)._connect()) as conn:
        orders = [dict(r) for r in conn.execute(
            'SELECT order_id,status FROM orders WHERE user_id=?', (uid,))]
        rows = [dict(r) for r in conn.execute(
            'SELECT o.order_id,i.id AS item_id,i.quantity,p.category,s.sku_id,s.color '
            'FROM orders o JOIN order_items i USING(order_id) '
            'JOIN products p USING(product_id) JOIN order_item_skus k ON k.item_id=i.id '
            'JOIN product_skus s USING(sku_id) WHERE o.user_id=?', (uid,))]
    if case.get('status'):
        orders = [o for o in orders if o['status'] == case['status']]
    if case.get('order_ids'):
        orders = [o for o in orders if o['order_id'] in case['order_ids']]
    ids = {o['order_id'] for o in orders}
    rows = [r for r in rows if r['order_id'] in ids]
    if case['mode'] == 'candidates':
        rows = [r for r in rows if r['category'] == '无线耳机' and r['color'] == case['color']]
    return {'orders': len(orders), 'lines': len(rows),
            'units': sum(r['quantity'] for r in rows), 'rows': rows}


def scripted_answer(case):
    if case['id'] == 'original-white-restore':
        return next(r['turns'][-1]['result']['answer'] for r in SOURCES
                    if r['case'] == 'history-white-restore' and not r['p1_enabled'])
    if case['id'] == 'stats-total':
        return next(r['turns'][-1]['result']['answer'] for r in SOURCES
                    if r['case'] == 'count-items' and not r['p1_enabled'])
    return case.get('model_answer', 'S20013 黑色、S20019 黑色；共 25 件商品。')


def run_case(local, enabled, case, *, uid='USER001', mutate=None, p0=False, p1=False):
    settings, store = local
    settings = settings.model_copy(update={
        'order_constraint_enabled': enabled, 'order_aggregation_enabled': enabled,
        'return_state_check_enabled': p0, 'order_candidate_render_enabled': p1,
    })
    before = data_fingerprint(settings.db_path)
    with closing(store._connect()) as conn:
        business_before = {t: conn.execute(f'SELECT * FROM {t}').fetchall()
                           for t in ('return_tasks', 'return_requests', 'return_previews',
                                     'return_confirmations', 'return_audit')}
    sid, calls = None, 0
    for text in case['history']:
        responses = [ScriptedResponse(text='已收到。')]
        if text == '查一下S20003的订单详情。':
            responses.insert(0, ScriptedResponse(wants_tools=True, tool_calls=[
                ScriptedToolCall('lookup', 'get_order', '{"order_id":"S20003"}'),
            ]))
        agent = CustomerServiceAgent(settings=settings, llm=ScriptedLLM(responses),
                                     registry=ToolRegistry(settings))
        turn = list(ChatService(settings, store).run_turn(text, uid, sid, agent=agent))[-1]
        sid = turn.session_id
        calls += len(agent.llm.calls)
    if case.get('restore'):
        store = SessionStore(settings.db_path)
    tool = case.get('tool', 'list_orders')
    registry = ToolRegistry(settings)
    if mutate is not None:
        payload = registry._list_orders(current_user_id=uid)
        payload = mutate(copy.deepcopy(payload))
        registry._tools['list_orders'].func = lambda **kwargs: payload
    llm = ScriptedLLM([
        ScriptedResponse(wants_tools=True, tool_calls=[ScriptedToolCall(
            'lookup', tool, json.dumps(case.get('arguments', {})),
        )]), ScriptedResponse(text=scripted_answer(case)),
    ])
    agent = CustomerServiceAgent(settings=settings, llm=llm, registry=registry)
    updates = list(ChatService(settings, store).run_turn(case['query'], uid, sid, agent=agent))
    final = updates[-1]
    snapshot = store.load(final.session_id, uid)
    assert final.result.stop_reason == 'answered'
    assert final.result.answer == agent.messages[-1]['content']
    assert final.history[-1]['content'] == final.result.answer
    assert snapshot['messages'][-1]['content'] == final.result.answer
    assert snapshot['last_result']['answer'] == final.result.answer
    assert final.result.trace[-1].detail['text'] == final.result.answer
    assert data_fingerprint(settings.db_path) == before
    with closing(store._connect()) as conn:
        assert business_before == {t: conn.execute(f'SELECT * FROM {t}').fetchall()
                                   for t in business_before}
    return final, agent, calls + len(llm.calls)


def audit(final, case, truth):
    answer = final.result.answer
    events = [e for e in final.result.trace if e.type == 'fact_render'
              and e.label in {'constrained_order_candidates', 'order_aggregation'}]
    if case['mode'] == 'passthrough':
        return answer == scripted_answer(case) and not events
    if not events:
        return False
    detail = events[-1].detail
    if case['mode'] == 'candidates':
        expected = {(r['order_id'], r['item_id'], r['sku_id'], r['color'])
                    for r in truth['rows']}
        actual = {(r['order_id'], r['item_id'], r['sku_id'], r['color'])
                  for r in detail['rows']}
        return (actual == expected and f'仅展示{case["color"]}耳机' in answer
                and all(f'| {r["sku_id"]} | {r["color"]} |' in answer for r in truth['rows'])
                and (bool(expected) or '未找到' in answer))
    metrics = {'orders': ('笔订单', truth['orders']), 'lines': ('条商品明细', truth['lines']),
               'units': ('件商品', truth['units'])}
    keys = metrics if case['unit'] == 'all' else [case['unit']]
    return (all(f'{metrics[k][1]} {metrics[k][0]}' in answer for k in keys)
            and all(metrics[k][0] not in answer for k in metrics if k not in keys)
            and '|' not in answer
            and detail['counts'] == {k: truth[k] for k in metrics})


@pytest.mark.parametrize('case', FROZEN['cases'], ids=lambda c: c['id'])
def test_frozen_reliability_cases(local, case):
    final, _, _ = run_case(local, True, case)
    assert audit(final, case, sql_truth(local[0], 'USER001', case)), final.result.answer


@pytest.mark.parametrize('case_id', ['original-white-restore', 'stats-total'])
def test_disabled_switch_preserves_original_failure(local, case_id):
    case = next(c for c in FROZEN['cases'] if c['id'] == case_id)
    final, _, _ = run_case(local, False, case)
    assert final.result.answer == scripted_answer(case)
    assert not audit(final, case, sql_truth(local[0], 'USER001', case))


def _bad_payload(payload, kind):
    orders = payload['orders']
    if kind == 'missing_order_id':
        del orders[0]['order_id']
    elif kind == 'non_dict_order':
        orders[0] = 'invalid'
    elif kind == 'invalid_items':
        orders[0]['items'] = {}
    elif kind == 'invalid_sku':
        orders[0]['items'][0]['sku'] = []
    elif kind == 'invalid_quantity':
        orders[0]['items'][0]['quantity'] = True
    elif kind == 'duplicate_order':
        orders.append(copy.deepcopy(orders[0]))
        payload['count'] += 1
    elif kind == 'duplicate_item':
        orders[0]['items'].append(copy.deepcopy(orders[0]['items'][0]))
    elif kind == 'count_mismatch':
        payload['count'] -= 1
    elif kind == 'missing_categories':
        del payload['product_categories']
    elif kind == 'unknown_category':
        payload['product_categories'][orders[0]['items'][0]['product_id']] = '未知'
    return payload


@pytest.mark.parametrize('case_id', ['original-white-restore', 'stats-total'])
@pytest.mark.parametrize('kind', ['missing_order_id', 'non_dict_order', 'invalid_items',
                                 'invalid_sku', 'invalid_quantity', 'duplicate_order',
                                 'duplicate_item', 'count_mismatch'])
def test_malformed_data_declines_without_business_changes(local, case_id, kind):
    case = next(c for c in FROZEN['cases'] if c['id'] == case_id)
    final, _, _ = run_case(local, True, case, mutate=lambda p: _bad_payload(p, kind))
    assert final.result.answer == scripted_answer(case)
    assert not any(e.type == 'fact_render' for e in final.result.trace)


@pytest.mark.parametrize('kind', ['missing_categories', 'unknown_category'])
def test_unknown_category_never_guesses_constraint_match(local, kind):
    case = FROZEN['cases'][0]
    final, _, _ = run_case(local, True, case, mutate=lambda p: _bad_payload(p, kind))
    assert final.result.answer == scripted_answer(case)
    assert not any(e.type == 'fact_render' for e in final.result.trace)


def test_unknown_color_is_not_a_positive_match(local):
    case = FROZEN['cases'][0]
    def missing_color(payload):
        for order in payload['orders']:
            if order['order_id'] == 'S20008':
                order['items'][0]['sku']['color'] = None
        return payload
    final, _, _ = run_case(local, True, case, mutate=missing_color)
    event = next(e for e in final.result.trace if e.type == 'fact_render')
    assert {r['order_id'] for r in event.detail['rows']} == {'S20011', 'S20014'}
    assert '颜色未知' in final.result.answer


@pytest.mark.parametrize('case_id', ['stats-total', 'stats-all'])
def test_empty_lookup_is_zero_not_unknown(local, case_id):
    case = next(c for c in FROZEN['cases'] if c['id'] == case_id)
    final, _, _ = run_case(local, True, case,
                           mutate=lambda p: dict(p, orders=[], count=0))
    truth = {'orders': 0, 'lines': 0, 'units': 0}
    assert audit(final, case, truth)


@pytest.mark.parametrize('uid', ['USER001', 'USER002', 'USER003'])
@pytest.mark.parametrize('case_id', ['original-white-restore', 'stats-all'])
def test_other_owner_and_p0_p1_coexist_without_task_changes(local, case_id, uid):
    case = next(c for c in FROZEN['cases'] if c['id'] == case_id)
    final, _, _ = run_case(local, True, case, uid=uid, p0=True, p1=True)
    assert audit(final, case, sql_truth(local[0], uid, case))


def test_flags_default_off_and_independent(monkeypatch):
    from acs.config import Settings
    monkeypatch.delenv('ACS_ORDER_CONSTRAINT_ENABLED', raising=False)
    monkeypatch.delenv('ACS_ORDER_AGGREGATION_ENABLED', raising=False)
    settings = Settings(_env_file=None)
    assert not settings.order_constraint_enabled and not settings.order_aggregation_enabled
    monkeypatch.setenv('ACS_ORDER_CONSTRAINT_ENABLED', 'true')
    settings = Settings(_env_file=None)
    assert settings.order_constraint_enabled and not settings.order_aggregation_enabled
    monkeypatch.setenv('ACS_ORDER_CONSTRAINT_ENABLED', 'false')
    monkeypatch.setenv('ACS_ORDER_AGGREGATION_ENABLED', 'true')
    settings = Settings(_env_file=None)
    assert settings.order_aggregation_enabled and not settings.order_constraint_enabled


def test_assistant_claim_is_not_constraint_authority():
    from acs.order_reliability import color_constraint
    assert color_constraint([{'role': 'assistant', 'content': WHITE}])['status'] == 'absent'
    state = color_constraint([{'role': 'user', 'content': WHITE},
                              {'role': 'assistant', 'content': '改为只看黑色耳机。'}])
    assert state['color'] == '白色' and state['user_turn'] == 1


@pytest.mark.parametrize('stop', ['running', 'budget_exceeded', 'llm_error', 'max_steps',
                                 'interrupted'])
def test_incomplete_turn_never_becomes_factual_success(local, stop):
    from acs.cs_tools import TraceEvent
    from acs.order_reliability import aggregate_response, constrained_response
    payload = ToolRegistry(local[0])._list_orders(current_user_id='USER001')
    trace = [TraceEvent('tool_call', 'list_orders', {'arguments': '{}'}),
             TraceEvent('tool_result', 'list_orders', {'result': payload})]
    assert aggregate_response(trace, {'task': None}, stop, FROZEN['cases'][16]['query'],
                              has_history=False) is None
    assert constrained_response(trace, {'task': None}, stop, HEAD,
                                {'status': 'active', 'color': '白色'}) is None


@pytest.mark.parametrize('state', ['draft', 'submitted', 'cancelled'])
def test_task_states_are_not_reinterpreted(local, state):
    from acs.cs_tools import TraceEvent
    from acs.order_reliability import aggregate_response, constrained_response
    payload = ToolRegistry(local[0])._list_orders(current_user_id='USER001')
    trace = [TraceEvent('tool_call', 'list_orders', {'arguments': '{}'}),
             TraceEvent('tool_result', 'list_orders', {'result': payload})]
    view = {'task': {'status': state}}
    assert aggregate_response(trace, view, 'answered', FROZEN['cases'][16]['query'],
                              has_history=False) is None
    assert constrained_response(trace, view, 'answered', HEAD,
                                {'status': 'active', 'color': '白色'}) is None


def test_ui_stats_trace_and_session_restore(local, monkeypatch):
    import app
    case = next(c for c in FROZEN['cases'] if c['id'] == 'stats-total')
    final, agent, _ = run_case(local, True, case)
    settings, store = local
    monkeypatch.setattr(app, 'SETTINGS', agent.settings)
    monkeypatch.setattr(app, '_SESSION_STORE', store)
    restored = app.on_restore(final.session_id, {}, 'USER001')
    assert restored[0][-1]['content'] == final.result.answer
    assert '订单统计事实' in restored[2] and 'fact-render' in restored[2]
    assert restored[2].count('· 最终回答') == 1
    assert '尚无退货草稿' in restored[5]


def test_constraint_projection_uses_history_before_context_trimming(local):
    settings, store = local
    settings = settings.model_copy(update={'order_constraint_enabled': True})
    sid = store.create('USER001')
    for raw, answer in [(WHITE, '已收到。')] + [
        ('查一下S20003的订单详情。', '订单查询说明。' * 900) for _ in range(4)
    ]:
        tid = store.begin_turn(sid, 'USER001', raw)
        store.finish_turn(sid, 'USER001', tid, [
            {'role': 'user', 'content': raw}, {'role': 'assistant', 'content': answer},
        ], {'answer': answer})
    agent = CustomerServiceAgent(settings=settings, registry=ToolRegistry(settings),
        llm=ScriptedLLM([ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall('orders', 'list_orders', '{}'),
        ]), ScriptedResponse(text='S20013 黑色。')]))
    final = list(ChatService(settings, store).run_turn(HEAD, 'USER001', sid, agent=agent))[-1]
    assert final.result.totals['context']['dropped_turns'] > 0
    assert final.result.totals['order_constraint']['color'] == '白色'
    event = next(e for e in final.result.trace if e.type == 'fact_render')
    assert {r['order_id'] for r in event.detail['rows']} == {'S20008', 'S20011', 'S20014'}


def test_cross_user_session_restore_is_rejected(local):
    settings, store = local
    sid = store.create('USER001')
    with pytest.raises(PermissionError):
        list(ChatService(settings.model_copy(update={'order_constraint_enabled': True}), store)
             .run_turn(HEAD, 'USER002', sid))


def test_fixed_model_call_count_and_placeholder_usage(local):
    case = next(c for c in FROZEN['cases'] if c['id'] == 'stats-total')
    final, agent, calls = run_case(local, True, case)
    assert calls == len(agent.llm.calls) == 2
    assert all('product_categories' not in m.get('content', '')
               for m in agent.llm.calls[0]['messages'])
    assert final.result.totals['prompt_tokens'] == 20  # ScriptedLLM placeholder, not real usage.


@pytest.mark.parametrize('mode', ['expiry', 'stream_loss'])
def test_expired_or_lost_response_cannot_be_presented_as_repaired(local, monkeypatch, mode):
    from acs.runtime import BudgetExceeded, RunBudget
    settings, store = local
    settings = settings.model_copy(update={'order_aggregation_enabled': True})
    llm = ScriptedLLM([ScriptedResponse(wants_tools=True, tool_calls=[
        ScriptedToolCall('lookup', 'list_orders', '{}'),
    ]), ScriptedResponse(text='错误的25件商品')])
    if mode == 'expiry':
        original = RunBudget.remaining
        def remaining(self):
            if len(llm.calls) >= 2:
                raise BudgetExceeded('expired after model response')
            return original(self)
        monkeypatch.setattr(RunBudget, 'remaining', remaining)
    else:
        def lost_stream(messages, **kwargs):
            response = llm.chat(messages, **kwargs)
            if response.wants_tools:
                yield response
            else:
                yield '尚未完成的25件商品'
                raise RuntimeError('response lost')
        monkeypatch.setattr(llm, 'chat_stream', lost_stream)
    agent = CustomerServiceAgent(settings=settings, llm=llm, registry=ToolRegistry(settings))
    query = next(c['query'] for c in FROZEN['cases'] if c['id'] == 'stats-total')
    final = list(ChatService(settings, store).run_turn(query, 'USER001', agent=agent))[-1]
    assert final.result.stop_reason == ('budget_exceeded' if mode == 'expiry' else 'llm_error')
    assert not any(e.type == 'fact_render' for e in final.result.trace)
    assert store.load(final.session_id, 'USER001')['last_result']['answer'] == final.result.answer
    assert final.task_view['task'] is None


def test_mixed_tools_keep_original_answer(local):
    settings, store = local
    settings = settings.model_copy(update={'order_aggregation_enabled': True})
    llm = ScriptedLLM([ScriptedResponse(wants_tools=True, tool_calls=[
        ScriptedToolCall('lookup', 'list_orders', '{}'),
        ScriptedToolCall('other', 'get_order', '{"order_id":"S20008"}'),
    ]), ScriptedResponse(text='保留混合工作流回答。')])
    agent = CustomerServiceAgent(settings=settings, llm=llm, registry=ToolRegistry(settings))
    query = next(c['query'] for c in FROZEN['cases'] if c['id'] == 'stats-total')
    final = list(ChatService(settings, store).run_turn(query, 'USER001', agent=agent))[-1]
    assert final.result.answer == '保留混合工作流回答。'
    assert not any(e.type == 'fact_render' for e in final.result.trace)


def test_correct_minimal_count_answer_stays_correct_with_aggregation_only(local):
    settings, store = local
    settings = settings.model_copy(update={'order_aggregation_enabled': True})
    llm = ScriptedLLM([ScriptedResponse(wants_tools=True, tool_calls=[
        ScriptedToolCall('lookup', 'list_orders', '{}'),
    ]), ScriptedResponse(text='29 件商品。')])
    agent = CustomerServiceAgent(settings=settings, llm=llm, registry=ToolRegistry(settings))
    query = next(c['query'] for c in FROZEN['cases'] if c['id'] == 'stats-total')
    final = list(ChatService(settings, store).run_turn(query, 'USER001', agent=agent))[-1]
    assert '29 件商品' in final.result.answer and '|' not in final.result.answer
    assert len(llm.calls) == 2
    tool = next(e.detail['result'] for e in final.result.trace if e.type == 'tool_result')
    assert 'product_categories' not in tool  # Aggregation adds zero model input characters.


def test_free_text_s20009_commentary_remains_a_known_failure(local):
    source = next(r for r in SOURCES if r['case'] == 'category-headphones' and r['p1_enabled'])
    case = {'id': 'known-s20009-footer', 'history': [], 'mode': 'passthrough',
            'query': source['turns'][0]['query'],
            'model_answer': source['turns'][0]['result']['answer']}
    final, _, _ = run_case(local, True, case)
    assert final.result.answer == case['model_answer']
    assert 'S20009' in final.result.answer.split('说明：')[-1]
    # Route preservation is not a claim that this answer is factually correct.
