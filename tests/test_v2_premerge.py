"""Pre-merge failure boundaries; scripted model, real SQLite, no provider calls."""

import json
from dataclasses import asdict

import pytest

from acs.chat_service import ChatService
from acs.cs_tools import ToolRegistry
from acs.service_agent import AgentResult, CustomerServiceAgent
from acs.sessions import SessionStore

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall


@pytest.mark.parametrize('fault', ['projection', 'checkpoint', 'final_projection'])
def test_diagnostic_failure_does_not_abort_business_or_final_history(settings, monkeypatch, fault):
    import acs.chat_service as module

    settings = settings.model_copy(update={'execution_progress_enabled': True})
    store = SessionStore(settings.db_path)
    project = module.progress_events

    def broken(*args):
        if fault == 'projection' or (fault == 'final_projection' and args[-1] == 'completed'):
            raise RuntimeError('diagnostic failure private-sentinel')
        return project(*args)

    def fail_save(*args):
        raise OSError('diagnostic failure private-sentinel')

    monkeypatch.setattr(module, 'progress_events', broken)
    if fault == 'checkpoint':
        monkeypatch.setattr(store, 'save_progress', fail_save)
    model = ScriptedLLM([
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall('write', 'update_return_task', '{"order_id":"O10024"}'),
        ]), ScriptedResponse(text='仅草稿已记录，尚未提交。'),
    ])
    agent = CustomerServiceAgent(settings=settings, llm=model, registry=ToolRegistry(settings))
    updates = list(ChatService(settings, store).run_turn('我想退O10024', 'USER001', agent=agent))
    final = updates[-1]
    saved = store.load(final.session_id, 'USER001')
    assert final.phase == 'completed' and final.result.stop_reason == 'answered'
    assert saved['messages'][-1]['content'] == final.result.answer
    assert not saved['running'] and final.task_view['task']['revision'] == 1
    assert len(model.calls) == 2
    assert final.result.totals['diagnostic_errors']
    assert 'private-sentinel' not in json.dumps(final.result.totals)
    with store._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM return_requests').fetchone()[0] == 0


@pytest.mark.parametrize('committed', [False, True])
def test_lost_update_response_compares_this_turn_fields_with_existing_draft(
    settings, monkeypatch, committed,
):
    settings = settings.model_copy(update={'runtime_recovery_enabled': True})
    store = SessionStore(settings.db_path)
    sid = store.create('USER001')
    tid = store.begin_turn(sid, 'USER001', '左侧断音')
    before = store.update_return_task(sid, 'USER001', tid, {
        'order_id': 'O10024', 'reason': 'quality', 'problem_description': '左侧断音',
    })['task']
    store.finish_turn(sid, 'USER001', tid, [
        {'role': 'user', 'content': '左侧断音'}, {'role': 'assistant', 'content': '草稿已存'},
    ], asdict(AgentResult(answer='草稿已存')))
    original, writes = store.update_return_task, []

    def lost(*args, **kwargs):
        writes.append(1)
        if committed:
            original(*args, **kwargs)
        raise OSError('write response unavailable')

    monkeypatch.setattr(store, 'update_return_task', lost)
    model = ScriptedLLM([
        ScriptedResponse(wants_tools=True, tool_calls=[ScriptedToolCall(
            'correction', 'update_return_task', '{"problem_description":"右侧断音"}',
        )]), ScriptedResponse(text='右侧断音已更正并退款'),
    ])
    final = list(ChatService(settings, store).run_turn(
        '更正问题描述为右侧断音', 'USER001', sid,
        agent=CustomerServiceAgent(settings=settings, llm=model, registry=ToolRegistry(settings)),
    ))[-1]
    task = final.task_view['task']
    assert task['problem_description'] == ('右侧断音' if committed else '左侧断音')
    assert task['revision'] == before['revision'] + int(committed) and len(writes) == 1
    check = next(e for e in final.result.trace if e.label == 'write_reconciliation')
    attempt = check.detail['attempts'][0]
    assert attempt['outcome'] == ('applied' if committed else 'not_applied')
    assert attempt['fields']['problem_description']['actual'] == task['problem_description']
    assert task['problem_description'] in final.result.answer
    assert ('本轮字段更正已核实' if committed else '本轮字段更正未生效') in final.result.answer
    assert '退款' not in final.result.answer
    assert store.load(sid, 'USER001')['last_result']['answer'] == final.result.answer


def test_readonly_progress_has_no_intermediate_persistent_writes(settings, monkeypatch):
    settings = settings.model_copy(update={'execution_progress_enabled': True})
    store = SessionStore(settings.db_path)
    writes = []
    monkeypatch.setattr(store, 'save_progress', lambda *args: writes.append(args))
    model = ScriptedLLM([
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall('order', 'get_order', '{"order_id":"O10024"}'),
        ]), ScriptedResponse(text='查询完成'),
    ])
    updates = list(ChatService(settings, store).run_turn(
        '查订单', 'USER001',
        agent=CustomerServiceAgent(settings=settings, llm=model, registry=ToolRegistry(settings)),
    ))
    assert not writes
    assert updates[-1].result.totals['progress'][-1]['kind'] == 'turn_completed'
    assert store.load(updates[-1].session_id, 'USER001')['last_result']['totals']['progress']


from pathlib import Path  # noqa: E402

from acs.routing import route_request  # noqa: E402

ROUTES = json.loads((Path(__file__).parents[1] /
                     'data/eval/v2_premerge_routes.json').read_text())['cases']


@pytest.mark.parametrize('case', ROUTES, ids=[c['id'] for c in ROUTES])
def test_additional_frozen_route_scope_preserves_required_tools(settings, case):
    store = SessionStore(settings.db_path)
    sid = store.create('USER001')
    tid = store.begin_turn(sid, 'USER001', case['text'])
    registry = ToolRegistry(settings.model_copy(update={'knowledge_mode': 'documents'}))
    registry.bind_return_task(store, sid, tid)
    route = route_request(case['text'], registry.names, True)
    assert route['intent'] == case['intent']
    assert set(case['needed_tools']) <= set(route['allowed_tools'])


@pytest.mark.parametrize('enabled', [False, True])
def test_combined_routing_and_recovery_cannot_override_owner_or_cancel_authorization(
    settings, enabled,
):
    settings = settings.model_copy(update={
        'workflow_router_enabled': enabled, 'runtime_recovery_enabled': enabled,
    })
    store = SessionStore(settings.db_path)
    sid = store.create('USER001')
    tid = store.begin_turn(sid, 'USER001', '退O10024')
    before = store.update_return_task(sid, 'USER001', tid, {'order_id': 'O10024'})['task']
    store.interrupt_turn(sid, 'USER001', tid)
    model = ScriptedLLM([
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall('foreign', 'get_order', '{"order_id":"O10030"}'),
            ScriptedToolCall('cancel', 'cancel_return_task', '{}'),
        ]), ScriptedResponse(text='未取得授权'),
    ])
    final = list(ChatService(settings, store).run_turn(
        '不是取消草稿，我要查别人的订单O10030', 'USER001', sid,
        agent=CustomerServiceAgent(settings=settings, llm=model, registry=ToolRegistry(settings)),
    ))[-1]
    assert store.get_return_task(sid, 'USER001')['task'] == before
    assert len(model.calls) == 2 and final.result.tool_calls == 2
    assert not any(e.type == 'retry_scheduled' for e in final.result.trace)
    assert any(e.type == 'tool_result' and e.label == 'get_order'
               and e.detail['result'].get('error_type') == 'forbidden'
               for e in final.result.trace)


@pytest.mark.parametrize('failure', [429, 503, 'timeout'])
@pytest.mark.parametrize('enabled', [False, True])
def test_transient_failure_after_read_preserves_draft_and_only_retries_model(
    settings, failure, enabled, monkeypatch,
):
    monkeypatch.setattr('acs.service_agent.sleep', lambda delay: None)
    settings = settings.model_copy(update={
        'workflow_router_enabled': enabled, 'runtime_recovery_enabled': enabled,
    })
    store = SessionStore(settings.db_path)
    sid = store.create('USER001')
    tid = store.begin_turn(sid, 'USER001', '退O10024')
    before = store.update_return_task(sid, 'USER001', tid, {'order_id': 'O10024'})['task']
    store.interrupt_turn(sid, 'USER001', tid)

    class AfterRead(ScriptedLLM):
        attempts = 0

        def chat_stream(self, *args, **kwargs):
            self.attempts += 1
            if self.attempts == 2:
                self.budget.request()
                error = TimeoutError('injected timeout') if failure == 'timeout' else RuntimeError()
                if failure != 'timeout':
                    error.status_code = failure
                raise error
            yield from super().chat_stream(*args, **kwargs)

    model = AfterRead([
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall('read', 'get_logistics', '{"order_id":"O10023"}'),
        ]), ScriptedResponse(text='物流已查，保留原草稿'),
    ])
    agent = CustomerServiceAgent(settings=settings, llm=model, registry=ToolRegistry(settings))
    final = list(ChatService(settings, store).run_turn(
        '查O10023物流，之后继续退货草稿', 'USER001', sid, agent=agent,
    ))[-1]
    assert model.attempts == (3 if enabled else 2)
    assert final.result.stop_reason == ('answered' if enabled else 'llm_error')
    assert final.result.tool_calls == 1 and final.task_view['task'] == before
    assert sum(e.type == 'tool_result' for e in final.result.trace) == 1
    assert model.budget is None  # Turn budget is detached after completion/interruption.


def test_uncertain_write_stops_model_from_reissuing_the_same_mutation(settings, monkeypatch):
    settings = settings.model_copy(update={'runtime_recovery_enabled': True})
    store = SessionStore(settings.db_path)
    original, writes = store.update_return_task, []

    def lost(*args, **kwargs):
        writes.append(1)
        original(*args, **kwargs)
        raise OSError('response lost')

    monkeypatch.setattr(store, 'update_return_task', lost)
    call = ScriptedToolCall('write', 'update_return_task', '{"order_id":"O10024"}')
    model = ScriptedLLM([
        ScriptedResponse(wants_tools=True, tool_calls=[call]),
        ScriptedResponse(wants_tools=True, tool_calls=[call]),
        ScriptedResponse(text='已重复保存'),
    ])
    final = list(ChatService(settings, store).run_turn(
        '我想退O10024', 'USER001',
        agent=CustomerServiceAgent(settings=settings, llm=model, registry=ToolRegistry(settings)),
    ))[-1]
    assert len(writes) == len(model.calls) == 1
    assert final.task_view['task']['revision'] == 1
    assert final.result.stop_reason == 'tool_error'
    assert store.load(final.session_id, 'USER001')['last_result']['answer'] == final.result.answer


def test_paid_guard_reserves_without_cache_discount_and_stops_before_request(tmp_path):
    from decimal import Decimal

    from scripts.eval_p1_live import Ledger

    ledger = Ledger(tmp_path / 'ledger.json', limit=Decimal('.05'),
                    input_guard=Decimal('4'), output_guard=Decimal('16'))
    from acs.runtime import BudgetExceeded

    with pytest.raises(BudgetExceeded):
        ledger.begin({'max_tokens': 4096, 'messages': []})
    assert not ledger.calls and ledger.spent == 0


def test_invalid_policy_is_rejected_before_a_draft_mutation(settings, monkeypatch):
    from acs.sessions import TaskValidationError

    store = SessionStore(settings.db_path)
    sid = store.create('USER001')
    tid = store.begin_turn(sid, 'USER001', '退O10024')
    registry = ToolRegistry(settings)
    registry.bind_return_task(store, sid, tid)

    def invalid(*args):
        raise TaskValidationError('invalid policy')

    monkeypatch.setattr('acs.cs_tools.return_policy', invalid)
    result, _, error = registry.run('update_return_task', '{"order_id":"O10024"}',
                                    current_user_id='USER001')
    assert error and 'invalid policy' in result['error']
    assert store.get_return_task(sid, 'USER001')['task'] is None


@pytest.mark.parametrize('batch', [False, True])
def test_successful_write_followed_by_failed_state_read_finishes_safely(
    settings, monkeypatch, batch,
):
    settings = settings.model_copy(update={'runtime_recovery_enabled': True})
    store = SessionStore(settings.db_path)
    read = store.get_return_task
    reads = []

    def failed_read(*args):
        reads.append(1)
        if len(reads) > 1:
            raise OSError('read unavailable after successful write')
        return read(*args)

    monkeypatch.setattr(store, 'get_return_task', failed_read)
    model = ScriptedLLM([
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall('write', 'update_return_task', '{"order_id":"O10024"}'),
        ] + ([ScriptedToolCall('later', 'get_order', '{"order_id":"O10023"}')]
             if batch else [])), ScriptedResponse(text='不应该再调用模型'),
    ])
    final = list(ChatService(settings, store).run_turn(
        '退O10024', 'USER001',
        agent=CustomerServiceAgent(settings=settings, llm=model, registry=ToolRegistry(settings)),
    ))[-1]
    assert final.result.stop_reason == 'state_uncertain' and len(model.calls) == 1
    assert final.task_view.get('state_unavailable')
    assert '待核实' in final.result.answer and '不应该' not in final.result.answer
    assert store.load(final.session_id, 'USER001')['last_result']['answer'] == final.result.answer
    assert read(final.session_id, 'USER001')['task']['revision'] == 1


@pytest.mark.parametrize('enabled', [False, True])
def test_workbench_recovery_ablation_is_bound_at_startup(settings, monkeypatch, enabled):
    import app

    settings = settings.model_copy(update={
        'runtime_recovery_enabled': enabled, 'knowledge_mode': 'documents',
    })
    store, calls = SessionStore(settings.db_path), []
    monkeypatch.setattr(app, 'SETTINGS', settings)
    monkeypatch.setattr(app, '_SESSION_STORE', store)
    monkeypatch.setattr(store, 'recover_interrupted', lambda **kw: calls.append(kw) or 0)
    app.build_app()
    assert calls == [{'reconcile': enabled}]


def test_interruption_progress_does_not_claim_authority_when_not_reconciled(settings):
    from acs.progress import progress_events

    store = SessionStore(settings.db_path)
    sid = store.create('USER001')
    tid = store.begin_turn(sid, 'USER001', '查询')
    store.save_progress(sid, 'USER001', tid, progress_events(
        sid, tid, AgentResult(answer='', stop_reason='running'), 'running',
    ))
    store.interrupt_turn(sid, 'USER001', tid, reconcile=False)
    event = store.load(sid, 'USER001')['last_result']['totals']['progress'][-1]
    assert '已核对' not in event['label']


def test_successful_cancel_with_failed_readback_is_persisted_as_uncertain(settings, monkeypatch):
    settings = settings.model_copy(update={'runtime_recovery_enabled': True})
    store = SessionStore(settings.db_path)
    sid = store.create('USER001')
    tid = store.begin_turn(sid, 'USER001', '退O10024')
    store.update_return_task(sid, 'USER001', tid, {'order_id': 'O10024'})
    store.interrupt_turn(sid, 'USER001', tid)
    read, ticks = store.get_return_task, []

    def unavailable(*args):
        ticks.append(1)
        if len(ticks) > 1:
            raise OSError('read unavailable')
        return read(*args)

    monkeypatch.setattr(store, 'get_return_task', unavailable)
    model = ScriptedLLM([ScriptedResponse(wants_tools=True, tool_calls=[
        ScriptedToolCall('cancel', 'cancel_return_task', '{}'),
    ])])
    final = list(ChatService(settings, store).run_turn(
        '中止退货草稿', 'USER001', sid,
        agent=CustomerServiceAgent(settings=settings, llm=model, registry=ToolRegistry(settings)),
    ))[-1]
    assert final.result.stop_reason == 'state_uncertain' and len(model.calls) == 1
    assert read(sid, 'USER001')['task']['status'] == 'cancelled'
    assert store.load(sid, 'USER001')['last_result']['answer'] == final.result.answer
