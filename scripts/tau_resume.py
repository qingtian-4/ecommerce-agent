"""Read-only audit and separately approved continuation of a frozen τ formal run."""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tau_run_state import atomic_json, digest, process_token, run_lock


def read(path):
    return json.loads(Path(path).read_text())


def charged_calls(calls, protocol):
    total = Decimal(0)
    settled = []
    for sequence, raw in enumerate(calls, 1):
        call = dict(raw)
        if call.get('sequence') != sequence:
            raise ValueError('Request sequence gap or duplicate')
        reserve = Decimal(call['reserved_cny'])
        if not reserve.is_finite() or reserve <= 0:
            raise ValueError('Invalid reservation')
        usage = call.get('usage')
        if usage:
            incoming, outgoing = usage['prompt_tokens'], usage['completion_tokens']
            if (type(incoming) is not int or incoming <= 0 or type(outgoing) is not int
                    or outgoing < 0 or usage['total_tokens'] != incoming+outgoing):
                raise ValueError('Invalid usage')
            charge = (incoming*Decimal(protocol['guard_input_output_cny_per_million'][0])
                      + outgoing*Decimal(protocol['guard_input_output_cny_per_million'][1]))/1000000
            if Decimal(call['charge_guard_cny']) != charge or charge > reserve:
                raise ValueError('Usage/charge/reservation mismatch')
        else:
            if ('charge_guard_cny' in call
                    and Decimal(call['charge_guard_cny']) != reserve):
                raise ValueError('Unknown usage must reserve full cost')
            charge = reserve
            call.update(charge_guard_cny=str(reserve), usage_missing=True,
                        recovery_settlement='full_reservation_no_usage')
        total += charge
        settled.append(call)
    if total > Decimal(protocol['budget_cny']):
        raise ValueError('Budget exhausted or invalid ledger')
    return settled, total


def load_evidence(source, seen=None):
    source = Path(source).resolve()
    seen = set() if seen is None else seen
    if source in seen:
        raise ValueError('Cyclic recovery ancestry')
    seen.add(source)
    completed, attempts, files = {}, [], {}
    supplement = source/'recovery_protocol.json'
    if supplement.exists():
        recovery = read(supplement)
        parent = Path(recovery['parent'])
        protocol, protocol_path, calls, completed, attempts, files = load_evidence(parent, seen)
        for path, expected in recovery['source_files'].items():
            if digest(path) != expected:
                raise ValueError('Parent evidence changed: '+path)
        status = read(source/'run_status.json') if (source/'run_status.json').exists() else {}
        if status.get('state') == 'running' and status.get('process_token') and (
                process_token(status['pid']) == status['process_token']):
            raise ValueError('Source continuation is still running')
        for path in sorted((source/'request_records').glob('*.json')):
            record = read(path)
            if path.stem != f"{record['sequence']:08d}":
                raise ValueError('Request filename/sequence mismatch')
            calls.append(record)
        if (source/'ledger_state.json').exists():
            cached = read(source/'ledger_state.json')
            if cached['last_sequence'] > len(calls):
                raise ValueError('Request journal missing records')
            _, conservative = charged_calls(calls, protocol)
            if Decimal(cached['spent_guard_cny']) > conservative:
                raise ValueError('Cached balance exceeds recoverable ledger')
        stored_records = {p.name:digest(p) for p in source.glob('*-0-0.json')}
        files[str(supplement)] = digest(supplement)
        files.update({str(p):digest(p) for p in (source/'request_records').glob('*.json')})
    else:
        interruption = read(source.parent/'INTERRUPTION.json')
        protocol_path = source.parent/'protocol_v6.json'
        protocol = read(protocol_path)
        if digest(protocol_path) != interruption['protocol_sha256']:
            raise ValueError('Original protocol hash mismatch')
        if digest(source/'requests.json') != interruption['raw_ledger_sha256']:
            raise ValueError('Original ledger hash mismatch')
        ledger = read(source/'requests.json')
        calls = ledger['calls']
        expected_spent = sum((Decimal(c.get('charge_guard_cny','0')) for c in calls),Decimal(0))
        if expected_spent != Decimal(ledger['spent_guard_cny']):
            raise ValueError('Original balance mismatch')
        stored_records = interruption['completed_sha256']
        files.update({str(p):digest(p) for p in [protocol_path,source/'requests.json',
                                               source.parent/'INTERRUPTION.json']})
    manifest = read(source/'manifest.json')
    if manifest['protocol_sha256'] != digest(protocol_path):
        raise ValueError('Manifest protocol mismatch')
    if not manifest['live']:
        raise ValueError('Offline preflight cannot become a paid evidence parent')
    for key in ['task_ids','arms','budget_cny']:
        if manifest[key] != protocol[key]:
            raise ValueError('Manifest condition mismatch: '+key)
    files[str(source/'manifest.json')] = digest(source/'manifest.json')
    paths = {p.name:p for p in source.glob('*-0-0.json')}
    if set(paths) != set(stored_records):
        raise ValueError('Unexpected or missing episode artifact')
    planned = {(t,a) for t in protocol['task_ids'] for a in protocol['arms']}
    local_keys = set()
    for name,path in paths.items():
        if digest(path) != stored_records[name]:
            raise ValueError('Episode hash mismatch: '+name)
        record = read(path)
        key = (record['task'], record['arm'])
        if key not in planned or key in local_keys or key in completed:
            raise ValueError('Unknown or duplicate episode: '+name)
        if name != f'{key[1]}-{key[0]}-0-0.json':
            raise ValueError('Episode identity/file mismatch')
        local_keys.add(key)
        if record['protocol_sha256'] != digest(protocol_path):
            raise ValueError('Episode protocol mismatch')
        if record['evaluation_status'] == 'scored':
            reward = record['official_reward']
            if reward not in (0,1) or record['simulation']['reward_info']['reward'] != reward:
                raise ValueError('Reward mismatch')
            if not (1 <= record['request_start'] <= record['request_end'] <= len(calls)):
                raise ValueError('Invalid completed request range')
            for call in calls[record['request_start']-1:record['request_end']]:
                if (call.get('task'),call.get('arm')) != key or not call.get('usage'):
                    raise ValueError('Episode request ownership/usage mismatch')
            completed[key] = {'path':str(path),'sha256':digest(path)}
        elif record['official_reward'] is not None:
            raise ValueError('Unscored result has reward')
        files[str(path)] = digest(path)
    attempt_path = source/'attempts.jsonl'
    local_attempts = ([json.loads(line) for line in attempt_path.read_text().splitlines()]
                      if attempt_path.exists() else [])
    keys = [(r['task'],r['arm']) for r in local_attempts]
    if len(keys) != len(set(keys)) or any(k not in planned for k in keys):
        raise ValueError('Duplicate or unknown start record')
    if not local_keys.issubset(keys):
        raise ValueError('Result missing start record')
    if any((c.get('task'), c.get('arm')) not in keys for c in calls
           if c.get('sequence', 0) > (len(calls) - len(list(
               (source/'request_records').glob('*.json'))))):
        raise ValueError('Request missing start record')
    if not supplement.exists() and any((c.get('task'), c.get('arm')) not in keys
                                       for c in calls):
        raise ValueError('Original request missing start record')
    attempts.extend(keys)
    if attempt_path.exists():
        files[str(attempt_path)] = digest(attempt_path)
    return protocol, protocol_path, calls, completed, attempts, files


def build_plan(source):
    protocol, protocol_path, raw, completed, attempts, files = load_evidence(source)
    calls, spent = charged_calls(raw, protocol)
    pending = []
    for i,task in enumerate(protocol['task_ids']):
        arms = protocol['arms']
        for arm in arms[i%len(arms):]+arms[:i%len(arms)]:
            if (task,arm) not in completed:
                pending.append({'task':task,'arm':arm,'state':
                                'started_unsettled' if (task,arm) in attempts else 'not_started'})
    root = Path(protocol_path).parent/'live'
    return {'format':1,'variant':'original-v2-supplement-not-v21',
            'parent':str(Path(source).resolve()),'original_protocol':str(protocol_path),
            'original_protocol_sha256':digest(protocol_path),'source_files':files,
            'completed':[{'task':t,'arm':a} for t,a in sorted(completed)],'pending':pending,
            'completed_artifacts':list(completed.values()),
            'inherited_guard_cny':str(spent),'limit_cny':protocol['budget_cny'],
            'remaining_cny':str(Decimal(protocol['budget_cny'])-spent),
            'next_request_sequence':len(calls)+1,
            'attempt_numbers':{f'{t}/{a}':attempts.count((t,a))+1
                               for t,a in {(p['task'],p['arm']) for p in pending}},
            'unknown_usage_sequences':[c['sequence'] for c in calls if not c.get('usage')],
            'policy':'Human approval required for unfinished episodes in fresh environments; '
                     'retain unknown request full reservation; never replay a tool in old state.',
            'lock_file':str(Path.home()/'.local/share/ecommerce-agent/tau-cohorts'
                            /f'{digest(protocol_path)}.lock'),
            'cohort_state':str(Path.home()/'.local/share/ecommerce-agent/tau-cohorts'
                               /f'{digest(protocol_path)}.json'),
            'root':str(root),
            'execution_sources':{str(p):digest(p) for p in [
                Path(__file__).resolve(),Path(__file__).with_name('tau_run_state.py').resolve(),
                Path(__file__).with_name('eval_tau_v2.py').resolve(),
                Path(__file__).with_name('tau_supervise.py').resolve()]}}


def validate_destination(plan, destination):
    destination=Path(destination).resolve()
    roots={Path(plan['original_protocol']).parent,Path(plan['parent'])}
    roots.update(Path(path).parent for path in plan['source_files']
                 if Path(path).name in {'manifest.json','recovery_protocol.json'})
    if any(destination == root or root in destination.parents for root in roots):
        raise ValueError('Parent experiment evidence is read-only; use a separate destination')


def claim_continuation(plan, out):
    """Under the experiment lock, make sequential attempts inherit the latest ledger too."""
    state_path=Path(plan['cohort_state'])
    if state_path.exists():
        previous=read(state_path)
        if previous['latest'] != plan['parent']:
            raise ValueError('Must resume from latest supplement, not reuse an older budget')
    atomic_json(state_path, {'latest':str(Path(out).resolve()),
                            'parent':plan['parent'],
                            'original_protocol_sha256':plan['original_protocol_sha256']})


def inherited_ledger(plan):
    protocol, _, raw, _, _, _ = load_evidence(plan['parent'])
    return charged_calls(raw, protocol)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume-from',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--upstream',type=Path)
    parser.add_argument('--v1-root',type=Path)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--dry-run',action='store_true')
    modes.add_argument('--live',action='store_true')
    parser.add_argument('--approval-file',type=Path)
    parser.add_argument('--preflight',type=Path)
    args=parser.parse_args()
    plan=build_plan(args.resume_from)
    validate_destination(plan,args.out)
    with run_lock(plan['lock_file']):
        if build_plan(args.resume_from)!=plan:
            raise ValueError('Evidence changed during audit')
        if args.live:
            if not args.approval_file or not args.preflight:
                raise ValueError('Paid continuation needs explicit approval and matching dry run')
            proof=read(args.preflight/'recovery_protocol.json')
            if (proof!=plan or not read(args.preflight/'summary.json')['valid']
                    or read(args.preflight/'summary.json')['live']):
                raise ValueError('Recovery protocol changed after dry run')
            approval=read(args.approval_file)
            if (approval.get('approved') is not True
                    or approval.get('recovery_protocol_sha256')!=digest(
                        args.preflight/'recovery_protocol.json')
                    or approval.get('limit_cny')!=plan['limit_cny']
                    or approval.get('unknown_usage_sequences')!=plan['unknown_usage_sequences']
                    or approval.get('restart')!=[r for r in plan['pending']
                                                if r['state']=='started_unsettled']):
                raise ValueError('Approval does not match this exact restart and budget policy')
        if not args.dry_run and not args.live:
            args.out.mkdir(parents=True,exist_ok=False)
            atomic_json(args.out/'recovery_protocol.json',plan)
            print(json.dumps(plan,ensure_ascii=False,indent=2))
            return
        if not args.upstream or not args.v1_root:
            raise ValueError('--upstream and --v1-root required for execution')
        from scripts.eval_tau_v2 import execute
        execute(Path(plan['original_protocol']),args.out,args.upstream,args.v1_root,
                live=args.live,preflight=args.preflight,resume_plan=plan)
        if not read(args.out/'summary.json')['valid']:
            raise SystemExit(2)


if __name__=='__main__':
    main()
