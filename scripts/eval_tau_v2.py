"""Pinned official retail evaluation; independent three-arm protocol and hard global meter.

Transport metering replaces only the provider-call seam, not official messages,
user simulation, environment functions, or reward computation. Scripted gold actions
are used exclusively in the default offline mode to test wiring, never as a live agent.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import importlib.util
import json
import os
import socket
import subprocess
import sys
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'scripts')]
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
from scripts.eval_delivery_live import RecordedLLM  # noqa: E402
from scripts.eval_p1_live import Ledger  # noqa: E402
from scripts.eval_tau_retail import PINNED_COMMIT  # noqa: E402
from scripts.tau_run_state import (  # noqa: E402
    RequestJournal,
    RunMonitor,
    atomic_json,
    budget_state,
)
from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall  # noqa: E402

from acs.config import Settings, get_settings  # noqa: E402
from acs.llm import LLMClient  # noqa: E402
from acs.scenarios import sha256  # noqa: E402

V1 = 'f34981fa1324310a9d0f0de4430fe1980ba664e9'
V2 = 'f6c3387c81e71022b96d553bd080f5751198b6f9'
ARMS = ['official', 'acs_v1', 'acs_v2']
TASKS = ['17', '51', '68']


def settings_for(original, arm):
    return original.model_copy(update={
        'workflow_router_enabled': arm == 'acs_v2',
        'runtime_recovery_enabled': arm == 'acs_v2',
        'execution_progress_enabled': False, 'return_state_check_enabled': False,
        'order_candidate_render_enabled': False, 'order_constraint_enabled': False,
        'order_aggregation_enabled': False, 'knowledge_mode': 'documents',
        'llm_temperature': 0, 'llm_max_output_tokens': 768, 'llm_thinking': False,
        'llm_timeout_s': 45, 'context_max_chars': 64000, 'max_steps': 8,
        'max_tool_calls': 16, 'max_api_requests': 12, 'turn_timeout_s': 120,
    })


def v1_class(root):
    """Load actual frozen V1 relative imports independently of the current acs package."""
    for p in (root / 'src/acs').glob('*.py'):
        expected = subprocess.check_output(['git', 'show', f'{V1}:src/acs/{p.name}'], cwd=ROOT)
        if p.read_bytes() != expected:
            raise ValueError('V1 source differs: '+p.name)
    spec = importlib.util.spec_from_file_location(
        'benchmark_acs_v1', root / 'src/acs/__init__.py',
        submodule_search_locations=[str(root / 'src/acs')],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return importlib.import_module('benchmark_acs_v1.service_agent').CustomerServiceAgent


def freeze(upstream, v1_root, *, suite="pilot"):
    if suite not in {"pilot", "formal"}:
        raise ValueError("Unknown suite")
    commit = subprocess.check_output(['git', '-C', str(upstream), 'rev-parse', 'HEAD'],
                                     text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(upstream), 'status', '--porcelain'])
    if commit != PINNED_COMMIT or dirty:
        raise ValueError('Pinned, unmodified official checkout required')
    os.environ['TAU2_DATA_DIR'] = str(upstream / 'data')
    from tau2.agent.llm_agent import AGENT_INSTRUCTION, SYSTEM_PROMPT
    from tau2.domains.retail.environment import get_environment, get_tasks, get_tasks_split
    tasks = {t.id: t for t in get_tasks('test')}
    original = get_settings()
    v1_class(v1_root)
    for p in sorted((ROOT / 'src/acs').glob('*.py')):
        if p.read_bytes() != subprocess.check_output(
                ['git', 'show', f'{V2}:src/acs/{p.name}'], cwd=ROOT):
            raise ValueError('V2 source differs: '+p.name)
    task_ids = list(get_tasks_split()['test']) if suite == 'formal' else TASKS
    paths = [*sorted((ROOT / 'src/acs').glob('*.py')), Path(__file__),
             ROOT / 'scripts/tau_acs_agent.py', ROOT / 'scripts/eval_p1_live.py',
             ROOT / 'scripts/eval_delivery_live.py', ROOT / 'tests/conftest.py',
             *sorted((ROOT / 'tests').glob('test_tau*.py')),
             ROOT / 'tests/test_external_tau_protocol.py']
    return {
        'format': 2, 'suite': suite, 'adapter_protocol': ('tau-schema-v2' if suite == 'formal'
                                                            else 'legacy-pilot'),
        'v2_variant': 'original-runtime-router-no-adapted-arm',
        'upstream_commit': commit, 'acs_v1': V1, 'acs_v2': V2,
        'arms': ARMS, 'task_split': 'test', 'task_ids': task_ids, 'trials': 1, 'seed': 20261022,
        'task_categories': {t: sorted({a.name for a in
                            tasks[t].evaluation_criteria.actions or []}) for t in task_ids},
        'official_tool_contracts': {t.name: {
            'parameters': t.params.model_json_schema(), 'returns': t.returns.model_json_schema(),
            'mutates_state': getattr(t._func, '__mutates_state__', True),
            'tool_type': t._func.__tool_type__.value,
        } for t in get_environment().get_tools()},
        'split_counts': {k: len(v) for k, v in get_tasks_split().items()},
        'model': original.llm_model, 'api_host': urlsplit(original.llm_base_url).hostname,
        'api_base': original.llm_base_url, 'temperature': 0, 'max_output_tokens': 768,
        'official_system_prompt': SYSTEM_PROMPT.format(
            agent_instruction=AGENT_INSTRUCTION, domain_policy='<official environment policy>'),
        'acs_budgets': {'context_chars': 64000, 'per_turn_steps': 8, 'api_requests': 12,
                        'tool_calls': 16, 'turn_timeout_s': 120},
        'all_arms': {'orchestrator_steps': 100, 'max_errors': 10, 'episode_timeout_s': 240,
                     'provider_timeout_s': 45, 'sdk_retries': 0, 'litellm_retries': 0,
                     'thinking': False, 'same_user_and_judge_model': True},
        'budget_cny': '40' if suite == 'formal' else '5',
        'global_max_actual_requests': 5000 if suite == 'formal' else 180,
        'guard_input_output_cny_per_million': [4, 16],
        'price_reference': 'https://api-docs.deepseek.com/quick_start/pricing/',
        'evaluation': 'official ALL, strict replay, no scorer changes',
        'failure_policy': 'No reruns; append start before calls; incomplete/unscored separate; '
                          'existing output directories refused, no automatic resume/replay',
        'measurement_scope': 'Official environment + actual ACS Loop/Router/Recovery. No '
                             'ChatService, local SQL, RAG, UI, local P0/P1 or crash recovery.',
        'source_sha256': {str(p.relative_to(ROOT)): sha256(p) for p in paths},
        'upstream_sha256': {str(p.relative_to(upstream)): sha256(p) for p in [
            *sorted((upstream / 'src/tau2').rglob('*.py')),
            *sorted((upstream / 'data/tau2/domains/retail').glob('*')),
        ] if p.is_file()},
        'tasks': {t: tasks[t].model_dump(mode='json') for t in task_ids},
        'packages': {n: importlib.metadata.version(n) for n in
                     ['tau2', 'litellm', 'openai', 'pydantic', 'pydantic-settings']},
    }


class TaggedLedger(Ledger):
    def __init__(self, path, *, limit=Decimal('5'), max_requests=180):
        super().__init__(path, limit=limit, max_requests=max_requests,
                         input_guard=Decimal('4'), output_guard=Decimal('16'))
        self.arm, self.task, self.role = None, None, 'agent'

    def save(self):
        if self.calls and 'arm' not in self.calls[-1]:
            self.calls[-1].update(arm=self.arm, task=self.task, role=self.role)
        if getattr(self, 'journal', None):
            self.journal.save(self)
        else:
            atomic_json(self.path, {'spent_guard_cny':str(self.spent),
                                   'halted':self.halted,'calls':self.calls})


def execute(protocol, out, upstream, v1_root, *, live=False, preflight=None, resume_plan=None):
    frozen = json.loads(protocol.read_text())
    current = freeze(upstream, v1_root, suite=frozen['suite'])
    if resume_plan:
        # Only execution observability/persistence changed; original agent and bridge stay pinned.
        for key in frozen:
            if key != 'source_sha256' and current[key] != frozen[key]:
                raise ValueError('Frozen condition differs: '+key)
        for name, expected in frozen['source_sha256'].items():
            if name != 'scripts/eval_tau_v2.py' and sha256(ROOT/name) != expected:
                raise ValueError('Frozen runtime/adapter differs: '+name)
    elif frozen != current:
        raise ValueError('Conditions differ from frozen protocol')
    if live:
        proof = json.loads((preflight / 'summary.json').read_text())
        if proof['protocol_sha256'] != sha256(protocol) or not proof['valid'] or proof['live']:
            raise ValueError('Matching successful no-network preflight required')
    original = get_settings() if live else Settings(_env_file=None)
    if live and (not original.has_llm_key or frozen['model'] != 'deepseek-flash'
                 or urlsplit(original.llm_base_url).hostname != 'api.deepseek.com'):
        raise ValueError('Only funded official DeepSeek Flash is allowed')
    if resume_plan:
        from scripts.tau_resume import validate_destination
        validate_destination(resume_plan,out)
    out.mkdir(parents=True, exist_ok=False)
    atomic_json(out / 'manifest.json', frozen | {
        'live': live, 'protocol_sha256': sha256(protocol),
        'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    })
    if resume_plan:
        atomic_json(out/'recovery_protocol.json', resume_plan)
    from litellm import ModelResponse
    from loguru import logger
    from openai import OpenAI
    from tau2.agent import llm_agent
    from tau2.data_model.simulation import TextRunConfig
    from tau2.domains.retail.environment import get_tasks
    from tau2.evaluator import evaluator_nl_assertions
    from tau2.runner.build import build_orchestrator
    from tau2.runner.simulation import run_simulation
    from tau2.user import user_simulator
    from tau2.utils import llm_utils
    from tau_acs_agent import ACSAgent

    logger.remove()
    logger.add(sys.stderr, level='CRITICAL')
    ledger = TaggedLedger(out / 'requests.json', limit=Decimal(frozen['budget_cny']),
                          max_requests=frozen['global_max_actual_requests'])
    prefix = 0
    if resume_plan:
        from scripts.tau_resume import claim_continuation, inherited_ledger
        ledger.calls, ledger.spent = inherited_ledger(resume_plan)
        prefix = len(ledger.calls)
        ledger.journal = RequestJournal(out, prefix)
        ledger.save()
        if live:
            claim_continuation(resume_plan, out)
    client = OpenAI(api_key=original.llm_api_key, base_url=original.llm_base_url,
                    max_retries=0, timeout=45) if live else None
    OldLoop = v1_class(v1_root)
    tasks = {t.id: t for t in get_tasks('test')}
    rows = []
    scenario = {}
    llm_args = {'temperature': 0, 'max_tokens': 768, 'num_retries': 0,
                'timeout': 45, 'extra_body': {'thinking': {'type': 'disabled'}}}
    model = 'openai/'+frozen['model']
    evaluator_nl_assertions.DEFAULT_LLM_NL_ASSERTIONS = model
    evaluator_nl_assertions.DEFAULT_LLM_NL_ASSERTIONS_ARGS = llm_args

    def tagged_generate(fn, role):
        def wrapped(*args, **kwargs):
            previous, ledger.role = ledger.role, role
            try:
                return fn(*args, **kwargs)
            finally:
                ledger.role = previous
        return wrapped

    def completion(**kwargs):
        clean = {k: v for k, v in kwargs.items() if k not in
                 {'num_retries', 'timeout', 'api_base', 'api_key'}}
        clean['model'] = clean['model'].removeprefix('openai/')
        if not live:
            if ledger.role == 'user':
                scenario['users'] += 1
                cursor = (len(scenario['offline_model'].calls) if scenario.get('offline_model')
                          else scenario['agent_index'])
                content = ('Please help with my retail request.' if scenario['users'] == 1
                           else '###STOP###' if scenario.get('fault') or
                           cursor >= len(scenario['agent_responses']) else 'Please continue.')
                message = {'role': 'assistant', 'content': content}
            elif ledger.role == 'judge':
                assertions = tasks[ledger.task].evaluation_criteria.nl_assertions or []
                message = {'role': 'assistant', 'content': json.dumps({'results': [
                    {'expectedOutcome': a, 'metExpectation': True,
                     'reasoning': 'Scripted infrastructure check, not quality evidence'}
                    for a in assertions]})}
            else:
                responses = scenario['agent_responses']
                message = responses[min(scenario['agent_index'], len(responses)-1)]
                scenario['agent_index'] += 1
            return ModelResponse(model=frozen['model'], choices=[{
                'index': 0, 'message': message,
                'finish_reason': 'tool_calls' if message.get('tool_calls') else 'stop',
            }], usage={'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15})
        record = ledger.begin(clean)
        started = perf_counter()
        try:
            response = client.chat.completions.create(**clean)
            record['latency_ms'] = (perf_counter()-started)*1000
            record['response'] = response.model_dump(mode='json')
            # Reuse the project's real usage/pricing parser, without another request.
            usage = LLMClient._record(accounting_client, response,
                                      model=original.llm_model, stage=ledger.role,
                                      latency_ms=record['latency_ms'], thinking=False)
            ledger.complete(record, usage, response.usage.model_dump() if response.usage else None)
            return ModelResponse(**response.model_dump())
        except Exception as exc:
            record['error_type'] = type(exc).__name__
            record['error_message'] = str(exc).replace(original.llm_api_key, '<redacted>')
            if 'charge_guard_cny' not in record:
                ledger.complete(record, None)
            raise
        finally:
            ledger.save()

    accounting_client = LLMClient(original) if live else None

    def protocol_failure(reason):
        ledger.halted = ledger.halted or reason
        ledger.save()

    def forbidden(*args, **kwargs):
        raise AssertionError('No network in preflight')

    def write_json(path, value):
        data = json.dumps(value, ensure_ascii=False, indent=2)
        if original.llm_api_key:
            data = data.replace(original.llm_api_key, '<redacted>')
        atomic_json(path, json.loads(data))

    def episode(task, arm, *, fault=False, omit_write=False):
        ledger.arm, ledger.task = arm, task.id
        scenario.update(users=0, agent_index=0, offline_model=None, fault=fault)
        actions = (task.evaluation_criteria.actions or []) if not live else []
        if omit_write:
            actions = actions[:-1]
        responses = []
        for start in range(0, max(len(actions), 1), 12):
            for i in range(start, min(start+12, len(actions)), 2):
                responses.append({'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': f'offline-{j}', 'type': 'function', 'function': {
                        'name': actions[j].name, 'arguments': json.dumps(actions[j].arguments)}}
                    for j in range(i, min(i+2, len(actions)))
                ]})
            responses.append({'role': 'assistant', 'content': 'Offline stage complete.'})
        scenario['agent_responses'] = responses
        record = {'task': task.id, 'arm': arm, 'fault': fault, 'omit_write': omit_write,
                  'before_db_hash': None, 'request_start': len(ledger.calls)+1,
                  'protocol_sha256': sha256(protocol)}
        if resume_plan:
            record['attempt_number'] = resume_plan['attempt_numbers'][task.id+'/'+arm]
            record['parent_evidence'] = resume_plan['parent']
        with (out / 'attempts.jsonl').open('a') as fp:
            fp.write(json.dumps(record | {'status': 'started'})+'\n')
            fp.flush()
            os.fsync(fp.fileno())
        started = perf_counter()
        orchestrator = None
        try:
            config = TextRunConfig(domain='retail', agent='llm_agent', user='user_simulator',
                                   llm_agent=model, llm_user=model, llm_args_agent=llm_args,
                                   llm_args_user=llm_args, max_steps=100, max_errors=10,
                                   enforce_communication_protocol=False)
            orchestrator = build_orchestrator(config, task, seed=frozen['seed'])
            record['before_db_hash'] = orchestrator.environment.get_db_hash()
            if arm != 'official':
                from acs.service_agent import CustomerServiceAgent
                settings = settings_for(original, arm)
                if live:
                    def factory(s):
                        return RecordedLLM(s, ledger)
                else:
                    scripted = [ScriptedResponse(text=r['content'] or '',
                        wants_tools=bool(r.get('tool_calls')), tool_calls=[ScriptedToolCall(
                            c['id'], c['function']['name'], c['function']['arguments'])
                            for c in r.get('tool_calls', [])]) for r in responses]

                    class OfflineModel(ScriptedLLM):
                        attempts = 0

                        def chat_stream(self, *args, **kwargs):
                            self.attempts += 1
                            if fault and self.attempts == 1:
                                error = RuntimeError('controlled pre-send 503')
                                error.status_code = 503
                                raise error
                            yield from super().chat_stream(*args, **kwargs)

                    def factory(s):
                        model = OfflineModel(scripted)
                        scenario['offline_model'] = model
                        return model
                orchestrator.agent = ACSAgent(
                    tools=orchestrator.environment.get_tools(),
                    domain_policy=orchestrator.environment.get_policy(), settings=settings,
                    loop_class=OldLoop if arm == 'acs_v1' else CustomerServiceAgent,
                    llm_factory=factory, normalize_results=frozen['suite'] == 'formal',
                    protocol_failure=protocol_failure if live else None,
                )
            orchestrator.timeout = 240
            simulation = run_simulation(orchestrator)
            record.update(simulation=simulation.model_dump(mode='json'),
                          official_reward=simulation.reward_info.reward,
                          evaluation_status=('invalid_protocol' if ledger.halted in {
                              'adapter_protocol_error', 'state_uncertain',
                          } else 'scored'))
        except BaseException as exc:
            record.update(error_type=type(exc).__name__,
                          error_message=str(exc).replace(original.llm_api_key, '<redacted>')
                          if original.llm_api_key else str(exc), official_reward=None,
                          evaluation_status='unscored',
                          partial_trajectory=([m.model_dump(mode='json')
                                               for m in orchestrator.get_trajectory()]
                                              if orchestrator else []))
            ledger.halted = ledger.halted or ('interrupted' if isinstance(
                exc, (KeyboardInterrupt, SystemExit)) else 'unrecoverable_episode_error')
        finally:
            record.update(after_db_hash=(orchestrator.environment.get_db_hash()
                                         if orchestrator else None),
                          elapsed_s=perf_counter()-started, request_end=len(ledger.calls))
            state = getattr(orchestrator, 'agent_state', None)
            if arm != 'official' and state:
                if state.stream is not None:
                    state.stream.close()
                    state.stream = None
                record['active_loop_messages'] = state.agent.messages
                record['loop_class_module'] = state.agent.__class__.__module__
                record['protocol_results'] = state.agent.registry.protocol_records
            write_json(out / f'{arm}-{task.id}-{int(fault)}-{int(omit_write)}.json', record)
            rows.append(record)
            if arm != 'official' and orchestrator:
                orchestrator.agent.stop(state=state)
            print(task.id, arm, 'reward', record['official_reward'], 'fault', fault,
                  'guard', ledger.spent, 'halt', ledger.halted, flush=True)
        return record

    try:
        with ExitStack() as stack:
            if resume_plan:
                stack.enter_context(RunMonitor(out, lambda: {
                    'task':ledger.task,'arm':ledger.arm,'request_sequence':len(ledger.calls),
                    'completed':sum(r['evaluation_status'] == 'scored' for r in rows),
                    **budget_state(ledger), 'halted':ledger.halted,
                }))
            stack.enter_context(patch.object(llm_utils, 'completion', completion))
            for module, role in [(llm_agent, 'agent'), (user_simulator, 'user'),
                                  (evaluator_nl_assertions, 'judge')]:
                stack.enter_context(patch.object(module, 'generate',
                                                tagged_generate(module.generate, role)))
            if not live:
                stack.enter_context(patch.object(socket.socket, 'connect', forbidden))
                stack.enter_context(patch.object(socket.socket, 'connect_ex', forbidden))
            for index, task_id in enumerate(frozen['task_ids']):
                rotation = index % len(ARMS)
                for arm in ARMS[rotation:]+ARMS[:rotation]:
                    if ledger.halted:
                        break
                    if resume_plan and not any(p['task'] == task_id and p['arm'] == arm
                                               for p in resume_plan['pending']):
                        continue
                    episode(tasks[task_id], arm)
                if ledger.halted:
                    break
            if not live and not ledger.halted and not resume_plan:
                episode(tasks['17'], 'acs_v2', omit_write=True)
                episode(tasks['17'], 'acs_v1', fault=True)
                episode(tasks['17'], 'acs_v2', fault=True)
    finally:
        if client:
            client.close()
            accounting_client._client.close()
        ledger.save()
    planned = (len(resume_plan['pending']) if resume_plan
               else len(frozen['task_ids'])*len(ARMS))
    natural = [r for r in rows if not r['fault'] and not r['omit_write']]
    complete = len(natural) == planned
    valid = complete and not ledger.halted and (all(
        r['evaluation_status'] == 'scored' for r in natural) if live
                          else all(r['official_reward'] == 1 for r in natural))
    if not live and not resume_plan:
        negative = next((r for r in rows if r['omit_write']), None)
        faults = {r['arm']: r for r in rows if r['fault']}
        valid = (valid and negative is not None and len(faults) == 2
                 and negative['official_reward'] == 0
                 and faults['acs_v1']['official_reward'] == 0
                 and faults['acs_v2']['official_reward'] == 1)
    summary = {'live': live, 'protocol_sha256': sha256(protocol), 'valid': valid,
               'planned_episodes': planned, 'attempted_episodes': len(natural),
               'guard_spent_cny': str(ledger.spent), 'actual_requests': len(ledger.calls),
               'new_requests':len(ledger.calls)-prefix,
               'halted': ledger.halted,
               'not_started': [{'task': t, 'arm': a} for t in frozen['task_ids'] for a in ARMS
                               if not any(r['task'] == t and r['arm'] == a for r in natural)],
               'arms': {arm: {'episodes': sum(r['arm'] == arm for r in natural),
                              'reward_sum': sum(r['official_reward'] or 0 for r in natural
                                                if r['arm'] == arm
                                                and r['evaluation_status'] == 'scored'),
                              'unscored': sum(r['evaluation_status'] != 'scored' for r in natural
                                              if r['arm'] == arm)} for arm in ARMS},
               'scope': frozen['measurement_scope']}
    if resume_plan:
        summary['not_started'] = [p for p in resume_plan['pending'] if not any(
            r['task'] == p['task'] and r['arm'] == p['arm'] for r in natural)]
        summary['recovery_protocol_sha256'] = sha256(out/'recovery_protocol.json')
        summary['inherited_guard_cny'] = resume_plan['inherited_guard_cny']
        # Export once; SIGKILL recovery uses per-request journal, not this optional export.
        atomic_json(out/'requests.json', {'calls':ledger.calls,
                    'spent_guard_cny':str(ledger.spent),'halted':ledger.halted})
    write_json(out / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--v1-root', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--freeze', action='store_true')
    parser.add_argument('--suite', choices=['pilot', 'formal'], default='pilot')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--preflight', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.freeze:
        with args.protocol.open('x') as fp:
            json.dump(freeze(args.upstream, args.v1_root, suite=args.suite), fp,
                      ensure_ascii=False, indent=2)
    else:
        if not args.out or (args.live and not args.preflight):
            parser.error('--out required; --live also requires --preflight')
        execute(args.protocol, args.out, args.upstream, args.v1_root,
                live=args.live, preflight=args.preflight)
