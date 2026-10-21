"""Alternate frozen/current ChatService under identical offline SQLite conditions."""
import argparse
import json
import shutil
import socket
import statistics
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall  # noqa: E402

from acs.chat_service import ChatService  # noqa: E402
from acs.config import Settings  # noqa: E402
from acs.cs_tools import ToolRegistry  # noqa: E402
from acs.scenarios import build_scenario, scenario_settings  # noqa: E402
from acs.service_agent import CustomerServiceAgent  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402


def run(out, samples):
    out.mkdir(parents=True, exist_ok=False)
    old = ModuleType('acs.frozen_chat_service')
    sys.modules[old.__name__] = old
    source = subprocess.check_output(['git', 'show', '1014bcb:src/acs/chat_service.py'], text=True)
    exec(compile(source, '1014bcb/chat_service.py', 'exec'), old.__dict__)
    rows = []
    with TemporaryDirectory() as directory, patch.object(
        socket.socket, 'connect', side_effect=AssertionError('no network'),
    ):
        root = Path(directory)
        build_scenario(root / 'base')
        for kind in ['read', 'write']:
            for sample in range(samples):
                for candidate in ([False, True] if sample % 2 else [True, False]):
                    local = root / f'{kind}-{sample}-{candidate}'
                    shutil.copytree(root / 'base', local)
                    settings = scenario_settings(Settings(_env_file=None), local).model_copy(
                        update={'execution_progress_enabled': True,
                                'workflow_router_enabled': True, 'runtime_recovery_enabled': True},
                    )
                    calls = ([ScriptedToolCall('order', 'get_order', '{"order_id":"S20017"}'),
                              ScriptedToolCall('ship', 'get_logistics', '{"order_id":"S20008"}')]
                             if kind == 'read' else [ScriptedToolCall(
                                 'write', 'update_return_task', '{"order_id":"S20017"}',
                             )])
                    model = ScriptedLLM([ScriptedResponse(wants_tools=True, tool_calls=calls),
                                         ScriptedResponse(text='处理结束，尚未提交申请')])
                    store, writes = SessionStore(settings.db_path), []
                    save = store.save_progress

                    def observed(*args, save=save, writes=writes):
                        started = perf_counter()
                        save(*args)
                        writes.append((perf_counter() - started) * 1000)

                    with patch.object(store, 'save_progress', observed):
                        started = perf_counter()
                        service = (ChatService if candidate else old.ChatService)(settings, store)
                        updates = list(service.run_turn(
                            '查订单和物流' if kind == 'read' else '我想退S20017', 'USER001',
                            agent=CustomerServiceAgent(settings=settings, llm=model,
                                                       registry=ToolRegistry(settings)),
                        ))
                        elapsed = (perf_counter() - started) * 1000
                    final = updates[-1]
                    saved = store.load(final.session_id, 'USER001')['last_result']
                    assert saved['answer'] == final.result.answer
                    assert final.result.stop_reason == 'answered'
                    rows.append({'kind': kind, 'sample': sample, 'candidate': candidate,
                                 'elapsed_ms': elapsed, 'checkpoint_count': len(writes),
                                 'checkpoint_ms': sum(writes), 'model_calls': len(model.calls),
                                 'answer': final.result.answer,
                                 'task': final.task_view['task']})
        with store._connect() as conn:
            pragmas = {k: conn.execute(f'PRAGMA {k}').fetchone()[0]
                       for k in ['journal_mode', 'synchronous', 'busy_timeout']}
    summary = {}
    for kind in ['read', 'write']:
        for candidate in [False, True]:
            group = [r for r in rows if r['kind'] == kind and r['candidate'] == candidate]
            times = sorted(r['elapsed_ms'] for r in group)
            summary[f'{kind}/{candidate}'] = {
                'n': len(times), 'p50_ms': statistics.median(times),
                'p95_ms': times[-1], 'max_ms': times[-1],
                'checkpoint_counts': [r['checkpoint_count'] for r in group],
            }
    result = {'rows': rows, 'summary': summary, 'pragmas': pragmas, 'paid_calls': 0,
              'baseline': '1014bcb', 'note': '8 pairs per path; p95 nearest-rank = max at n=8; '
              'not a provider latency or production SLA measurement'}
    (out / 'samples.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=8)
    args = parser.parse_args()
    run(args.out, args.samples)
