"""Replay saved successful decisions in fresh official environments, without model/judge calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT),str(ROOT/'scripts')]
os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'


def replay(source,out, *, include_failed=False):
    from loguru import logger
    from tau2.data_model.message import AssistantMessage, UserMessage
    from tau2.domains.retail.environment import get_environment
    from tau_acs_agent import ACSAgent
    from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall

    from acs.config import Settings

    logger.remove()
    logger.add(sys.stderr,level='ERROR')
    out.mkdir(parents=True,exist_ok=False)
    cases=[(p,json.loads(p.read_text())) for p in sorted(source.glob('*-0-0.json'))]
    if not include_failed:
        cases=[(p,r) for p,r in cases if r['official_reward']==1]
    frozen={'version':'V2.1-offline-replay','paid':False,'source_commit':'462569b','include_failed':include_failed,
            'source_cases':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p,r in cases},
            'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [*sorted((ROOT/'src/acs').glob('*.py')),
                          ROOT/'scripts/tau_acs_agent.py',Path(__file__)]},
            'router':True,'write_receipt_guard':True,'recovery':True,
            'judge':'none','metric':'exact official DB hash and preserved tool decisions; '
                'not an online score or NL reward; captured user turns, no new gold tuning'}
    (out/'protocol.json').write_text(json.dumps(frozen,ensure_ascii=False,indent=2)+'\n')
    def forbidden(*args,**kwargs):
        raise AssertionError('Network forbidden in fixed-decision replay')
    results=[]
    with patch.object(socket.socket,'connect',forbidden),patch.object(
            socket.socket,'connect_ex',forbidden):
        for path,record in cases:
            messages=record.get('active_loop_messages')
            if messages:
                first=next(i for i,m in enumerate(messages) if m['role']=='user')
                turns=messages[first:]
            else:
                turns=record['simulation']['messages']
                first=next(i for i,m in enumerate(turns) if m['role']=='user')
                turns=turns[first:]
            responses=[]
            for message in turns:
                if message['role']!='assistant':
                    continue
                calls=[]
                for c in message.get('tool_calls') or []:
                    fn=c.get('function',c)
                    args=fn['arguments']
                    calls.append(ScriptedToolCall(c['id'],fn['name'],
                        args if isinstance(args,str) else json.dumps(args)))
                responses.append(ScriptedResponse(text=message.get('content') or '',
                                                  wants_tools=bool(calls),tool_calls=calls))
            model=ScriptedLLM(responses)
            env=get_environment()
            before_match=env.get_db_hash()==record['before_db_hash']
            adapter=ACSAgent(env.get_tools(),env.get_policy(),normalize_results=True,
                settings=Settings(_env_file=None,workflow_router_enabled=True,
                    write_receipt_guard_enabled=True,runtime_recovery_enabled=True,
                    max_steps=8,max_tool_calls=16,context_max_chars=64000),
                llm_factory=lambda s,model=model:model)
            state=adapter.get_init_state([AssistantMessage.text('Hi! How can I help you today?')])
            finals=[]
            try:
                for i,message in enumerate(turns):
                    if message['role']!='user' or not any(
                            m['role']=='assistant' for m in turns[i+1:]):
                        continue
                    answer,state=adapter.generate_next_message(UserMessage(role='user',
                        content=message['content']),state)
                    while answer.tool_calls:
                        answer,state=adapter.generate_next_message(
                            env.get_response(answer.tool_calls[0]),state)
                    finals.append({'text':answer.content,'raw_data':answer.raw_data})
                result={'artifact':path.name,'task':record['task'],'arm':record['arm'],
                        'before_db_match':before_match,'original_reward':record['official_reward'],
                        'after_db_match':env.get_db_hash()==record['after_db_hash'],
                        'model_decisions':len(model.calls),'expected_decisions':len(responses),
                        'tool_sequence_match':[p['tool'] for p in
                                               state.agent.registry.protocol_records]
                            == [c['name'] for m in record['simulation']['messages']
                                for c in m.get('tool_calls') or []], 'finals':finals}
            except Exception as exc:
                result={'artifact':path.name,'after_db_match':False,
                        'error_type':type(exc).__name__,'error':str(exc),'finals':finals}
            (out/path.name).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
            results.append(result)
            print(path.name,result['after_db_match'],flush=True)
    summary={'cases':len(results),'db_matches':sum(r['after_db_match'] for r in results),
             'real_requests':0,'official_rewards_computed':0,
             'failures':[r['artifact'] for r in results if not r['after_db_match']]}
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--include-failed',action='store_true')
    args=parser.parse_args()
    replay(args.source,args.out,include_failed=args.include_failed)
