"""Analysis must preserve unscored attempts and index the full planned task set."""
from scripts.analyze_tau_v2 import argument_issues, formal_comparison


def test_argument_checks_use_schema_and_preserve_valid_empty_list():
    contracts = {'read': {'parameters': {'properties': {
        'ids': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['ids']}}}
    assert argument_issues({'name':'read','arguments':{'ids':[]}}, contracts) == []
    assert argument_issues({'name':'read','arguments':{'ids':['X']}}, contracts) == []
    assert argument_issues({'name':'read','arguments':{'ids':[1]}}, contracts)
    assert argument_issues({'name':'read','arguments':{}}, contracts)
    assert argument_issues({'name':'read','arguments':{'ids':[], 'other':1}}, contracts)
    assert argument_issues({'name':'missing','arguments':{}}, contracts)


def test_full_task_index_keeps_unscored_and_missing_separate(tmp_path):
    def row(task, arm, score):
        return {'task':task, 'arm':arm, 'reward':score, 'official_reward':score,
                'evaluation_status':'scored' if score is not None else 'unscored',
                'scope_denials':0, 'empty_scope_routes':0, 'stop_reasons':{}, 'tool_errors':0,
                'reward_details':{}, 'artifact':f'{arm}-{task}.json', 'tool_error_details':[],
                'prompt_tokens':100}
    rows = [row('a','official',1), row('a','acs_v1',0), row('a','acs_v2',1),
            row('b','official',None)]
    result = formal_comparison(tmp_path, {'task_ids':['a','b','c']}, rows)
    assert len(result['tasks']) == 3
    assert result['v1_v2']['v2_only_success'] == ['a']
    assert result['v1_v2']['v1_only_success'] == []
    assert result['tasks'][1]['official_status'] == 'unscored'
    assert result['tasks'][1]['acs_v1_status'] == 'not_started'
    assert result['tasks'][2]['category'] == 'incomplete_or_unscored'
    assert (tmp_path/'task_comparison.csv').exists()


def test_raw_reward_does_not_turn_unscored_protocol_failure_into_success(tmp_path):
    import json

    from scripts.analyze_tau_v2 import analyze
    (tmp_path/'manifest.json').write_text(json.dumps({
        'suite':'formal','task_ids':['a'],'official_tool_contracts':{}}))
    (tmp_path/'requests.json').write_text(json.dumps({
        'spent_guard_cny':'0','halted':'adapter_protocol_error','calls':[]}))
    episode = {'arm':'acs_v2','task':'a','official_reward':1,
               'evaluation_status':'invalid_protocol', 'elapsed_s':1,
               'before_db_hash':'before','after_db_hash':'after',
               'simulation':{'messages':[], 'reward_info':{'reward':1}}}
    (tmp_path/'acs_v2-a-0-0.json').write_text(json.dumps(episode))
    result = analyze(tmp_path)
    assert result['episodes'][0]['official_reward'] == 1
    assert result['episodes'][0]['reward'] is None
    assert result['arms']['acs_v2']['success'] == 0
    assert result['arms']['acs_v2']['unscored'] == 1
    assert result['arms']['acs_v2']['pass_1'] is None
    assert result['arms']['official']['not_started'] == 1


def test_interrupted_process_keeps_unfinished_request_and_attempt(tmp_path):
    import json

    from scripts.analyze_tau_v2 import analyze
    (tmp_path/'manifest.json').write_text(json.dumps({
        'suite':'formal','task_ids':['a'],'official_tool_contracts':{}}))
    (tmp_path/'requests.json').write_text(json.dumps({
        'spent_guard_cny':'2','halted':None,'calls':[{
            'sequence':1,'arm':'acs_v1','task':'a','role':'agent',
            'reserved_cny':'0.5','request':{}}]}))
    (tmp_path/'attempts.jsonl').write_text(json.dumps({
        'task':'a','arm':'acs_v1','status':'started'})+'\n')
    result = analyze(tmp_path)
    assert result['execution_complete'] is False
    assert result['effective_stop_reason'] == 'interrupted_unsettled_request'
    assert result['unfinished_attempts'] == [{'task':'a','arm':'acs_v1'}]
    assert result['guard_including_unsettled_cny'] == '2.5'
    assert result['unsettled_request_sequences'] == [1]
    assert result['arms']['acs_v1']['not_started'] == 0
    assert result['arms']['acs_v1']['unfinished'] == 1
    assert result['formal']['tasks'][0]['acs_v1_status'] == 'interrupted_unscored'
    assert result['arms']['acs_v1']['pass_1'] is None
