"""Independent examples: no test IDs, addresses or reference actions in runtime rules."""
import json

import pytest

from acs.chat_service import ChatService
from acs.cs_tools import ToolRegistry
from acs.routing import route_request
from acs.service_agent import CustomerServiceAgent
from acs.sessions import SessionStore

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall


@pytest.mark.parametrize('available',[[],['read_account','change_shipping_address']])
def test_sku_with_foreign_registry_cannot_remove_every_tool(available):
    route=route_request('Please use this SKU and proceed',available,False)
    assert route['confidence']=='fallback'
    assert route['allowed_tools']==available


def test_pending_local_task_keeps_return_workflow_on_sku_followup():
    route=route_request('SKU颜色确认了', ['get_order','update_return_task'],True)
    assert 'update_return_task' in route['allowed_tools']


def run(settings,responses,message='请帮我处理退货',*,enabled=True,store=None,sid=None):
    settings=settings.model_copy(update={'write_receipt_guard_enabled':enabled})
    store=store or SessionStore(settings.db_path)
    model=ScriptedLLM(responses)
    agent=CustomerServiceAgent(settings=settings,llm=model,registry=ToolRegistry(settings))
    frames=list(ChatService(settings,store).run_turn(message,'USER001',sid,agent=agent))
    return frames,store,agent


def test_plain_completion_without_execution_is_not_a_receipt(settings):
    frames,store,agent=run(settings,[ScriptedResponse(text='已提交退货申请，退款已到账。')])
    final=frames[-1]
    assert '尚未核实' in final.result.answer
    assert '退款已到账' not in final.result.answer
    assert all('退款已到账' not in f.result.answer for f in frames)
    assert store.load(final.session_id,'USER001')['messages'][-1]['content']==final.result.answer
    assert agent.messages[-1]['content']==final.result.answer
    assert next(e for e in final.result.trace if e.type=='final').detail['text'] == (
        final.result.answer)
    assert any(e.type=='write_verification' for e in final.result.trace)


def test_dsml_is_untrusted_text_not_a_call(settings):
    text='<｜｜DSML｜｜ calls><invoke name="change_shipping_address">done</invoke>'
    frames,_,_=run(settings,[ScriptedResponse(text=text)])
    assert '尚未核实' in frames[-1].result.answer
    assert not any(e.type=='tool_call' for e in frames[-1].result.trace)
    assert all(text not in f.result.answer for f in frames)


@pytest.mark.parametrize('text',[
    '这是引用：“我已经取消订单”。请不要执行。',
    '如果取消草稿，之前的信息会丢失吗？',
    '我可以在页面确认后提交申请，目前尚未提交。',
    '耳机支持主动降噪。',
])
def test_read_only_quoted_and_hypothetical_answers_are_preserved(settings,text):
    frames,_,_=run(settings,[ScriptedResponse(text=text)],message='我只是咨询，不执行任何操作')
    assert frames[-1].result.answer==text


@pytest.mark.parametrize('enabled',[False,True])
def test_successful_draft_is_verified_without_creating_an_application(settings,enabled):
    frames,store,_=run(settings,[ScriptedResponse(wants_tools=True,tool_calls=[
        ScriptedToolCall('save','update_return_task','{"order_id":"O10024"}')]),
        ScriptedResponse(text='退货草稿已记录，尚未提交申请。')],
        '我想退O10024',enabled=enabled)
    final=frames[-1]
    assert final.task_view['task']['order_id']=='O10024'
    assert final.task_view['task']['revision']==1
    with store._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM return_requests').fetchone()[0]==0
    if enabled:
        assert any(e.type=='write_verification' and e.detail.get('verified')
                   for e in final.result.trace)


@pytest.mark.parametrize('mode',['rejected','lost','unchanged','router_denied'])
def test_false_success_after_failed_or_unverifiable_write(settings,monkeypatch,mode):
    store=SessionStore(settings.db_path)
    if mode in {'lost','unchanged'}:
        original=store.update_return_task
        def faulty(*args,**kwargs):
            if mode=='lost':
                original(*args,**kwargs)
                raise OSError('response lost')
            return store.get_return_task(args[0],args[1])
        monkeypatch.setattr(store,'update_return_task',faulty)
    order='O10001' if mode=='rejected' else 'O10024'
    settings=settings.model_copy(update={'workflow_router_enabled':mode=='router_denied'})
    message='查询推荐型号参数' if mode=='router_denied' else '我想退'+order
    frames,_,_=run(settings,[ScriptedResponse(wants_tools=True,tool_calls=[
        ScriptedToolCall('save','update_return_task',json.dumps({'order_id':order}))]),
        ScriptedResponse(text='已提交退货申请，退款已到账。')],message,store=store)
    assert '退款已到账' not in frames[-1].result.answer
    assert '尚未核实' in frames[-1].result.answer or '未完成' in frames[-1].result.answer
    with store._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM return_requests').fetchone()[0]==0
    if mode=='lost':
        assert frames[-1].task_view['task']['revision']==1


def test_switch_off_preserves_the_known_false_claim_for_ablation(settings):
    text='已提交退货申请，退款已到账。'
    frames,_,_=run(settings,[ScriptedResponse(text=text)],enabled=False)
    assert frames[-1].result.answer==text


def test_verified_receipt_cannot_authorize_a_different_object_or_superseded_state():
    from acs.write_evidence import guarded_answer
    receipts=[{'verified':True,'claims':['address'],'target':'order-A','scope':'address',
               'summary':'Verified order-A address: Actual Street.'}]
    text,detail=guarded_answer("I've updated the address of order-B to Wrong Street.",receipts)
    assert 'order-B' not in text and 'Wrong Street' not in text
    assert 'order-A' in text and 'Actual Street' in text
    receipts.append({'verified':True,'claims':['address'],'target':'order-C','scope':'address',
                     'summary':'Verified order-C address: Current Street.'})
    text,detail=guarded_answer('Your address has been updated for order-A.',receipts)
    assert 'order-A' not in text and 'order-C' in text


def test_streamed_false_claim_never_reaches_visible_updates(settings):
    class Streaming(ScriptedLLM):
        def chat_stream(self,*args,**kwargs):
            yield '已提交退货申请，退款已到账。'
            yield from super().chat_stream(*args,**kwargs)
    settings=settings.model_copy(update={'write_receipt_guard_enabled':True})
    model=Streaming([ScriptedResponse(text='已提交退货申请，退款已到账。')])
    agent=CustomerServiceAgent(settings=settings,llm=model,registry=ToolRegistry(settings))
    frames=list(ChatService(settings).run_turn('请处理售后','USER001',agent=agent))
    assert all('退款已到账' not in f.result.answer for f in frames)
    assert frames[-1].result.answer


def test_write_verification_is_an_independent_trace_and_progress(settings):
    import app

    from acs.cs_tools import TraceEvent
    from acs.progress import progress_events, render_progress
    from acs.service_agent import AgentResult
    result=AgentResult(answer='尚未核实',trace=[TraceEvent('write_verification','answer',{
        'verified':False,'model_answer':'unverified diagnostic only'})])
    html=app.render_trace(result)
    assert '业务回执核验' in html
    projected=progress_events('session','turn',result,'completed')
    assert any(p['kind']=='write_verification' for p in projected)
    assert 'unverified diagnostic only' not in render_progress(projected)


def test_preview_rejection_keeps_authoritative_missing_requirements(settings):
    frames,_,_=run(settings,[ScriptedResponse(wants_tools=True,tool_calls=[
        ScriptedToolCall('preview','preview_return','{}')]),
        ScriptedResponse(text='请补充退货信息。')],'请预览退货申请')
    result=frames[-1].result
    payload=next(e.detail['result'] for e in result.trace
                 if e.type=='tool_result' and e.label=='preview_return')
    assert payload['ready'] is False
    assert payload['reasons']
    assert all(reason in result.answer for reason in payload['reasons'])


@pytest.mark.parametrize('authorized',[True,False])
def test_cancel_guard_keeps_explicit_authorization_and_actual_state(settings,authorized):
    frames,store,_=run(settings,[ScriptedResponse(wants_tools=True,tool_calls=[
        ScriptedToolCall('save','update_return_task','{"order_id":"O10024"}')]),
        ScriptedResponse(text='草稿已记录。')],'我想退O10024')
    sid=frames[-1].session_id
    text='中止退货草稿' if authorized else '不要取消退货草稿，我只是问一下'
    frames,_,_=run(settings,[ScriptedResponse(wants_tools=True,tool_calls=[
        ScriptedToolCall('cancel','cancel_return_task','{}')]),
        ScriptedResponse(text='草稿已取消。')],text,store=store,sid=sid)
    actual=store.get_return_task(sid,'USER001')['task']
    assert actual['status']==('cancelled' if authorized else 'draft')
    assert frames[-1].task_view['task']==actual
    if not authorized:
        assert '未执行取消' in frames[-1].result.answer
