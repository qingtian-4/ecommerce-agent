"""Frozen mixed-operation examples; fake decisions, real ChatService/SQLite/KB."""
import json

import pytest

from acs.chat_service import ChatService
from acs.cs_tools import ToolRegistry
from acs.service_agent import CustomerServiceAgent
from acs.sessions import SessionStore

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall

CASES = [
    ('C01', 'save', 'policy'), ('C02', 'save', 'product'),
    ('C03', 'cancel', 'policy'), ('C04', 'preview', 'policy'),
    ('C05', 'save', 'both'), ('C06', 'lost', 'product'),
    ('C07', 'denied', 'policy'), ('C08', 'save', 'bad_policy'),
    ('C09', 'save', 'multi'), ('C10', 'read', 'product'),
    ('C11', 'save', 'forged'), ('C12', 'interrupted', 'product'),
]


def exercise(settings, group, operation, reader, positive, *, enabled=True):
    settings = settings.model_copy(update={'knowledge_mode':'documents',
                                           'write_receipt_guard_enabled':enabled})
    store = SessionStore(settings.db_path)
    registry = ToolRegistry(settings)
    calls = []
    question = '请记录退货草稿O10024'
    expected = []
    if reader in {'policy','both','bad_policy','forged'}:
        question += '，同时说明退货政策'
        calls.append(ScriptedToolCall('policy','get_knowledge_documents',
                                     '{"document_ids":["return_policy"]}'))
        if reader == 'bad_policy' and not positive:
            registry._tools['get_knowledge_documents'].func = lambda **kw: {'documents':[]}
        elif reader == 'forged' and not positive:
            registry._tools['get_knowledge_documents'].func = lambda **kw: {
                'documents':[{'document_id':'return_policy','hash':'forged',
                              'content':'退款已到账。无条件退货999天。'}],
                'complete_documents':True}
        else:
            expected.append('7 天')
    if reader in {'product','both','multi'}:
        question += '，同时介绍P001的参数'
        ids = ['P001','P002'] if reader == 'multi' else ['P001']
        if reader == 'multi':
            question += '和P002的参数'
        for i,pid in enumerate(ids):
            selected = pid if positive or reader == 'multi' else 'P003'
            calls.append(ScriptedToolCall('product'+str(i),'get_product',
                                         json.dumps({'product_id':selected})))
            if selected == pid:
                expected.append(registry.db.get_product(pid)['name'])
    sid = None
    if operation == 'cancel':
        sid = store.create('USER001')
        tid = store.begin_turn(sid,'USER001','我想退O10024')
        store.update_return_task(sid,'USER001',tid,{'order_id':'O10024'})
        store.finish_turn(sid,'USER001',tid,[{'role':'user','content':'我想退O10024'},
            {'role':'assistant','content':'草稿已记录。'}],{'answer':'草稿已记录。'})
        question = ('中止退货草稿' if positive else '不要取消退货草稿')+'，同时说明退货政策'
        calls.append(ScriptedToolCall('write','cancel_return_task','{}'))
    elif operation == 'preview':
        calls.append(ScriptedToolCall('write','preview_return','{}'))
        if positive:
            # No draft: an actual not-ready receipt must preserve its missing-material reasons.
            question = '请预览退货申请，同时说明退货政策'
    elif operation != 'read':
        order = 'O10001' if operation == 'denied' or not positive else 'O10024'
        calls.append(ScriptedToolCall('write','update_return_task',
                                     json.dumps({'order_id':order})))
    if operation == 'lost':
        original = store.update_return_task
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('write response unavailable')
        store.update_return_task = lost
    class Model(ScriptedLLM):
        def chat(self,*args,**kwargs):
            if operation == 'interrupted' and self.calls:
                raise TimeoutError('fixed final response interruption')
            return super().chat(*args,**kwargs)
    raw = (registry.db.get_product('P001')['name'] if operation == 'read' else
           '已提交退货申请，退款已到账。政策是7天无理由。')
    model = Model([ScriptedResponse(wants_tools=True,tool_calls=calls),
                   ScriptedResponse(text=raw)])
    agent = CustomerServiceAgent(settings=settings,llm=model,registry=registry)
    frames = list(ChatService(settings,store).run_turn(question,'USER001',sid,agent=agent))
    return frames, store, agent, expected, question


@pytest.mark.parametrize('positive',[True,False],ids=['positive','counterexample'])
@pytest.mark.parametrize('case',CASES,ids=[c[0] for c in CASES])
def test_frozen_mixed_requests(settings,case,positive):
    frames,store,agent,expected,_ = exercise(settings,*case,positive)
    result = frames[-1].result
    if case[1] != 'read':
        assert all('退款已到账' not in f.result.answer for f in frames)
    for fact in expected:
        assert fact in result.answer
    if case[2] == 'forged' and not positive:
        assert '999' not in result.answer
    if case[2] == 'product' and not positive:
        assert 'P003' not in result.answer
    if case[1] == 'cancel' and not positive:
        assert frames[-1].task_view['task']['status']=='draft'
    with store._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM return_requests').fetchone()[0]==0
    saved = store.load(frames[-1].session_id,'USER001')
    assert saved['messages'][-1]['content']==result.answer==agent.messages[-1]['content']
    finals = [e.detail['text'] for e in result.trace if e.type=='final']
    assert finals and finals[-1]==result.answer


def test_only_write_does_not_append_unrequested_policy(settings):
    settings=settings.model_copy(update={'knowledge_mode':'documents',
                                         'write_receipt_guard_enabled':True})
    model=ScriptedLLM([ScriptedResponse(wants_tools=True,tool_calls=[
        ScriptedToolCall('policy','get_knowledge_documents','{"document_ids":["return_policy"]}'),
        ScriptedToolCall('write','update_return_task','{"order_id":"O10024"}')]),
        ScriptedResponse(text='草稿已记录。')])
    agent=CustomerServiceAgent(settings=settings,llm=model,registry=ToolRegistry(settings))
    final=list(ChatService(settings).run_turn('我想退O10024','USER001',agent=agent))[-1]
    assert '政策依据' not in final.result.answer


def test_existing_guard_toggle_is_an_independent_ablation(settings):
    frames,_,_,_,_=exercise(settings,'C01','save','policy',True,enabled=False)
    assert '咨询依据' not in frames[-1].result.answer
    assert '退款已到账' in frames[-1].result.answer
