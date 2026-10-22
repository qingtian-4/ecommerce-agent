"""Independent official Tool/ToolMessage protocol examples; no gold tasks or paid LLM."""
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip('tau2')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from tau2.data_model.message import ToolMessage, UserMessage  # noqa: E402
from tau2.environment.tool import as_tool  # noqa: E402
from tau_acs_agent import ACSAgent  # noqa: E402

from acs.config import Settings  # noqa: E402

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall  # noqa: E402


@pytest.mark.parametrize('mode',['success','unchanged','failure','lost'])
def test_official_contract_requires_target_fields_in_real_receipt(mode):
    database={'user_id':'sample_user','address':{'address1':'old street'}}
    def modify_user_address(user_id:str,address1:str)->dict:
        if mode!='unchanged':
            database['address']={'address1':address1}
        return database
    tool=as_tool(modify_user_address)
    tool._func.__mutates_state__=True
    model=ScriptedLLM([ScriptedResponse(wants_tools=True,tool_calls=[ScriptedToolCall(
        'write','modify_user_address','{"user_id":"sample_user","address1":"new street"}')]),
        ScriptedResponse(text='Your address has been updated.')])
    settings=Settings(_env_file=None,write_receipt_guard_enabled=True,
                      workflow_router_enabled=True)
    adapter=ACSAgent([tool],'Only perform authorized actions.',settings=settings,
                     normalize_results=True,llm_factory=lambda s:model)
    call,state=adapter.generate_next_message(UserMessage(role='user',
        content='Confirmed. Please update the address; here is the SKU.'),adapter.get_init_state())
    assert call.tool_calls
    result=tool._func(user_id='sample_user',address1='new street')
    error=mode in {'failure','lost'}
    content='Error: response unavailable' if error else json.dumps(result)
    answer,state=adapter.generate_next_message(ToolMessage(role='tool',id='write',
                                              content=content,error=error),state)
    if mode=='success':
        assert 'sample_user' in answer.content and 'new street' in answer.content
        assert database['address']['address1']=='new street'
    else:
        assert answer.content!='Your address has been updated.'
        assert len(model.calls)==1
    assert state.agent.messages[-1]['content']==answer.content
    if mode=='unchanged':
        assert database['address']['address1']=='old street'


def test_no_schema_dsml_cannot_become_a_structured_mutation():
    text='<｜｜DSML｜｜ calls><invoke name="modify_user_address">Done</invoke>'
    model=ScriptedLLM([ScriptedResponse(text=text)])
    adapter=ACSAgent([], 'No tools.',settings=Settings(_env_file=None,
        write_receipt_guard_enabled=True),llm_factory=lambda s:model,normalize_results=True)
    answer,state=adapter.generate_next_message(UserMessage(role='user',content='Proceed'),
                                               adapter.get_init_state())
    assert not answer.tool_calls and '尚未核实' in answer.content
    assert not state.agent.registry.protocol_records


def test_existing_refund_cannot_authorize_refund_completion_on_address_change():
    from tau_acs_agent import OfficialRegistry
    registry=OfficialRegistry([])
    receipt={'order_id':'sample_order','address':{'address1':'current street'},
             'payment_history':[{'transaction_type':'refund','amount':5}]}
    proof=registry.verify_mutation('modify_pending_order_address',
        {'order_id':'sample_order','address1':'current street'},{'result':receipt},None,'external')
    assert proof['verified'] and 'refund' not in proof['claims']
    assert 'refund' not in proof['summary']


def test_item_replacement_checks_old_and_new_multiplicity():
    from tau_acs_agent import OfficialRegistry
    registry=OfficialRegistry([])
    registry.before_mutation={'items':[{'item_id':'old'},{'item_id':'new'}]}
    args={'order_id':'sample','item_ids':['old'],'new_item_ids':['new']}
    payload={'result':{'order_id':'sample','status':'pending (item modified)',
                       'items':[{'item_id':'old'},{'item_id':'new'}]}}
    assert not registry.verify_mutation(
        'modify_pending_order_items',args,payload,None,'x')['verified']
    payload['result']['items']=[{'item_id':'new'},{'item_id':'new'}]
    assert registry.verify_mutation('modify_pending_order_items',args,payload,None,'x')['verified']


def test_historical_payment_is_not_a_current_payment_change():
    from tau_acs_agent import OfficialRegistry
    registry=OfficialRegistry([])
    registry.before_mutation={'payment_history':[{'transaction_type':'payment',
        'payment_method_id':'original','amount':12}]}
    args={'order_id':'sample','payment_method_id':'new'}
    payload={'result':{'order_id':'sample','payment_history':[{'transaction_type':'payment',
        'payment_method_id':'new','amount':12}]}}
    assert not registry.verify_mutation(
        'modify_pending_order_payment',args,payload,None,'x')['verified']
    payload['result']['payment_history']=registry.before_mutation['payment_history']+[
        {'transaction_type':'payment','payment_method_id':'new','amount':12},
        {'transaction_type':'refund','payment_method_id':'original','amount':12}]
    assert registry.verify_mutation(
        'modify_pending_order_payment',args,payload,None,'x')['verified']
