"""Read-only ancestry audit, then derive an attempt-aware joint official report."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.analyze_tau_v2 import analyze  # noqa: E402
from scripts.tau_resume import build_plan, charged_calls, load_evidence, read  # noqa: E402
from scripts.tau_run_state import atomic_json, digest  # noqa: E402


def combine(source, out):
    source, out = Path(source).resolve(), Path(out).resolve()
    plan = build_plan(source)
    from scripts.tau_resume import validate_destination
    validate_destination(plan,out)
    protocol, _, raw, _, _, _ = load_evidence(source)
    calls, spent = charged_calls(raw,protocol)
    chain, current = [], source
    while True:
        chain.append(current)
        if not (current/'recovery_protocol.json').exists():
            break
        current = Path(read(current/'recovery_protocol.json')['parent'])
    chain.reverse()
    selected, attempts = {}, []
    for directory in chain:
        for path in sorted(directory.glob('*-0-0.json')):
            record = read(path)
            selected[record['task'],record['arm']] = path
        owned = (read(directory/'requests.json')['calls'] if directory == chain[0] else
                 [read(p) for p in sorted((directory/'request_records').glob('*.json'))])
        for line in (directory/'attempts.jsonl').read_text().splitlines():
            record = json.loads(line)
            seqs = [c['sequence'] for c in owned
                    if (c['task'],c['arm']) == (record['task'],record['arm'])]
            attempts.append(record | {'source':str(directory), 'request_sequences':seqs,
                'guard_cny':str(sum((Decimal(calls[i-1]['charge_guard_cny']) for i in seqs),
                                    Decimal(0)))})
    used = set()
    for path in selected.values():
        record = read(path)
        seqs = set(range(record['request_start'],record['request_end']+1))
        if used & seqs:
            raise ValueError('Requests counted in multiple effective attempts')
        used |= seqs
    out.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='tau-joint-') as temporary:
        work = Path(temporary)
        atomic_json(work/'manifest.json',read(chain[0]/'manifest.json'))
        atomic_json(work/'requests.json',{'calls':calls,'spent_guard_cny':str(spent),
                                         'halted':read(source/'summary.json')['halted']})
        for path in selected.values():
            shutil.copyfile(path,work/path.name)
        # Superseded starts are retained separately, not mistaken for an ongoing episode.
        (work/'attempts.jsonl').write_text(''.join(json.dumps({'task':t,'arm':a})+'\n'
                                                 for t,a in selected))
        result = analyze(work)
        for path in [*work.glob('*.csv'),work/'badcase_index.json']:
            shutil.copyfile(path,out/path.name)
    ineffective = [c['sequence'] for c in calls if c['sequence'] not in used]
    result['supplement'] = {
        'sources':[str(p) for p in chain], 'pending':plan['pending'],
        'all_scored':not plan['pending'],
        'historical_unknown_usage':[c['sequence'] for c in calls if not c.get('usage')],
        'ineffective_request_sequences':ineffective,
        'ineffective_guard_cny':str(sum((Decimal(calls[i-1]['charge_guard_cny'])
                                        for i in ineffective),Decimal(0))),
        'all_attempts':attempts,
        'artifacts':{p.name:{'path':str(p),'sha256':digest(p)} for p in selected.values()},
        'ledger_reconciled':sum((Decimal(a['guard_cny']) for a in attempts),Decimal(0))==spent,
    }
    if not result['supplement']['ledger_reconciled']:
        raise ValueError('Attempt totals do not reconcile to inherited ledger')
    result['limitations'] = ('One trial; approved interruption restart task97/V1, old attempt '
                            'and full unknown-usage reservation retained in total cost. '
                            'No scored failures rerun, no V2.1, no pass^3. '
                            'Missing/unscored episodes remain missing, not successes.')
    atomic_json(out/'analysis.json',result)
    atomic_json(out/'source_audit.json',plan)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    report=combine(args.source,args.out)
    print(json.dumps({'arms':report['arms'],'supplement':{
        k:v for k,v in report['supplement'].items() if k not in {'all_attempts','artifacts'}}},
        ensure_ascii=False,indent=2))
