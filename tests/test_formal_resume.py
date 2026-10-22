"""Immutable parent evidence, inherited money and process death are recovery boundaries."""
import hashlib
import json
from decimal import Decimal

import pytest
from scripts.tau_resume import build_plan


def dump(path, value):
    path.write_text(json.dumps(value))


@pytest.fixture
def source(tmp_path):
    root = tmp_path/'original'
    live = root/'live'
    live.mkdir(parents=True)
    protocol = {'task_ids':['a','b'],'arms':['official','acs_v1','acs_v2'],
                'budget_cny':'40','global_max_actual_requests':5000,
                'guard_input_output_cny_per_million':[4,16]}
    dump(root/'protocol_v6.json',protocol)
    digest = hashlib.sha256((root/'protocol_v6.json').read_bytes()).hexdigest()
    dump(live/'manifest.json',protocol | {'protocol_sha256':digest,'live':True})
    calls = [{'sequence':1,'arm':'official','task':'a','role':'agent',
              'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15},
              'charge_guard_cny':'0.000120','reserved_cny':'0.1'},
             {'sequence':2,'arm':'acs_v1','task':'a','role':'agent','reserved_cny':'0.2'}]
    dump(live/'requests.json',{'calls':calls,'spent_guard_cny':'0.000120','halted':None})
    result={'task':'a','arm':'official','official_reward':1,'evaluation_status':'scored',
            'protocol_sha256':digest,'request_start':1,'request_end':1,
            'simulation':{'reward_info':{'reward':1}}}
    dump(live/'official-a-0-0.json',result)
    (live/'attempts.jsonl').write_text(''.join(json.dumps({'task':'a','arm':a})+'\n'
                                                for a in ['official','acs_v1']))
    dump(root/'INTERRUPTION.json',{
        'protocol_sha256':digest,
        'raw_ledger_sha256':hashlib.sha256((live/'requests.json').read_bytes()).hexdigest(),
        'completed_sha256':{'official-a-0-0.json':hashlib.sha256(
            (live/'official-a-0-0.json').read_bytes()).hexdigest()}})
    return live


def test_plan_skips_completed_and_keeps_unknown_full_reservation(source):
    plan=build_plan(source)
    assert Decimal(plan['inherited_guard_cny'])==Decimal('0.200120')
    assert Decimal(plan['remaining_cny'])==Decimal('39.799880')
    assert len(plan['pending'])==5
    assert plan['pending'][0]=={'task':'a','arm':'acs_v1','state':'started_unsettled'}
    assert plan['completed']==[{'task':'a','arm':'official'}]
    assert plan['next_request_sequence']==3


@pytest.mark.parametrize('damage',['result','ledger','missing_ledger','duplicate','unregistered'])
def test_corruption_and_unregistered_extra_results_are_rejected(source,damage):
    if damage=='missing_ledger':
        (source/'requests.json').unlink()
    elif damage=='ledger':
        (source/'requests.json').write_text('{}')
    elif damage=='result':
        (source/'official-a-0-0.json').write_text('{}')
    else:
        (source/'extra-0-0.json').write_text((source/'official-a-0-0.json').read_text())
    with pytest.raises((ValueError,FileNotFoundError)):
        build_plan(source)


def test_insufficient_budget_reserves_before_any_request(tmp_path):
    from scripts.eval_tau_v2 import TaggedLedger

    from acs.runtime import BudgetExceeded
    ledger=TaggedLedger(tmp_path/'ledger.json',limit=Decimal('40'))
    ledger.spent=Decimal('39.999999')
    with pytest.raises(BudgetExceeded):
        ledger.begin({'messages':[],'max_tokens':768})
    assert ledger.calls==[]


def test_lock_blocks_another_process_and_releases_after_kill(tmp_path):
    import subprocess
    import sys
    import time

    from scripts.tau_run_state import run_lock
    path=tmp_path/'lock'
    worker=subprocess.Popen([sys.executable,'-c',
        'import time; from scripts.tau_run_state import run_lock; '
        f'ctx=run_lock({str(path)!r}); ctx.__enter__(); print("ready",flush=True); time.sleep(60)'],
        stdout=subprocess.PIPE,text=True)
    try:
        assert worker.stdout.readline().strip()=='ready'
        with pytest.raises(RuntimeError), run_lock(path):
            pass
        worker.kill()
        worker.wait(timeout=5)
        with run_lock(path):
            time.sleep(.001)
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.stdout.close()


@pytest.mark.parametrize('termination',['kill','terminate','result_before_kill','record_before_cache'])
def test_real_process_death_preserves_money_and_completed_result(source,tmp_path,termination):
    import os
    import subprocess
    import sys
    from pathlib import Path
    out=tmp_path/'continuation'
    root=Path(__file__).resolve().parents[1]
    code='''
import json,sys,time
from pathlib import Path
from scripts.tau_resume import build_plan,inherited_ledger
from scripts.tau_run_state import atomic_json,RequestJournal,RunMonitor
from scripts.eval_tau_v2 import TaggedLedger
from acs.llm import Usage
source,out=map(Path,sys.argv[1:3]);out.mkdir()
plan=build_plan(source);atomic_json(out/'recovery_protocol.json',plan)
protocol=json.loads((source/'manifest.json').read_text());atomic_json(out/'manifest.json',protocol)
(out/'attempts.jsonl').write_text(json.dumps({'task':'a','arm':'acs_v1'})+'\\n')
ledger=TaggedLedger(out/'requests.json');ledger.calls,ledger.spent=inherited_ledger(plan)
ledger.journal=RequestJournal(out,len(ledger.calls));ledger.arm='acs_v1';ledger.task='a'
ledger.save()
if sys.argv[3]=='record_before_cache':
    import scripts.tau_run_state as storage
    original=storage.atomic_json
    def paused(path,value):
        if path.name=='ledger_state.json':
            print('ready',flush=True);time.sleep(60)
        original(path,value)
    storage.atomic_json=paused
with RunMonitor(out,lambda:{'request_sequence':len(ledger.calls)}):
    call=ledger.begin({'messages':[],'max_tokens':768})
    if sys.argv[3]=='result_before_kill':
        ledger.complete(call,Usage('model','agent',prompt_tokens=10,completion_tokens=5,total_tokens=15))
        result=json.loads((source/'official-a-0-0.json').read_text())
        result.update(arm='acs_v1',request_start=3,request_end=3)
        atomic_json(out/'acs_v1-a-0-0.json',result)
    print('ready',flush=True)
    time.sleep(60)
'''
    env=os.environ | {'PYTHONPATH':str(root/'src')+os.pathsep+str(root)}
    worker=subprocess.Popen([sys.executable,'-c',code,str(source),str(out),termination],
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
    try:
        assert worker.stdout.readline().strip()=='ready',worker.stderr.read()
        worker.terminate() if termination=='terminate' else worker.kill()
        worker.wait(timeout=10)
        resumed=build_plan(out)
        assert resumed['next_request_sequence']==4
        if termination=='result_before_kill':
            assert {'task':'a','arm':'acs_v1'} in resumed['completed']
            assert all(not (p['task']=='a' and p['arm']=='acs_v1') for p in resumed['pending'])
        else:
            assert Decimal(resumed['inherited_guard_cny'])>Decimal('0.200120')
            assert resumed['unknown_usage_sequences']==[2,3]
        if termination=='terminate':
            assert json.loads((out/'exit.json').read_text())['signal']=='SIGTERM'
        else:
            assert not (out/'exit.json').exists()
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.stdout.close()
        worker.stderr.close()


def test_paid_cohort_cannot_restart_from_same_parent_in_different_output(source,tmp_path):
    from scripts.tau_resume import claim_continuation
    plan=build_plan(source)
    plan['cohort_state']=str(tmp_path/'cohort.json')
    claim_continuation(plan,tmp_path/'first')
    with pytest.raises(ValueError,match='latest'):
        claim_continuation(plan,tmp_path/'second')
    plan['parent']=str((tmp_path/'first').resolve())
    claim_continuation(plan,tmp_path/'second')


@pytest.mark.parametrize('valid',[True,False])
def test_supervisor_records_real_exit_code_and_independent_log(source,tmp_path,valid):
    import subprocess
    import sys
    from pathlib import Path
    status=tmp_path/'supervised.json'
    log=tmp_path/'console.log'
    script=Path(__file__).resolve().parents[1]/'scripts/tau_supervise.py'
    result=subprocess.run([sys.executable,str(script),'--log',str(log),'--status',str(status),
        '--','--resume-from',str(source),
        '--out',str(tmp_path/'plan'),*(['--unknown-argument'] if not valid else [])],
        timeout=20,capture_output=True,text=True)
    recorded=json.loads(status.read_text())
    assert recorded['exit_code']==result.returncode
    assert (result.returncode==0)==valid
    assert log.stat().st_size>0


def test_continuation_cannot_write_inside_original_evidence(source):
    from scripts.tau_resume import validate_destination
    plan=build_plan(source)
    for path in [source/'new',source.parent/'new',source/'console.log']:
        with pytest.raises(ValueError,match='read.only'):
            validate_destination(plan,path)
    validate_destination(plan,source.parent.parent/'independent-supplement')


@pytest.mark.parametrize('approved',[False,True])
def test_live_requires_exact_explicit_approval_before_execution(
    source,tmp_path,monkeypatch,approved,
):
    import sys

    from scripts import eval_tau_v2, tau_resume
    plan=build_plan(source)
    proof=tmp_path/'preflight'
    proof.mkdir()
    dump(proof/'recovery_protocol.json',plan)
    dump(proof/'summary.json',{'valid':True,'live':False})
    approval=tmp_path/'approval.json'
    dump(approval,{'approved':approved,
        'recovery_protocol_sha256':hashlib.sha256(
            (proof/'recovery_protocol.json').read_bytes()).hexdigest(),
        'limit_cny':'40','unknown_usage_sequences':[2],
        'restart':[p for p in plan['pending'] if p['state']=='started_unsettled']})
    calls=[]
    def fake_execute(protocol,out,*args,**kwargs):
        calls.append(kwargs)
        out.mkdir()
        dump(out/'summary.json',{'valid':True})
    monkeypatch.setattr(eval_tau_v2,'execute',fake_execute)
    monkeypatch.setattr(sys,'argv',['tau_resume','--resume-from',str(source),
        '--out',str(tmp_path/'live-attempt'),'--preflight',str(proof),
        '--approval-file',str(approval),'--upstream',str(tmp_path),
        '--v1-root',str(tmp_path),'--live'])
    if approved:
        tau_resume.main()
        assert len(calls)==1 and calls[0]['live']
    else:
        with pytest.raises(ValueError,match='Approval'):
            tau_resume.main()
        assert calls==[]
