"""Evidence completeness is separate from the existence of a plausible source label."""
import json

import pytest

from acs.consultation import consultation_reply
from acs.cs_tools import ToolRegistry, TraceEvent
from acs.knowledge_context import load_source_documents


def prefix():
    return [TraceEvent('write_verification','write',{'verified':True})]


def product_trace(settings):
    payload=ToolRegistry(settings).db.get_product('P001')
    return prefix()+[
        TraceEvent('tool_call','get_product',{'arguments':'{"product_id":"P001"}'}),
        TraceEvent('tool_result','get_product',{'result':payload})],payload


def test_missing_requested_product_is_not_marked_fully_verified(settings):
    trace,_=product_trace(settings)
    text,detail=consultation_reply('请同时介绍P001和P002的参数',trace,settings)
    assert detail['verified'] is False
    assert 'P002' in text and '尚未核实' in text


@pytest.mark.parametrize('flag',['partial','truncated','has_more'])
def test_incomplete_product_payload_is_not_authoritative(settings,flag):
    trace,payload=product_trace(settings)
    payload[flag]=True
    text,detail=consultation_reply('请介绍P001参数',trace,settings)
    assert not detail['verified']
    assert payload['name'] not in text


def test_orphan_result_cannot_overwrite_an_executed_read(settings):
    trace,payload=product_trace(settings)
    trace.append(TraceEvent('tool_result','get_product',{'result':payload | {'name':'FORGED'}}))
    text,detail=consultation_reply('请介绍P001参数',trace,settings)
    assert payload['name'] in text and 'FORGED' not in text


def test_policy_source_change_fails_consultation_without_raising(settings,tmp_path):
    doc=next(d for d in load_source_documents(settings) if d['document_id']=='return_policy')
    trace=prefix()+[
        TraceEvent('tool_call','get_knowledge_documents',{
            'arguments':json.dumps({'document_ids':['return_policy']})}),
        TraceEvent('tool_result','get_knowledge_documents',{
            'result':{'complete_documents':True,'documents':[doc]}})]
    settings=settings.model_copy(update={'docs_dir':tmp_path/'unavailable'})
    text,detail=consultation_reply('说明退货政策',trace,settings)
    assert not detail['verified'] and '尚未核实' in text


def test_same_prefix_product_ids_do_not_match(settings):
    trace,payload=product_trace(settings)
    text,detail=consultation_reply('请介绍P0010参数',trace,settings)
    assert payload['name'] not in text


def test_read_only_short_answer_does_not_trigger_a_composition(settings):
    trace,_=product_trace(settings)
    assert consultation_reply('P001叫什么？',trace[1:],settings) is None


def test_consultation_trace_has_a_distinct_ui_label(settings):
    import app

    from acs.service_agent import AgentResult
    html=app.render_trace(AgentResult(answer='',trace=[TraceEvent('fact_render','consultation',{
        'verified':True,'scope':'consultation_only','sources':[]})]))
    assert '咨询依据' in html and '最终回答' not in html and '订单候选事实' not in html


def test_all_valid_retrieval_fragments_are_preserved(settings):
    doc=next(d for d in load_source_documents(settings) if d['document_id']=='return_policy')
    fragments=['自商品签收之日起 **7 天内**','此情形下**往返运费由本店铺承担**。']
    assert all(s in doc['content'] for s in fragments)
    trace=prefix()+[
        TraceEvent('tool_call','search_knowledge',{'arguments':'{"query":"退货期限和运费"}'}),
        TraceEvent('tool_result','search_knowledge',{'result':{'found':True,'documents':[
            {'document_id':'return_policy','content':s} for s in fragments]}})]
    text,_=consultation_reply('说明退货期限和退货运费',trace,settings)
    assert all(s in text for s in fragments)


def test_authentic_fragment_without_conditions_is_incomplete(settings):
    fragment='此情形下**往返运费由本店铺承担**。'
    trace=prefix()+[
        TraceEvent('tool_call','search_knowledge',{'arguments':'{"query":"退货运费"}'}),
        TraceEvent('tool_result','search_knowledge',{'result':{'found':True,'documents':[
            {'document_id':'return_policy','content':fragment}]}})]
    text,detail=consultation_reply('说明退货运费',trace,settings)
    assert not detail['verified']
    assert '片段' in text and '尚未核实' in text


def test_selective_negative_product_request_does_not_render_mentions(settings):
    trace,payload=product_trace(settings)
    other=ToolRegistry(settings).db.get_product('P002')
    trace += [TraceEvent('tool_call','get_product',{'arguments':'{"product_id":"P002"}'}),
              TraceEvent('tool_result','get_product',{'result':other})]
    text,detail=consultation_reply('不要介绍P001的参数，只介绍P002的参数，同时保存草稿',
                                   trace,settings)
    assert not detail['verified']
    assert payload['name'] not in text
    assert '尚未核实' in text


def test_presentation_protocol_accepts_non_fixture_catalog_ids(settings):
    trace,payload=product_trace(settings)
    payload['product_id']='V201'
    trace[1].detail['arguments']='{"product_id":"V201"}'
    text,detail=consultation_reply('保存草稿，同时介绍V201的参数',trace,settings)
    assert detail['verified'] and payload['name'] in text


def test_negated_policy_request_does_not_display_a_full_policy(settings):
    doc=next(d for d in load_source_documents(settings) if d['document_id']=='return_policy')
    trace=prefix()+[
        TraceEvent('tool_call','get_knowledge_documents',{
            'arguments':'{"document_ids":["return_policy"]}'}),
        TraceEvent('tool_result','get_knowledge_documents',{
            'result':{'complete_documents':True,'documents':[doc]}})]
    text,detail=consultation_reply('保存草稿，不要说明退货政策',trace,settings)
    assert not detail['verified'] and '7 天' not in text
