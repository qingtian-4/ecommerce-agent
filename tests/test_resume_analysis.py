"""A resumed attempt must not inherit abandoned-attempt task/arm usage."""
import json

import pytest
from scripts.analyze_tau_v2 import analyze


def fixture(tmp_path, start=3, end=3):
    manifest={'suite':'formal','task_ids':['97'],'official_tool_contracts':{}}
    calls=[{'sequence':i,'task':'97','arm':'acs_v1','role':'agent',
            'reserved_cny':'0.1','charge_guard_cny':'0.00012',
            'request':{},'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}
           for i in range(1,4)]
    episode={'task':'97','arm':'acs_v1','official_reward':1,'evaluation_status':'scored',
             'elapsed_s':1,'request_start':start,'request_end':end,
             'before_db_hash':'initial','after_db_hash':'final',
             'simulation':{'messages':[], 'reward_info':{'reward':1}}}
    for name,value in [('manifest.json',manifest),('requests.json',{
            'calls':calls,'spent_guard_cny':'0.00036','halted':None}),
            ('acs_v1-97-0-0.json',episode)]:
        (tmp_path/name).write_text(json.dumps(value))
    return calls


def test_effective_attempt_uses_request_range_but_total_spend_keeps_all(tmp_path):
    fixture(tmp_path)
    result=analyze(tmp_path)
    assert result['arms']['acs_v1']['requests']==1
    assert result['arms']['acs_v1']['prompt_tokens']==10
    assert result['actual_requests']==3
    assert result['guard_spent_cny']=='0.00036'


@pytest.mark.parametrize('start,end',[(0,3),(3,4),(3,2)])
def test_invalid_request_bounds_are_not_silently_aggregated(tmp_path,start,end):
    fixture(tmp_path,start,end)
    with pytest.raises(ValueError):
        analyze(tmp_path)


def test_range_must_belong_to_the_episode(tmp_path):
    calls=fixture(tmp_path)
    calls[-1]['task']='other'
    (tmp_path/'requests.json').write_text(json.dumps({
        'calls':calls,'spent_guard_cny':'0.00036','halted':None}))
    with pytest.raises(ValueError):
        analyze(tmp_path)


def test_all_attempts_present_with_unscored_result_is_not_complete(tmp_path):
    calls=fixture(tmp_path)
    prototype=json.loads((tmp_path/'acs_v1-97-0-0.json').read_text())
    for i,arm in enumerate(['official','acs_v1','acs_v2'],1):
        calls[i-1]['arm']=arm
        record=prototype | {'arm':arm,'request_start':i,'request_end':i}
        if arm=='acs_v2':
            record.update(evaluation_status='unscored',official_reward=None)
        (tmp_path/f'{arm}-97-0-0.json').write_text(json.dumps(record))
    (tmp_path/'requests.json').write_text(json.dumps({
        'calls':calls,'spent_guard_cny':'0.00036','halted':None}))
    result=analyze(tmp_path)
    assert result['execution_complete'] is False
    assert result['effective_stop_reason']=='unscored_episodes'
