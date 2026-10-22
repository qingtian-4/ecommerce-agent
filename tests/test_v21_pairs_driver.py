"""Stale or unsuccessful offline proofs must never reach the paid worker."""
import argparse
import json
from pathlib import Path

import pytest
from scripts.eval_v21_pairs import manifest, run


@pytest.mark.parametrize('tamper',['cases','preflight_complete'])
def test_paid_driver_rejects_mismatched_proof_before_output_or_network(tmp_path,tamper):
    root=Path(__file__).resolve().parents[1]
    cases=tmp_path/'cases.json'
    cases.write_text('{"cases": []}')
    snapshot=tmp_path/'snapshot.sql'
    snapshot.write_text('SELECT 1;')
    proof=tmp_path/'proof'
    proof.mkdir()
    a=argparse.Namespace(cases=cases,snapshot=snapshot,v2_root=root,v21_root=root,
        out=tmp_path/'paid',live=True,worker=False,preflight=proof)
    (proof/'manifest.json').write_text(json.dumps(manifest(a)))
    (proof/'summary.json').write_text(json.dumps({'preflight_complete':True,'live':False}))
    if tamper=='cases':
        cases.write_text('{"cases": [{"id":"changed"}]}')
    else:
        (proof/'summary.json').write_text(json.dumps({'preflight_complete':False,'live':False}))
    with pytest.raises(ValueError,match='offline rehearsal'):
        run(a)
    assert not a.out.exists()


def test_worker_infrastructure_error_cannot_authorize_live(tmp_path,monkeypatch):
    from scripts import eval_v21_pairs

    root=Path(__file__).resolve().parents[1]
    cases=tmp_path/'cases.json'
    cases.write_text(json.dumps({'cases':[{'id':'x','turns':[{'query':'q'}]}]}))
    snapshot=tmp_path/'snapshot.sql'
    snapshot.write_text('SELECT 1;')
    a=argparse.Namespace(cases=cases,snapshot=snapshot,v2_root=root,v21_root=root,
        out=tmp_path/'dry',live=False,worker=False,env_file=tmp_path/'unused')
    def failed_worker(command,**kwargs):
        arm=command[command.index('--arm')+1]
        (a.out/(arm+'-x.json')).write_text(json.dumps({'error':{'type':'OSError'},'turns':[]}))
        return argparse.Namespace(returncode=0)
    monkeypatch.setattr(eval_v21_pairs.subprocess,'run',failed_worker)
    with pytest.raises(RuntimeError,match='protocol'):
        run(a)
    assert not (a.out/'summary.json').exists()
