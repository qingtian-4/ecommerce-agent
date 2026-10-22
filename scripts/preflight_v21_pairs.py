"""Offline-only rehearsal for separately authorized V2/V2.1 natural-task pairs."""
from __future__ import annotations

import argparse
import json
import socket
import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def run(runtime,cases,snapshot,out):
    sys.path[:0]=[str(runtime/'src'),str(runtime)]
    from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall

    from acs.chat_service import ChatService
    from acs.config import Settings
    from acs.cs_tools import ToolRegistry
    from acs.service_agent import CustomerServiceAgent
    from acs.sessions import SessionStore

    spec=json.loads(cases.read_text())
    out.mkdir(parents=True,exist_ok=False)
    def denied(*args,**kwargs):
        raise AssertionError('Paid/network calls are not authorized by this preflight')
    records=[]
    with patch.object(socket.socket,'connect',denied),TemporaryDirectory() as tmp:
        for case in spec['cases']:
            db=Path(tmp)/(case['id']+'.db')
            with sqlite3.connect(db) as conn:
                conn.executescript(snapshot.read_text())
            settings=Settings(_env_file=None,llm_api_key='',db_path=db,
                docs_dir=Path(__file__).resolve().parents[1]/'data/docs',knowledge_mode='documents',
                write_receipt_guard_enabled=True,workflow_router_enabled=True,
                runtime_recovery_enabled=True,execution_progress_enabled=False)
            store=SessionStore(db)
            sid=store.create('USER001')
            turns=[]
            for turn in case['turns']:
                calls=[ScriptedToolCall(str(i),c['name'],json.dumps(c['args']))
                       for i,c in enumerate(turn['tools'])]
                model=ScriptedLLM([ScriptedResponse(wants_tools=True,tool_calls=calls),
                                   ScriptedResponse(text=turn['script_answer'])])
                agent=CustomerServiceAgent(settings=settings,llm=model,registry=ToolRegistry(settings))
                frames=list(ChatService(settings,SessionStore(db)).run_turn(
                    turn['query'],'USER001',sid,agent=agent))
                saved=store.load(sid,'USER001')['messages'][-1]['content']
                assert saved==frames[-1].result.answer
                turns.append({'query':turn['query'],'answer':saved,
                    'frames':[f.result.answer for f in frames],
                    'trace':[e.as_dict() for e in frames[-1].result.trace],
                    'task':frames[-1].task_view,'scripted_calls':len(model.calls),
                    'synchronized':True,'stop_reason':frames[-1].result.stop_reason})
            with store._connect() as conn:
                assert conn.execute('SELECT COUNT(*) FROM return_requests').fetchone()[0]==0
            records.append({'id':case['id'],'turns':turns})
    (out/'raw.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
    (out/'summary.json').write_text(json.dumps({'episodes':len(records),'paid_requests':0,
        'network_forbidden':True,'runtime':str(runtime),'infrastructure_ok':True,
        'scoring':'scripted wiring and state; not a model success rate'},indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','cases','snapshot','out']:
        parser.add_argument('--'+name,type=Path,required=True)
    a=parser.parse_args()
    run(a.runtime_root.resolve(),a.cases,a.snapshot,a.out)
