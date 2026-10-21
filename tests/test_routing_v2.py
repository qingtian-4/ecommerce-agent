"""Frozen routing requests and enforcement, independent of model decisions."""
import json
from pathlib import Path

import pytest

from acs.chat_service import ChatService
from acs.cs_tools import ToolRegistry
from acs.routing import route_request
from acs.service_agent import CustomerServiceAgent
from acs.sessions import SessionStore

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall

CASES = json.loads((Path(__file__).parents[1] / 'data/eval/routing_v2.json').read_text())['cases']


@pytest.mark.parametrize('case', CASES, ids=[c['text'] for c in CASES])
def test_frozen_intent(case):
    result = route_request(case['text'], [], False)
    assert result['intent'] == case['intent']
    assert result['confidence'] in {'high', 'fallback'}
    assert result['basis'] and result['candidates'] is not None


@pytest.mark.parametrize('enabled', [False, True])
def test_tool_scope_is_enforced_not_just_hidden_in_schema(settings, enabled):
    settings = settings.model_copy(update={'workflow_router_enabled': enabled})
    llm = ScriptedLLM([
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall('wrong', 'get_product', '{"product_id":"P001"}'),
            ScriptedToolCall('right', 'get_order', '{"order_id":"O10024"}'),
        ]), ScriptedResponse(text='订单查询结束'),
    ])
    agent = CustomerServiceAgent(settings=settings, llm=llm, registry=ToolRegistry(settings))
    final = list(ChatService(settings).run_turn('查订单O10024的颜色', 'USER001', agent=agent))[-1]
    names = {t['function']['name'] for t in llm.calls[0]['tools']}
    assert ('get_product' not in names) is enabled
    rejected = [e for e in final.result.trace if e.type == 'error'
                and e.detail.get('error_type') == 'route_scope']
    assert bool(rejected) is enabled
    calls = [e for e in final.result.trace if e.type == 'tool_result' and e.label == 'get_product']
    assert bool(calls) is (not enabled)
    assert not SessionStore(settings.db_path).load(final.session_id, 'USER001')['running']


def test_cross_order_lookup_preserves_return_draft_and_correction_reopens_tools(settings):
    settings = settings.model_copy(update={'workflow_router_enabled': True})
    store = SessionStore(settings.db_path)
    sid = store.create('USER001')
    tid = store.begin_turn(sid, 'USER001', '我想退O10024')
    before = store.update_return_task(sid, 'USER001', tid, {'order_id': 'O10024'})
    store.interrupt_turn(sid, 'USER001', tid)
    llm = ScriptedLLM([ScriptedResponse(wants_tools=True, tool_calls=[
        ScriptedToolCall('query', 'get_logistics', '{"order_id":"O10023"}'),
    ]), ScriptedResponse(text='物流查询结束')])
    agent = CustomerServiceAgent(settings=settings, llm=llm, registry=ToolRegistry(settings))
    final = list(ChatService(settings, store).run_turn('查O10023物流', 'USER001', sid,
                                                       agent=agent))[-1]
    assert final.task_view['task'] == before['task']
    assert not any(t['function']['name'] == 'cancel_return_task' for t in llm.calls[0]['tools'])
    corrected = route_request('改成O10024退1件', agent.registry.names, True)
    assert 'update_return_task' in corrected['allowed_tools']
