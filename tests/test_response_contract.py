"""Response parity at the actual adapters/SDKs; no paid model or network."""
from __future__ import annotations

import os
import socket

import pytest
from langchain_core.messages import AIMessage

os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
from .test_tau_langgraph_adapter import paired_run, tool  # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Response contracts never use external network")
    monkeypatch.setattr(socket.socket, "connect", blocked)


def test_mixed_response_preserves_semantics_and_official_parity(tmp_path):
    text = "I will check the order before confirming anything."
    response = AIMessage(content=text, tool_calls=tool().tool_calls,
                         response_metadata={"finish_reason": "tool_calls"})
    rows = paired_run(tmp_path, [response, AIMessage(content="The lookup is complete.")])
    assert [len(row['executed']) for row in rows] == [1, 1]
    assert all(row['termination'] == 'user_stop' for row in rows)
    for row in rows:
        history = row['model_requests'][1]['messages']
        assert next(m for m in history if m.get('tool_calls'))['content'] == text
        assert all(m.content != text for m in row['messages'] if m.role == 'assistant')


import asyncio  # noqa: E402
import json  # noqa: E402
import stat  # noqa: E402
from pathlib import Path  # noqa: E402

import httpx  # noqa: E402

from acs.episode_budget import EpisodeLedger, EpisodeLimits  # noqa: E402
from acs.episode_transport import EpisodeAsyncTransport  # noqa: E402
from acs.model_response_contract import inspect_wire  # noqa: E402
from acs.response_evidence import ResponseEvidence  # noqa: E402
from acs.runtime import BudgetExceeded  # noqa: E402

from .test_episode_budget import (  # noqa: E402
    ARMS,
    USAGE,
    call_message,
    make_agent,
    settings,
)


def payload_for(kind):
    msg, finish = call_message(), 'tool_calls'
    if kind in {'direct', 'missing_finish', 'bad_finish', 'length', 'empty', 'no_done'}:
        msg = {'role': 'assistant', 'content': 'Final answer.' if kind != 'empty' else ''}
        finish = {'missing_finish': None, 'bad_finish': 'content_filter', 'length': 'length'}.get(
            kind, 'stop')
    elif kind == 'mixed':
        msg['content'] = 'I will read the records before confirming completion.'
    elif kind == 'batch':
        msg['tool_calls'].extend(call_message('c2')['tool_calls'])
    elif kind == 'bad_json':
        msg['tool_calls'][0]['function']['arguments'] = '{'
    elif kind == 'nonobject_json':
        msg['tool_calls'][0]['function']['arguments'] = '[]'
    elif kind == 'empty_id':
        msg['tool_calls'][0]['id'] = ''
    elif kind == 'duplicate_id':
        import copy
        msg['tool_calls'].append(copy.deepcopy(msg['tool_calls'][0]))
    elif kind == 'unknown_tool':
        msg['tool_calls'][0]['function']['name'] = 'private_write_sql'
    elif kind == 'null_call':
        msg['tool_calls'] = [None]
    elif kind == 'bad_args':
        msg['tool_calls'][0]['function']['arguments'] = '{}'
    elif kind == 'stop_with_calls':
        finish = 'stop'
    elif kind == 'tool_finish_without_calls':
        msg = {'role': 'assistant', 'content': 'Done'}
    return msg, finish


def wire_completion(message, finish, payload, *, omit_done=False):
    if payload.get('stream'):
        import copy
        delta = copy.deepcopy(message)
        delta.pop('role', None)
        for index, call in enumerate(delta.get('tool_calls') or []):
            if isinstance(call, dict):
                call['index'] = index
        chunks = [{'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]},
                  {'choices': [], 'usage': USAGE}]
        content = b''.join(b'data: ' + json.dumps(c).encode() + b'\n\n' for c in chunks)
        return httpx.Response(200, content=content + (b'' if omit_done else b'data: [DONE]\n\n'),
                              headers={'content-type': 'text/event-stream',
                                       'x-request-id': 'offline-provider-id'})
    return httpx.Response(200, json={'id': 'offline', 'object': 'chat.completion', 'created': 1,
        'model': 'deepseek-flash', 'usage': USAGE,
        'choices': [{'index': 0, 'message': message, 'finish_reason': finish}]},
        headers={'x-request-id': 'offline-provider-id'})


CASES = {
    'direct': None, 'single': None, 'batch': None, 'mixed': None, 'stop_with_calls': None,
    'bad_args': None, 'missing_finish': 'missing_finish_reason',
    'bad_finish': 'invalid_finish_reason', 'length': 'invalid_finish_reason',
    'empty': 'empty_response', 'bad_json': 'invalid_tool_calls',
    'nonobject_json': 'invalid_tool_calls', 'empty_id': 'empty_tool_call_id',
    'duplicate_id': 'duplicate_tool_call_id', 'unknown_tool': 'unknown_tool',
    'null_call': 'sdk_response_parse_error', 'tool_finish_without_calls': 'missing_tool_calls',
}


@pytest.mark.parametrize('kind,expected', CASES.items())
def test_actual_sdk_official_pair_response_matrix(tmp_path, kind, expected):
    from unittest.mock import patch

    from eval_tau_development_pilot import attach_host_hooks
    from tau2.data_model.message import AssistantMessage
    from tau2.domains.retail.environment import get_environment, get_tasks
    from tau2.orchestrator.orchestrator import Orchestrator
    from tau2.runner.build import build_user
    from tau2.runner.simulation import run_simulation
    from tau2.user import user_simulator
    results = []
    public_rows = []
    for arm in ARMS:
        folder = tmp_path / arm
        folder.mkdir()
        ledger = EpisodeLedger(folder / 'ledger')
        ledger.begin('e')
        message, finish = payload_for(kind)
        sends = []

        def response(request, sends=sends, message=message, finish=finish):
            body = json.loads(request.content)
            sends.append(body)
            if len(sends) == 1:
                return wire_completion(message, finish, body)
            return wire_completion({'role': 'assistant', 'content': 'Lookup completed.'},
                                   'stop', body)

        env = get_environment()
        agent = make_agent(arm, env, settings(), ledger, 'e', folder / 'graph',
                           transport_factory=lambda: httpx.MockTransport(response))
        task = next(t for t in get_tasks('base') if t.id == '33')
        user = build_user('user_simulator', env, task, llm='offline')
        orch = Orchestrator('retail', agent, user, env, task, seed=17)
        attach_host_hooks(orch, ledger, 'e', folder)
        count = []

        def user_model(count=count, **kwargs):
            count.append(1)
            return AssistantMessage.text('Read my order.' if len(count) == 1 else '###STOP###')
        with patch.object(user_simulator, 'generate', user_model):
            simulation = run_simulation(orch)
        rows = ledger.rows()
        contracts = [r for r in rows if r['type'] == 'response_contract']
        assert contracts[0]['predicate'] == expected
        actual = [r['call'] for r in rows if r['type'] == 'tool_execute_started']
        assert len(actual) == (0 if expected or kind == 'direct' else 2 if kind == 'batch' else 1)
        if expected:
            assert len(sends) == 1
            assert ledger.summary()['stop_reasons'] == ['adapter_protocol_error']
            assert simulation.termination_reason.value == 'agent_stop'
        if kind == 'mixed':
            assert next(m for m in sends[1]['messages'] if m.get('tool_calls'))[
                'content'] == message['content']
            assert all(m.content != message['content'] for m in simulation.messages
                       if m.role == 'assistant')
        artifacts = list(ledger.evidence.root.glob('*.json'))
        assert artifacts and stat.S_IMODE(ledger.evidence.root.stat().st_mode) == 0o700
        assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in artifacts)
        first_id = next(r['request_id'] for r in rows if r['type'] == 'reserved')
        raw = json.loads((ledger.evidence.root / (first_id + '.json')).read_text())
        assert raw['raw_response_body'] and raw['provider_request_id'] == 'offline-provider-id'
        evidence_index = next(i for i,r in enumerate(rows) if r['type'] == 'response_evidence')
        verdict_index = next(i for i,r in enumerate(rows) if r['type'] == 'response_contract')
        assert evidence_index < verdict_index
        result = {'arm': arm, 'case': kind, 'db': env.get_db_hash(),
                  'reward': simulation.reward_info.model_dump(mode='json'),
                  'calls': actual, 'termination': simulation.termination_reason.value,
                  'stop_reasons': ledger.summary()['stop_reasons'],
                  'predicate': contracts[0]['predicate'], 'http_requests': len(sends),
                  'raw_evidence_before_verdict': True}
        public_rows.append(result)
        results.append({k:v for k,v in result.items() if k != 'arm'})
    assert results[0] == results[1]
    if output := os.environ.get('ACS_RESPONSE_MATRIX_OUT'):
        path = Path(output)
        path.mkdir(parents=True, exist_ok=True)
        # Fixed fixture contains official benchmark data only; no raw model text.
        (path / (kind + '.json')).write_text(json.dumps(public_rows, indent=2) + '\n')


@pytest.mark.parametrize('streaming', [False, True])
def test_raw_evidence_survives_parse_failure_or_missing_usage(tmp_path, streaming):
    from tau_episode import configure_evidence

    from acs.config import Settings
    ledger = EpisodeLedger(tmp_path / 'ledger')
    ledger.begin('e')
    configure_evidence(Settings(_env_file=None, llm_api_key='offline-key'), ledger)
    body = json.dumps({'model': 'deepseek-flash', 'max_tokens': 1024, 'stream': streaming,
                       'messages': [], 'tools': []}).encode()
    async def run():
        async with httpx.AsyncClient(transport=EpisodeAsyncTransport(ledger, 'e', 'agent',
            httpx.MockTransport(lambda r: httpx.Response(200, content=(
                b'data: []\n\ndata: [DONE]\n\n' if streaming else b'{"choices":['))))) as client:
            with pytest.raises(BudgetExceeded, match='usage_missing'):
                await client.post('https://offline.test', content=body)
    asyncio.run(run())
    rows = ledger.rows()
    assert next(r for r in rows if r['type'] == 'response_contract')['predicate'] in {
        'sdk_response_parse_error', 'incomplete_stream'}
    assert len(list(ledger.evidence.root.glob('*.json'))) == 1
    assert ledger.summary()['unknown_requests'] == 1


def test_missing_stream_terminator_is_rejected_before_sdk(tmp_path):
    ledger = EpisodeLedger(tmp_path / 'ledger')
    ledger.begin('e')
    payload = {'model': 'deepseek-flash', 'max_tokens': 1024, 'stream': True,
               'messages': [], 'tools': []}
    response = wire_completion({'content': 'Partial final'}, 'stop', payload, omit_done=True)
    assert inspect_wire(response.content, payload)['predicate'] == 'incomplete_stream'


def test_private_evidence_rejects_symlinks_and_public_permissions(tmp_path):
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    link = tmp_path / 'link'
    link.symlink_to(private, target_is_directory=True)
    with pytest.raises(PermissionError):
        ResponseEvidence(link)
    private.chmod(0o755)
    with pytest.raises(PermissionError):
        ResponseEvidence(private)


def test_resume_dispatch_enforces_outer_tool_budget(tmp_path):
    from tau2.data_model.message import AssistantMessage, ToolCall
    from tau2.domains.retail.environment import get_environment
    from tau_episode import EpisodeAgent
    class Recovering:
        def __init__(self):
            env = get_environment()
            self.tools, self.domain_policy = env.get_tools(), env.get_policy()
        def resume(self, state):
            return AssistantMessage.text('', tool_calls=[ToolCall(
                id='second-write', name='modify_user_address', arguments={})]), state
        def is_stop(self, message):
            return False
    ledger = EpisodeLedger(tmp_path / 'ledger')
    ledger.begin('e', EpisodeLimits(tools=1))
    ledger.tool_intent('e', {'id': 'first'})
    ledger.tool_receipt('e', {'id': 'first'})
    agent = EpisodeAgent(Recovering(), ledger, 'e')
    reply, _ = agent.resume(None)
    assert not reply.tool_calls
    assert reply.raw_data['episode_stop_reason'] == 'episode_tool_limit'
    assert len([r for r in ledger.rows() if r['type'] == 'tool_intent']) == 1


@pytest.mark.parametrize('arm', ARMS)
def test_reused_id_stops_before_second_dispatch_and_preserves_private_body(tmp_path, arm):
    from tau2.data_model.message import UserMessage
    from tau2.domains.retail.environment import get_environment
    ledger = EpisodeLedger(tmp_path / 'ledger')
    ledger.begin('e')
    sent = []
    def response(request):
        payload = json.loads(request.content)
        sent.append(payload)
        return wire_completion(call_message(), 'tool_calls', payload)
    env = get_environment()
    agent = make_agent(arm, env, settings(), ledger, 'e', tmp_path / 'graph',
                       transport_factory=lambda: httpx.MockTransport(response))
    state = agent.get_init_state()
    try:
        intent, state = agent.generate_next_message(
            UserMessage(role='user', content='order'), state)
        receipt = env.get_response(intent.tool_calls[0])
        stopped, state = agent.generate_next_message(receipt, state)
        assert not stopped.tool_calls
        assert stopped.raw_data['episode_stop_reason'] == 'adapter_protocol_error'
        reports = [r for r in ledger.rows() if r['type'] == 'response_contract']
        assert reports[-1]['predicate'] == 'reused_tool_call_id'
        assert len([r for r in ledger.rows() if r['type'] == 'tool_intent']) == 1
        assert len(sent) == 2
    finally:
        agent.stop(state=state)


def test_public_projection_cannot_copy_personal_text_or_unknown_metadata():
    from tau_pilot_artifacts import public_id, public_rows, public_trace
    secret = 'customer.fullname@example.org'
    rows = [
        {'type': 'response_contract', 'finish_reason': secret, 'tool_names': ['<unknown>'],
         'raw_data': {'Authorization': secret}, 'content': secret,
         'predicate': 'invalid_finish_reason'},
        {'type': 'tool_receipt', 'receipt': {'id': secret, 'content': secret, 'error': False}},
        {'type': 'tool_intent', 'call': {'id': secret, 'name': 'get_user_details',
                                       'arguments': {'user_id': secret}}},
    ]
    raw = {'messages': [{'role': 'assistant', 'content': secret, 'tool_calls': [
        {'id': secret, 'name': 'get_user_details', 'arguments': {secret: secret}}]},
        {'role': 'tool', 'id': secret, 'content': secret}]}
    clean_rows = public_rows(rows)
    clean_trace = public_trace(raw, {'get_user_details'})
    projected = json.dumps(clean_rows) + json.dumps(clean_trace)
    assert secret not in projected
    assert '<unexpected>' in projected and 'get_user_details' in projected
    identity = public_id(secret)
    assert clean_rows[1]['receipt']['id'] == clean_rows[2]['call']['id'] == identity
    assert clean_trace['messages'][0]['tool_calls'][0]['id'] == identity
    assert clean_trace['messages'][1]['tool_call_id'] == identity


def test_evidence_redacts_credentials_without_saving_headers_or_input(tmp_path):
    evidence = ResponseEvidence(tmp_path / 'private', secrets=('offline-credential',))
    evidence.save('abc123', {'raw_response_body': 'Text offline-credential'})
    data = (evidence.root / 'abc123.json').read_text()
    assert 'offline-credential' not in data
    assert 'Text [REDACTED_CREDENTIAL]' in data


def test_resume_durable_native_checkpoint_before_dispatch_enforces_budget(tmp_path, monkeypatch):
    from tau2.data_model.message import UserMessage
    from tau2.domains.retail.environment import get_environment
    env = get_environment()
    ledger = EpisodeLedger(tmp_path / 'ledger')
    ledger.begin('e', EpisodeLimits(tools=1))
    ledger.tool_intent('e', {'id': 'consumed'})
    ledger.tool_receipt('e', {'id': 'consumed'})
    def respond(request):
        return wire_completion(call_message('after-crash'), 'tool_calls',
                               json.loads(request.content))
    agent = make_agent('langgraph', env, settings(), ledger, 'e', tmp_path / 'graph',
                       transport_factory=lambda: httpx.MockTransport(respond))
    state = agent.get_init_state()
    def exit_before_dispatch(*args):
        raise RuntimeError('simulated process exit after model checkpoint before dispatch')
    monkeypatch.setattr(agent.inner, '_dispatch', exit_before_dispatch)
    with pytest.raises(RuntimeError, match='simulated process exit'):
        agent.generate_next_message(UserMessage(role='user', content='lookup'), state)
    agent.stop(state=state)
    reopened = make_agent('langgraph', env, settings(), EpisodeLedger(tmp_path / 'ledger'),
                          'e', tmp_path / 'graph', transport_factory=lambda: httpx.MockTransport(
                              lambda request: pytest.fail('checkpoint must not request model')))
    try:
        reply, _ = reopened.resume(state)
        assert not reply.tool_calls
        assert reply.raw_data['episode_stop_reason'] == 'episode_tool_limit'
        assert len([r for r in reopened.ledger.rows() if r['type'] == 'reserved']) == 1
    finally:
        reopened.stop(state=state)


def test_smoke_executes_official_reads_and_shares_global_ledger(tmp_path, monkeypatch):
    import tau_episode
    from eval_tau_development_pilot import run_smoke
    actual = tau_episode.make_agent
    def scripted(arm, environment, config, ledger, episode, checkpoint):
        sent = []
        def response(request):
            payload = json.loads(request.content)
            sent.append(payload)
            message = call_message() if len(sent) == 1 else {
                'role': 'assistant', 'content': 'The order is pending.'}
            return wire_completion(message, 'tool_calls' if len(sent) == 1 else 'stop', payload)
        return actual(arm, environment, config, ledger, episode, checkpoint,
                      transport_factory=lambda: httpx.MockTransport(response))
    monkeypatch.setattr(tau_episode, 'make_agent', scripted)
    ledger = EpisodeLedger(tmp_path / 'ledger')
    private = ResponseEvidence(tmp_path / 'raw-private').root
    assert run_smoke(settings(), ledger, private, tmp_path)
    summary = json.loads((tmp_path / 'smoke.json').read_text())
    assert all(r['db_unchanged'] and r['tool_executions'] == 1 for r in summary['episodes'])
    assert ledger.summary()['requests'] == 4
    ledger.begin('pilot-after-smoke')
    assert ledger.summary()['requests'] == 4
