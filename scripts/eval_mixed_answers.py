"""Offline paired development probe; fixed decisions, immutable SQL snapshot, no network."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import socket
import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


def run(runtime, snapshot, out, *, guard=True):
    sys.path[:0]=[str(runtime/'src'),str(runtime)]
    os.environ['GRADIO_ANALYTICS_ENABLED']='False'
    from tests.conftest import ScriptedLLM, ScriptedResponse

    from acs.chat_service import ChatService
    from acs.config import Settings
    from acs.cs_tools import ToolRegistry
    from acs.service_agent import CustomerServiceAgent
    spec=importlib.util.spec_from_file_location('tests.frozen_mixed',
                                               ROOT/'tests/test_mixed_write_answers.py')
    frozen=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frozen)
    out.mkdir(parents=True,exist_ok=False)
    def digest(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()
    protocol={'paid':False,'guard':guard,'runtime_root':str(runtime),
              'fixture_sha256':digest(ROOT/'tests/test_mixed_write_answers.py'),
              'probe_sha256':digest(Path(__file__)),
              'docs_sha256':{p.name:digest(p) for p in sorted((ROOT/'data/docs').glob('*.md'))},
              'snapshot_sha256':digest(snapshot),
              'code_sha256':{str(p.relative_to(runtime)):digest(p)
                  for p in sorted((runtime/'src/acs').glob('*.py'))},
              'grading':'v1: required authoritative facts + actual task state + no false '
                        'completion frames + answered; literal development contract, no judge',
              'cases':[c[0]+'-'+str(p) for c in frozen.CASES for p in [True,False]]}
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    def no_network(*args,**kwargs):
        raise AssertionError('Network prohibited in the mixed-answer probe')
    records=[]
    with patch.object(socket.socket,'connect',no_network),TemporaryDirectory() as temporary:
        for group,operation,reader in frozen.CASES:
            for positive in [True,False]:
                case_id=group+'-'+str(positive)
                db=Path(temporary)/(case_id+'.db')
                with sqlite3.connect(db) as conn:
                    conn.executescript(snapshot.read_text())
                settings=Settings(_env_file=None,db_path=db,docs_dir=ROOT/'data/docs',
                                  index_dir=Path(temporary)/'index',embedding_model='fake',
                                  embedding_dim=8,relevance_calibration_path=None)
                started=perf_counter()
                frames,store,agent,required,question=frozen.exercise(
                    settings,group,operation,reader,positive,enabled=guard)
                elapsed=(perf_counter()-started)*1000
                answer=frames[-1].result.answer
                supported=bool(required)
                if '7 天' in required:
                    required+=['15 天','买家','店铺']
                if reader in {'product','both','multi'} and positive:
                    for pid in ['P001','P002'] if reader=='multi' else ['P001']:
                        required+=agent.registry.db.get_product(pid)['attributes']
                consultation=bool(required) and all(s in answer for s in required)
                false_frames=sum('退款已到账' in f.result.answer for f in frames)
                with store._connect() as conn:
                    requests=conn.execute('SELECT COUNT(*) FROM return_requests').fetchone()[0]
                state=frames[-1].task_view['task'] or {}
                if operation=='cancel':
                    state_ok=state.get('status')==('cancelled' if positive else 'draft')
                elif operation in {'read','preview'}:
                    state_ok=not state
                else:
                    state_ok=(state.get('order_id')=='O10024' if positive and operation!='denied'
                              else not state)
                model_calls=len(agent.llm.calls)
                record={'id':case_id,'question':question,'source_supported':supported,
                    'required':required,'consultation_complete':consultation,
                    'visible_false_completion_frames':false_frames,'business_state_ok':state_ok,
                    'unexpected_applications':requests,'stop_reason':frames[-1].result.stop_reason,
                    'frozen_task_complete':bool(consultation and not false_frames and state_ok
                        and not requests and frames[-1].result.stop_reason=='answered'),
                    'answer':answer,'frames':[f.result.answer for f in frames],
                    'trace':[e.as_dict() for e in frames[-1].result.trace],
                    'scripted_model_calls':model_calls,'elapsed_ms':elapsed,
                    'first_answer_ms':next((f.elapsed_ms for f in frames if f.result.answer),None),
                    'model_answer':next((e.detail.get('model_answer') for e in
                        frames[-1].result.trace if e.type=='write_verification'
                        and e.label=='answer'),
                        answer if frames[-1].result.stop_reason=='answered' else None)}
                model=ScriptedLLM([ScriptedResponse(text='已收到补充咨询。')])
                probe_settings=agent.settings
                probe=CustomerServiceAgent(settings=probe_settings,llm=model,
                                           registry=ToolRegistry(probe_settings))
                list(ChatService(probe_settings,store).run_turn('继续咨询', 'USER001',
                    frames[-1].session_id,agent=probe))
                record['next_input_chars']=len(json.dumps(model.calls[0]['messages'],
                                                         ensure_ascii=False))
                record['context_probe_calls']=1
                (out/(case_id+'.json')).write_text(json.dumps(record,ensure_ascii=False,
                                                              indent=2)+'\n')
                records.append(record)
    summary={'episodes':len(records),'paid_requests':0,'real_tokens':None,
             'consultation_complete':sum(r['consultation_complete'] for r in records),
             'frozen_task_complete':sum(r['frozen_task_complete'] for r in records),
             'visible_false_completion_frames':sum(
                 r['visible_false_completion_frames'] for r in records),
             'unexpected_applications':sum(r['unexpected_applications'] for r in records),
             'scripted_model_calls':sum(r['scripted_model_calls'] for r in records),
             'context_probe_calls':len(records),
             'mean_next_input_chars':sum(r['next_input_chars'] for r in records)/len(records),
             'mean_elapsed_ms':sum(r['elapsed_ms'] for r in records)/len(records),
             'limitation':'Known development cases; scripted usage is not real model evidence.'}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root',type=Path,default=ROOT)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--guard-off',action='store_true')
    args=parser.parse_args()
    run(args.runtime_root.resolve(),args.snapshot.resolve(),args.out.resolve(),
        guard=not args.guard_off)
