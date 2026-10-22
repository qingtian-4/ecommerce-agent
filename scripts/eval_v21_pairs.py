"""One separately budgeted natural V2/V2.1 pair set, isolated source processes."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
from contextlib import nullcontext
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(a):
    return {'cases_sha256':digest(a.cases),'snapshot_sha256':digest(a.snapshot),
        'driver_sha256':digest(Path(__file__)),
        'docs_sha256':{p.name:digest(p) for p in sorted((ROOT/'data/docs').glob('*.md'))},
        'paid_cap_cny':'1','guard_rates':[4,16],
        'max_output':512,'temperature':0,'model':'deepseek-flash','provider':'api.deepseek.com',
        'trials':1,'seed':None,'judge':'no paid judge; frozen contract source review',
        'recorder_helpers':{arm:{name:digest(runtime/'scripts'/name)
            for name in ['eval_delivery_live.py','eval_p1_live.py']}
            for arm,runtime in [('v2',a.v2_root),('v21',a.v21_root)]},
        'sources':{arm:{str(p.relative_to(runtime)):digest(p)
            for p in sorted((runtime/'src/acs').glob('*.py'))}
            for arm,runtime in [('v2',a.v2_root),('v21',a.v21_root)]},
        'settings':{'router':True,'recovery':True,'progress':False,'four_protections':False,
                    'write_guard':'v21 true; unavailable in original V2',
                    'max_steps':4,'max_tools':6,'max_requests_per_turn':5,
                    'provider_timeout_s':45,'turn_timeout_s':90,'knowledge':'documents'},
        'classification':'Authored development tasks, one trial; no general causal claim.'}


def worker(a):
    runtime=a.v2_root if a.arm=='v2' else a.v21_root
    sys.path[:0]=[str(runtime/'src'),str(runtime),str(ROOT)]
    from scripts.eval_delivery_live import RecordedLLM
    from scripts.eval_p1_live import Ledger
    from scripts.tau_run_state import atomic_json
    from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall

    from acs.chat_service import ChatService
    from acs.config import Settings
    from acs.cs_tools import ToolRegistry
    from acs.llm import LLMClient
    from acs.service_agent import CustomerServiceAgent
    from acs.sessions import SessionStore

    ledger=Ledger(a.out/'requests.json',limit=Decimal('1'),max_requests=100,
                  input_guard=Decimal('4'),output_guard=Decimal('16'))
    if ledger.path.exists():
        previous=json.loads(ledger.path.read_text())
        ledger.calls=previous['calls']
        ledger.spent=Decimal(previous['spent_guard_cny'])
        ledger.halted=previous['halted']
    if ledger.halted or any('charge_guard_cny' not in c for c in ledger.calls):
        raise ValueError('Unsettled/halted ledger; no automatic resume')
    ledger.save=lambda:atomic_json(ledger.path,{'calls':ledger.calls,
        'spent_guard_cny':str(ledger.spent),'halted':ledger.halted})
    original_begin=ledger.begin
    def begin(kwargs):
        record=original_begin(kwargs)
        record.update(arm=a.arm,case=a.case,role='agent')
        ledger.save()
        return record
    ledger.begin=begin
    original=Settings(_env_file=a.env_file if a.live else None,llm_api_key='') if not a.live else \
        Settings(_env_file=a.env_file)
    if a.live and (not original.has_llm_key or original.llm_model!='deepseek-flash'
                  or urlsplit(original.llm_base_url).hostname!='api.deepseek.com'):
        raise ValueError('Only funded official DeepSeek Flash is allowed')
    case=next(c for c in json.loads(a.cases.read_text())['cases'] if c['id']==a.case)
    record={'case':a.case,'arm':a.arm,'live':a.live,'turns':[],
            'request_start':len(ledger.calls)+1,'started_utc':datetime.now(UTC).isoformat()}
    def denied(*args,**kwargs):
        raise AssertionError('Unexpected network/non-stream/embedding request')
    started=perf_counter()
    with TemporaryDirectory(prefix='v21-pair-') as temp, \
            patch.object(LLMClient,'chat',denied),patch.object(LLMClient,'embed',denied), \
            patch.object(LLMClient,'rerank',denied), \
            (nullcontext() if a.live else patch.object(socket.socket,'connect',denied)):
        db=Path(temp)/'business.db'
        with sqlite3.connect(db) as conn:
            conn.executescript(a.snapshot.read_text())
        common=Settings(_env_file=None,llm_api_key=original.llm_api_key,
            llm_base_url=original.llm_base_url,llm_model=original.llm_model)
        settings=common.model_copy(update={'db_path':db,'docs_dir':ROOT/'data/docs',
            'index_dir':Path(temp)/'index','embedding_api_key':'','reranker_enabled':False,
            'knowledge_mode':'documents','write_receipt_guard_enabled':True,
            'workflow_router_enabled':True,'runtime_recovery_enabled':True,
            'execution_progress_enabled':False,'return_state_check_enabled':False,
            'order_candidate_render_enabled':False,'order_constraint_enabled':False,
            'order_aggregation_enabled':False,'llm_max_output_tokens':512,
            'llm_temperature':0,'llm_thinking':False,'llm_timeout_s':45,
            'max_steps':4,'max_api_requests':5,'max_tool_calls':6,'turn_timeout_s':90})
        sid=SessionStore(db).create('USER001')
        try:
            for turn in case['turns']:
                if ledger.halted:
                    break
                model=RecordedLLM(settings,ledger) if a.live else ScriptedLLM([
                    ScriptedResponse(wants_tools=True,tool_calls=[ScriptedToolCall(
                        str(i),c['name'],json.dumps(c['args']))
                        for i,c in enumerate(turn['tools'])]),
                    ScriptedResponse(text=turn['script_answer'])])
                agent=CustomerServiceAgent(settings=settings,llm=model,registry=ToolRegistry(settings))
                frames=list(ChatService(settings,SessionStore(db)).run_turn(
                    turn['query'],'USER001',sid,agent=agent))
                record['turns'].append({'query':turn['query'],'result':asdict(frames[-1].result),
                    'frames':[{'answer':f.result.answer,'elapsed_ms':f.elapsed_ms} for f in frames],
                    'task':frames[-1].task_view,
                    'synchronized':SessionStore(db).load(sid,'USER001')['messages'][-1]['content']
                                   ==frames[-1].result.answer})
                atomic_json(a.out/(a.arm+'-'+a.case+'.json'),record)
        except Exception as exc:
            record['error']={'type':type(exc).__name__,'message':str(exc)}
        with sqlite3.connect(db) as conn:
            record['counts']={t:conn.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]
                              for t in ['return_requests','return_reviews','return_audit']}
        record.update(elapsed_s=perf_counter()-started,request_end=len(ledger.calls))
        atomic_json(a.out/(a.arm+'-'+a.case+'.json'),record)
        ledger.save()


def run(a):
    if a.worker:
        worker(a)
        return
    frozen=manifest(a)
    if a.live:
        proof=json.loads((a.preflight/'summary.json').read_text())
        if not proof['preflight_complete'] or proof['live'] or json.loads(
                (a.preflight/'manifest.json').read_text())!=frozen:
            raise ValueError('Matching complete offline rehearsal required')
    a.out.mkdir(parents=True,exist_ok=False)
    lock=(a.out/'run.lock').open('w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    (a.out/'manifest.json').write_text(json.dumps(frozen,ensure_ascii=False,indent=2)+'\n')
    if a.live:
        (a.out/'authorization.json').write_text(json.dumps({
            'approved':True,'source':'User explicitly authorized independent 1 CNY',
            'cap_cny':'1','not_shared_with_tau':True},indent=2)+'\n')
    done=[]
    for i,case in enumerate(json.loads(a.cases.read_text())['cases']):
        for arm in (['v2','v21'] if i%2==0 else ['v21','v2']):
            if (a.out/'requests.json').exists() and json.loads(
                    (a.out/'requests.json').read_text())['halted']:
                break
            cmd=[sys.executable,str(Path(__file__).resolve()),'--worker','--arm',arm,
                '--case',case['id'],'--out',str(a.out),'--cases',str(a.cases),
                '--snapshot',str(a.snapshot),'--v2-root',str(a.v2_root),
                '--v21-root',str(a.v21_root),'--env-file',str(a.env_file)]
            if a.live:
                cmd.append('--live')
            with (a.out/(arm+'-'+case['id']+'.log')).open('w') as log:
                result=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,env=os.environ.copy())
            if result.returncode:
                raise RuntimeError('Worker failed; stop without automatic paid retry')
            episode=json.loads((a.out/(arm+'-'+case['id']+'.json')).read_text())
            if (episode.get('error') or len(episode['turns'])!=len(case['turns'])
                    or not all(t['synchronized'] for t in episode['turns'])
                    or any(episode['counts'].values())
                    or (not a.live and any(t['result']['stop_reason']!='answered'
                                            for t in episode['turns']))):
                raise RuntimeError('Worker protocol error; preserve evidence and stop')
            done.append(arm+'-'+case['id'])
    (a.out/'summary.json').write_text(json.dumps({'live':a.live,'attempted':done,
        'preflight_complete':not a.live and len(done)==16,'planned':16,
        'completion_is_not_scored_success':True},indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['cases','snapshot','v2-root','v21-root','out','preflight','env-file']:
        p.add_argument('--'+name,type=Path)
    p.add_argument('--live',action='store_true')
    p.add_argument('--worker',action='store_true')
    p.add_argument('--case')
    p.add_argument('--arm',choices=['v2','v21'])
    a=p.parse_args()
    for name in ['cases','snapshot','v2_root','v21_root','out']:
        setattr(a,name,getattr(a,name).resolve())
    run(a)
