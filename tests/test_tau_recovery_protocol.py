"""Independent offline fault evidence; no paid requests or task-score claims."""
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip('tau2')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from tau2.data_model.message import ToolMessage, UserMessage  # noqa: E402
from tau2.domains.retail.environment import get_environment, get_tasks  # noqa: E402
from tau2.environment.tool import as_tool  # noqa: E402
from tau_acs_agent import ACSAgent, OfficialRegistry  # noqa: E402

from acs.config import Settings  # noqa: E402
from acs.recovery import classify_failure  # noqa: E402

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall  # noqa: E402


def tool_response(action, call_id='read'):
    return ScriptedResponse(wants_tools=True, tool_calls=[ScriptedToolCall(
        call_id, action.name, json.dumps(action.arguments))])


def make_adapter(model, env, *, recovery=True):
    return ACSAgent(env.get_tools(), env.get_policy(), normalize_results=True,
        settings=Settings(_env_file=None, runtime_recovery_enabled=recovery,
                          workflow_router_enabled=True), llm_factory=lambda s: model)


@pytest.mark.parametrize('status,after_read,recovery,expected_retry', [
    (503, False, False, 0), (503, False, True, 1),
    (429, True, True, 1), (503, True, True, 1), (400, False, True, 0),
])
def test_provider_fault_before_content_is_bounded(status, after_read, recovery, expected_retry):
    env = get_environment()
    action = next(t for t in get_tasks('test') if t.id == '17').evaluation_criteria.actions[0]
    responses = ([tool_response(action)] if after_read else []) + [ScriptedResponse(text='Done.')]

    class FaultModel(ScriptedLLM):
        attempts = 0

        def chat_stream(self, *args, **kwargs):
            self.attempts += 1
            if self.attempts == (2 if after_read else 1):
                error = RuntimeError('Controlled provider fault')
                error.status_code = status
                raise error
            yield from super().chat_stream(*args, **kwargs)

    model = FaultModel(responses)
    adapter = make_adapter(model, env, recovery=recovery)
    before = env.get_db_hash()
    answer, state = adapter.generate_next_message(
        UserMessage(role='user', content='Help with my order'), adapter.get_init_state())
    if after_read:
        assert answer.tool_calls
        answer, state = adapter.generate_next_message(env.get_response(answer.tool_calls[0]), state)
    trace = answer.raw_data['acs_trace']
    assert sum(e['type'] == 'retry_scheduled' for e in trace) == expected_retry
    assert (answer.content == 'Done.') == bool(expected_retry)
    assert env.get_db_hash() == before
    assert model.attempts <= (3 if after_read else 2)


def test_partial_stream_is_not_retried_or_reported_complete():
    env = get_environment()

    class Partial(ScriptedLLM):
        attempts = 0

        def chat_stream(self, *args, **kwargs):
            self.attempts += 1
            yield 'A partial, unverified answer'
            raise ConnectionError('Injected mid-stream connection loss')

    model = Partial([])
    adapter = make_adapter(model, env)
    answer, state = adapter.generate_next_message(
        UserMessage(role='user', content='Help'), adapter.get_init_state())
    assert model.attempts == 1
    assert answer.raw_data['acs_stop_reason'] == 'llm_error'
    assert not any(e['type'] == 'retry_scheduled' for e in answer.raw_data['acs_trace'])
    assert state.agent.messages[-1]['content'] == answer.content


def test_legal_empty_list_is_valid_not_an_error():
    def empty_lookup() -> list[str]:
        return []
    registry = OfficialRegistry([as_tool(empty_lookup)], normalize_results=True)
    registry.reply = ToolMessage(role='tool', id='empty', content='[]')
    payload, _, error = registry.run('empty_lookup', '{}', current_user_id='external')
    assert payload == {'result': []} and error is None
    assert classify_failure(payload=payload)['category'] == 'ok'


def test_successful_official_write_with_lost_receipt_stops_without_replay():
    env = get_environment()
    action = next(t for t in get_tasks('test') if t.id == '17').evaluation_criteria.actions[-1]
    model = ScriptedLLM([tool_response(action,'write'), tool_response(action,'repeat')])
    adapter = make_adapter(model, env)
    before = env.get_db_hash()
    call, state = adapter.generate_next_message(
        UserMessage(role='user', content='Confirmed, update the address'), adapter.get_init_state())
    receipt = env.get_response(call.tool_calls[0])
    after = env.get_db_hash()
    assert not receipt.error and before != after
    # The official receipt remains above; transport fault is injected only at the bridge boundary.
    lost = ToolMessage(role='tool', id=call.tool_calls[0].id,
                       content='Error: Injected lost write receipt', error=True)
    answer, state = adapter.generate_next_message(lost, state)
    assert answer.raw_data['acs_stop_reason'] == 'state_uncertain'
    assert adapter.is_stop(answer) and state.stream is None
    assert len(model.calls) == 1 and env.get_db_hash() == after
    assert answer.raw_data['protocol_guard']['mutates_state'] is True
    assert state.agent.messages[-1]['content'] == answer.content


def test_identical_successful_reads_stop_at_no_progress_limit():
    env = get_environment()
    action = next(t for t in get_tasks('test') if t.id == '17').evaluation_criteria.actions[0]
    model = ScriptedLLM([tool_response(action, f'repeat-{n}') for n in range(6)])
    adapter = make_adapter(model, env)
    answer, state = adapter.generate_next_message(
        UserMessage(role='user', content='Look up my account'), adapter.get_init_state())
    calls = 0
    while answer.tool_calls:
        calls += 1
        answer, state = adapter.generate_next_message(env.get_response(answer.tool_calls[0]), state)
    assert answer.raw_data['acs_stop_reason'] == 'no_progress'
    assert calls == 3 and len(model.calls) == 3
    assert not any(e['type'] == 'retry_scheduled' for e in answer.raw_data['acs_trace'])


@pytest.mark.parametrize('text', [
    'Help me return an item and check a different order.',
    'Do not cancel anything. What is the delivery status?',
    'She said cancel the order, but I am only asking a question.',
    'Please continue with the previous request.',
])
def test_original_english_router_retains_full_scope_for_uncertain_requests(text):
    from acs.routing import route_request
    available = [t.name for t in get_environment().get_tools()]
    route = route_request(text, available, True)
    assert route['confidence'] == 'fallback' and route['allowed_tools'] == available


def test_original_sku_literal_has_known_empty_scope_in_official_namespace():
    from acs.routing import route_request
    available = [t.name for t in get_environment().get_tools()]
    route = route_request('Show this SKU please', available, False)
    assert route['confidence'] == 'high' and route['allowed_tools'] == []


def test_budget_end_prevents_model_call_and_official_tool_execution(monkeypatch):
    from acs import service_agent
    from acs.runtime import RunBudget
    env = get_environment()
    model = ScriptedLLM([ScriptedResponse(text='Must not run')])
    adapter = make_adapter(model, env)
    monkeypatch.setattr(service_agent, 'RunBudget', lambda *a: RunBudget(1e-12, 1))
    before = env.get_db_hash()
    answer, _ = adapter.generate_next_message(
        UserMessage(role='user', content='Help'), adapter.get_init_state())
    assert answer.raw_data['acs_stop_reason'] == 'budget_exceeded'
    assert not model.calls and env.get_db_hash() == before


def test_environment_setup_failure_is_unscored_stops_and_preserves_plan(tmp_path, monkeypatch):
    from scripts import eval_tau_v2 as runner
    from tau2.runner import build

    from acs.service_agent import CustomerServiceAgent
    frozen = {'suite': 'formal', 'model': 'deepseek-flash', 'budget_cny': '40',
              'global_max_actual_requests': 2400, 'seed': 42, 'task_ids': ['17'],
              'measurement_scope': 'offline environment failure test'}
    protocol = tmp_path / 'protocol.json'
    protocol.write_text(json.dumps(frozen))
    monkeypatch.setattr(runner, 'freeze', lambda *a, **kw: frozen)
    monkeypatch.setattr(runner, 'v1_class', lambda *a: CustomerServiceAgent)
    def fail(*a, **kw):
        raise OSError('Injected environment initialization failure')
    monkeypatch.setattr(build, 'build_orchestrator', fail)
    output = tmp_path / 'results'
    runner.execute(protocol, output, tmp_path, tmp_path)
    summary = json.loads((output / 'summary.json').read_text())
    episode = json.loads((output / 'official-17-0-0.json').read_text())
    assert summary['halted'] == 'unrecoverable_episode_error' and not summary['valid']
    assert summary['actual_requests'] == 0 and len(summary['not_started']) == 2
    assert episode['official_reward'] is None and episode['error_type'] == 'OSError'
    assert len((output / 'attempts.jsonl').read_text().splitlines()) == 1


def test_frozen_v1_budget_exception_uses_its_own_module_identity(tmp_path, monkeypatch):
    import io
    import subprocess
    import tarfile

    from scripts.eval_tau_v2 import V1, v1_class

    archive = subprocess.check_output(['git', 'archive', V1, 'src'])
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(tmp_path, filter='data')
    loop = v1_class(tmp_path)
    model = ScriptedLLM([ScriptedResponse(text='Must not run')])
    import importlib
    old_runtime = importlib.import_module(loop.__module__)
    original_budget = old_runtime.RunBudget
    monkeypatch.setattr(old_runtime, 'RunBudget', lambda *a: original_budget(1e-12, 1))
    env = get_environment()
    adapter = ACSAgent(env.get_tools(), env.get_policy(), loop_class=loop,
                      settings=Settings(_env_file=None), llm_factory=lambda s: model,
                      normalize_results=True)
    answer, _ = adapter.generate_next_message(
        UserMessage(role='user', content='Help'), adapter.get_init_state())
    assert answer.raw_data['acs_stop_reason'] == 'budget_exceeded'
    assert not model.calls


def test_bad_read_protocol_halts_before_another_model_or_judge_request():
    env = get_environment()
    action = next(t for t in get_tasks('test') if t.id == '17').evaluation_criteria.actions[1]
    model = ScriptedLLM([tool_response(action), ScriptedResponse(text='Must not be generated')])
    failures = []
    adapter = ACSAgent(env.get_tools(), env.get_policy(), normalize_results=True,
        settings=Settings(_env_file=None, runtime_recovery_enabled=True),
        llm_factory=lambda s: model, protocol_failure=failures.append)
    answer, state = adapter.generate_next_message(
        UserMessage(role='user', content='Account details please'), adapter.get_init_state())
    answer, state = adapter.generate_next_message(ToolMessage(role='tool',
        id=answer.tool_calls[0].id, content='[]'), state)
    assert failures == ['adapter_protocol_error']
    assert answer.raw_data['acs_stop_reason'] == 'adapter_protocol_error'
    assert adapter.is_stop(answer) and len(model.calls) == 1
    assert state.agent.messages[-1]['content'] == answer.content


def test_official_argument_binding_failure_is_not_uncertain_write():
    env = get_environment()
    registry = OfficialRegistry(env.get_tools(), normalize_results=True)
    arguments = {'order_id': '#missing', 'reason': 'no longer needed', 'extra': True}
    from tau2.data_model.message import ToolCall
    receipt = env.get_response(ToolCall(id='bad-args', name='cancel_pending_order',
                                       arguments=arguments))
    assert receipt.error
    registry.reply = receipt
    payload, _, error = registry.run('cancel_pending_order', json.dumps(arguments),
                                    current_user_id='external')
    assert error and payload['error_type'] == 'invalid_arguments'
    assert not registry.uncertain_write and not registry.protocol_error


def test_mismatched_receipt_id_cannot_resume_a_different_call():
    env = get_environment()
    action = next(t for t in get_tasks('test') if t.id == '17').evaluation_criteria.actions[0]
    model = ScriptedLLM([tool_response(action)])
    adapter = make_adapter(model, env)
    _, state = adapter.generate_next_message(
        UserMessage(role='user', content='Help'), adapter.get_init_state())
    with pytest.raises(ValueError, match='does not match'):
        adapter.generate_next_message(ToolMessage(role='tool', id='wrong', content='{}'), state)
    assert len(model.calls) == 1
    adapter.stop(state=state)
