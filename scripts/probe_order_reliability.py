"""Three paired offline replays per frozen task, preserving every outcome."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import socket
import sys
from dataclasses import asdict
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from tests.conftest import ScriptedLLM, ScriptedResponse  # noqa: E402
from tests.test_order_reliability import FROZEN, audit, run_case, sql_truth  # noqa: E402

from acs.chat_service import ChatService  # noqa: E402
from acs.config import Settings  # noqa: E402
from acs.cs_tools import ToolRegistry  # noqa: E402
from acs.scenarios import build_scenario, scenario_settings  # noqa: E402
from acs.service_agent import CustomerServiceAgent  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402


def run(output: Path):
    output.mkdir(parents=True, exist_ok=False)
    records = []
    freeze = json.loads((ROOT / 'results/reliability_v1_offline/freeze.json').read_text())
    assert hashlib.sha256((ROOT / 'data/eval/reliability_v1.json').read_bytes()).hexdigest() == (
        freeze['cases_sha256'])
    def denied(*args, **kwargs):
        raise AssertionError('No network permitted in reliability probe')
    with TemporaryDirectory(prefix='acs-reliability-') as temporary, patch.object(
        socket.socket, 'connect', denied,
    ), patch.object(socket.socket, 'connect_ex', denied):
        base = Path(temporary) / 'source'
        build_scenario(base)
        for case in FROZEN['cases']:
            for trial in range(FROZEN['paired_repetitions']):
                arms = (False, True) if trial % 2 == 0 else (True, False)
                for enabled in arms:
                    root = Path(temporary) / f'{case["id"]}-{trial}-{enabled}'
                    shutil.copytree(base, root)
                    settings = scenario_settings(Settings(
                        _env_file=None, llm_api_key='', embedding_api_key='',
                    ), root)
                    final, agent, calls = run_case((settings, SessionStore(settings.db_path)),
                                                   enabled, case)
                    truth = sql_truth(settings, 'USER001', case)
                    passed = audit(final, case, truth)
                    events = [e for e in final.result.trace if e.type == 'fact_render']
                    settings = agent.settings
                    observer = CustomerServiceAgent(settings=settings,
                        llm=ScriptedLLM([ScriptedResponse(text='已收到。')]),
                        registry=ToolRegistry(settings))
                    followup = list(ChatService(settings).run_turn(
                        '谢谢。', 'USER001', final.session_id, agent=observer,
                    ))[-1]
                    record = {'case': case['id'], 'mode': case['mode'], 'trial': trial,
                              'enabled': enabled, 'passed': passed, 'answer': final.result.answer,
                              'truth': truth, 'model_calls': calls,
                              'current_input_chars': final.result.totals['model_input_chars'],
                              'next_input_chars': followup.result.totals['model_input_chars'][0],
                              'elapsed_ms': final.elapsed_ms,
                              'check_ms': final.result.totals.get('order_reliability_ms'),
                              'renders': [asdict(e) for e in events],
                              'result': asdict(final.result), 'business_unchanged': True,
                              'synchronized': True,
                              'usage_kind': 'fake_placeholder_not_real_tokens'}
                    records.append(record)
                    with (output / 'raw.jsonl').open('a') as stream:
                        stream.write(json.dumps(record, ensure_ascii=False) + '\n')
                print(case['id'], trial + 1, flush=True)
    summary = {'cases': len(FROZEN['cases']), 'trials_per_arm': FROZEN['paired_repetitions'],
               'conversations': len(records), 'paid_calls': 0, 'real_tokens': None,
               'arms': {}, 'pairs': []}
    for enabled in (False, True):
        arm = [r for r in records if r['enabled'] == enabled]
        times = sorted(r['elapsed_ms'] for r in arm)
        checks = sorted(r['check_ms'] for r in arm if r['check_ms'] is not None)
        summary['arms'][str(enabled)] = {
            'passed': sum(r['passed'] for r in arm), 'n': len(arm),
            'by_mode': {m: {'passed': sum(r['passed'] for r in arm if r['mode'] == m),
                            'n': sum(r['mode'] == m for r in arm)}
                        for m in ('candidates', 'aggregate', 'passthrough')},
            'elapsed_ms': {'p50': median(times), 'p95': times[int(.95 * (len(times)-1))],
                           'max': max(times)},
            'check_ms': {'p50': median(checks), 'p95': checks[int(.95 * (len(checks)-1))],
                         'max': max(checks)} if checks else None}
    for case in FROZEN['cases']:
        off = [r for r in records if r['case'] == case['id'] and not r['enabled']]
        on = [r for r in records if r['case'] == case['id'] and r['enabled']]
        summary['pairs'].append({
            'case': case['id'], 'mode': case['mode'],
            'off_passed': sum(r['passed'] for r in off), 'on_passed': sum(r['passed'] for r in on),
            'new_failures': sum(a['passed'] and not b['passed']
                                for a, b in zip(off, on, strict=True)),
            'extra_model_calls': [b['model_calls']-a['model_calls']
                                  for a, b in zip(off, on, strict=True)],
            'current_input_chars_delta': [[y-x for x, y in zip(a['current_input_chars'],
                                                             b['current_input_chars'], strict=True)]
                                         for a, b in zip(off, on, strict=True)],
            'next_input_chars_delta': [b['next_input_chars']-a['next_input_chars']
                                      for a, b in zip(off, on, strict=True)]})
    summary['protected_files_unchanged'] = all(
        hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected
        for path, expected in freeze['protected_sha256'].items())
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(summary['arms'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out)
