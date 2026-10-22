"""Additional lifecycle evidence; not retroactively part of the frozen 24 cases."""
from contextlib import closing

from acs.chat_service import ChatService
from acs.cs_tools import ToolRegistry
from acs.service_agent import CustomerServiceAgent
from acs.sessions import SessionStore

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall
from .test_return_submission import counts, flow  # noqa: F401


def enabled(settings):
    return settings.model_copy(update={'knowledge_mode':'documents',
                                      'write_receipt_guard_enabled':True})


def model_for(settings, calls, model_type=ScriptedLLM):
    model=model_type([ScriptedResponse(wants_tools=True,tool_calls=calls),
                     ScriptedResponse(text='已提交退货申请，退款已到账。')])
    return CustomerServiceAgent(settings=settings,llm=model,registry=ToolRegistry(settings))


def policy_call():
    return ScriptedToolCall('policy','get_knowledge_documents','{"document_ids":["return_policy"]}')


def assert_synced(final,store,agent):
    assert final.result.answer==store.load(final.session_id,'USER001')['messages'][-1]['content']
    assert final.result.answer==agent.messages[-1]['content']
    finals=[e.detail['text'] for e in final.result.trace if e.type=='final']
    assert final.result.answer==finals[-1]


def test_ready_preview_then_explicit_submit_and_status_query_preserve_state(flow):  # noqa: F811
    settings,store,service,sid=flow
    settings=enabled(settings)
    agent=model_for(settings,[policy_call(),ScriptedToolCall('preview','preview_return','{}')])
    frames=list(ChatService(settings,store).run_turn(
        '请预览退货申请，同时说明退货政策','USER001',sid,agent=agent))
    assert all('退款已到账' not in f.result.answer for f in frames)
    assert '7 天' in frames[-1].result.answer
    assert_synced(frames[-1],store,agent)
    assert counts(settings)==(0,0,0)
    preview=service.latest_preview(sid,'USER001')
    assert preview['ready']
    created=service.confirm_and_submit(sid,'USER001',preview['preview_id'])
    service.confirm_and_submit(sid,'USER001',preview['preview_id'])
    assert counts(settings)==(1,1,1)
    state=store.get_return_task(sid,'USER001')['task']
    agent=model_for(settings,[ScriptedToolCall('requests','get_return_requests','{}')])
    # A current state read is not authorization to create another application.
    frames=list(ChatService(settings,store).run_turn('查询申请状态','USER001',sid,agent=agent))
    assert store.get_return_task(sid,'USER001')['task']==state
    assert created['status']=='pending_review' and counts(settings)==(1,1,1)
    assert all('退款已到账' not in f.result.answer for f in frames)
    assert_synced(frames[-1],store,agent)


def test_cross_turn_product_correction_and_reload_do_not_reuse_old_fact(settings):
    settings=enabled(settings)
    store=SessionStore(settings.db_path)
    first=model_for(settings,[ScriptedToolCall('p','get_product','{"product_id":"P001"}'),
        ScriptedToolCall('w','update_return_task','{"order_id":"O10024"}')])
    frames=list(ChatService(settings,store).run_turn(
        '记录O10024的草稿，同时介绍P001的参数','USER001',agent=first))
    sid=frames[-1].session_id
    second=model_for(settings,[ScriptedToolCall('p','get_product','{"product_id":"P002"}'),
        ScriptedToolCall('w','update_return_task','{"order_id":"O10024"}')])
    final=list(ChatService(settings,SessionStore(settings.db_path)).run_turn(
        '更正草稿，仍退O10024，同时介绍P002的参数','USER001',sid,agent=second))[-1]
    assert second.registry.db.get_product('P002')['name'] in final.result.answer
    assert first.registry.db.get_product('P001')['name'] not in final.result.answer
    assert_synced(final,SessionStore(settings.db_path),second)


def test_partial_stream_then_timeout_does_not_publish_false_claim(settings):
    class Interrupted(ScriptedLLM):
        def chat_stream(self,*args,**kwargs):
            if self.calls:
                yield '已提交退货申请，退款已到账。'
                raise TimeoutError('fixed partial stream interruption')
            yield from super().chat_stream(*args,**kwargs)
    settings=enabled(settings)
    store=SessionStore(settings.db_path)
    agent=model_for(settings,[policy_call(),ScriptedToolCall(
        'w','update_return_task','{"order_id":"O10024"}')],Interrupted)
    frames=list(ChatService(settings,store).run_turn(
        '记录O10024的草稿，同时说明退货政策','USER001',agent=agent))
    assert all('退款已到账' not in f.result.answer for f in frames)
    assert '7 天' in frames[-1].result.answer
    assert counts(settings)==(0,0,0)
    assert_synced(frames[-1],store,agent)


def test_generator_cancellation_after_write_restores_actual_draft_without_replay(settings):
    settings=enabled(settings)
    store=SessionStore(settings.db_path)
    agent=model_for(settings,[policy_call(),ScriptedToolCall(
        'w','update_return_task','{"order_id":"O10024"}')])
    with closing(ChatService(settings,store).run_turn(
            '记录O10024的草稿，同时说明退货政策','USER001',agent=agent)) as stream:
        for frame in stream:
            assert '退款已到账' not in frame.result.answer
            if frame.task_view['task']:
                sid=frame.session_id
                break
    restored=SessionStore(settings.db_path)
    before=restored.get_return_task(sid,'USER001')['task']
    assert before['order_id']=='O10024'
    query=CustomerServiceAgent(settings=settings,registry=ToolRegistry(settings),
        llm=ScriptedLLM([ScriptedResponse(wants_tools=True,tool_calls=[
            ScriptedToolCall('q','get_return_task','{}')]),ScriptedResponse(text='当前为草稿。')]))
    list(ChatService(settings,restored).run_turn('查询当前草稿','USER001',sid,agent=query))
    assert restored.get_return_task(sid,'USER001')['task']==before
    assert counts(settings)==(0,0,0)
