"""Read frozen artifacts; write a new scorecard without any scoring/model invocation."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from statistics import median

ROOT=Path(__file__).resolve().parents[2]
SOURCES={}


def read(name):
    path=ROOT/name
    SOURCES[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    return json.loads(path.read_text())


def run(out):
    if out.exists():
        raise ValueError('A new output directory is required')
    historical=read('results/iteration_v2_recovery/tau_base/summary.json')
    raw=ROOT/'results/iteration_v2_recovery/tau_base/raw.jsonl'
    SOURCES[str(raw.relative_to(ROOT))]=hashlib.sha256(raw.read_bytes()).hexdigest()
    hrows=[json.loads(line) for line in raw.open()]
    read('results/iteration_v2_recovery/tau_base/frozen_manifest.json')
    rows=[]
    for arm in ['llm_agent','acs_loop']:
        saved=historical[arm]
        actual=[r for r in hrows if r['arm']==arm]
        assert len(actual)==saved['episodes']==342
        success=sum(r['reward']==1 for r in actual)
        assert success/342==saved['pass^1']
        times=sorted(r['simulation']['duration'] for r in actual)
        tokens=Counter()
        db=Counter()
        for r in actual:
            reward=r['simulation'].get('reward_info') or {}
            db[str((reward.get('db_check') or {}).get('db_match'))]+=1
            for m in r['simulation'].get('messages',[]):
                for k,v in (m.get('usage') or {}).items():
                    if k in ['prompt_tokens','completion_tokens']:
                        tokens[k]+=v
        assert sum(tokens.values())==saved['observed_tokens']
        rows.append({'cohort':'historical_684','arm':arm,'planned':342,'scored':342,
            'success':success,'denominator':342,'pass_1':saved['pass^1'],
            'pass_3':saved['pass^3'],'trials':3,'requests':None,
            'tool_calls':saved['tool_calls'],'observed_input_tokens':tokens['prompt_tokens'],
            'observed_output_tokens':tokens['completion_tokens'],
            'usage_scope':'exported messages; judge usage incomplete',
            'db_matches':dict(db),'latency_scope':'episode; derived nearest-rank p95',
            'p50_s':median(times),'p95_s':times[math.ceil(.95*len(times))-1],
            'cost_cny':None,'cost_complete':False,
            'failed_components':saved['failed_reward_components'],
            'source':'results/iteration_v2_recovery/tau_base/summary.json'})
    formal=read('results/tau_retail_formal_combined_20261022_final/analysis.json')
    components=read('results/tau_retail_formal_combined_20261022_final/score_components.json')
    common=read('results/tau_retail_formal_combined_20261022_final/common_39_metrics.json')
    for arm,s in formal['arms'].items():
        rows.append({'cohort':'formal_119_of_120','arm':arm,'planned':40,'scored':s['scored'],
            'success':s['success'],'denominator':40,'pass_1':s['pass_1'],'pass_3':None,
            'unscored':s['unscored'],'trials':1,'requests':s['requests'],
            'tool_calls':s['tool_calls'],'observed_input_tokens':s['prompt_tokens'],
            'observed_output_tokens':s['completion_tokens'],
            'usage_scope':'effective attempts, all roles; V2 includes unscored task111',
            'guard_cny':s['guard_cny'],'cost_estimate_cny':s['configured_price_estimate_cny'],
            'latency_scope':'artifact episode percentile, includes unscored V2 attempt',
            'p50_s':s['elapsed_p50_s'],'p95_s':s['elapsed_p95_s'],
            'official_components':components[arm],
            'route_events':s['route_events'],'fallback_routes':s['fallback_routes'],
            'empty_scopes':s['empty_scope_routes'],'retries':s['retries'],
            'common_39':common[arm],
            'source':'results/tau_retail_formal_combined_20261022_final/analysis.json'})
    local=read('results/final_delivery_c9afdbf/live/analysis.json')
    local_summary=read('results/final_delivery_c9afdbf/live/summary.json')
    local_ledger=read('results/final_delivery_c9afdbf/live/requests.json')
    for arm,s in local['arms'].items():
        samples=[r for r in local['assessments'] if r['enabled']==(arm=='candidate')]
        raw_path=ROOT/'results/final_delivery_c9afdbf/live/raw.jsonl'
        SOURCES[str(raw_path.relative_to(ROOT))]=hashlib.sha256(raw_path.read_bytes()).hexdigest()
        raw_local=[json.loads(line) for line in raw_path.open()]
        actual=[r for r in raw_local if r['enabled']==(arm=='candidate')]
        tools=sum(e['type']=='tool_call' for r in actual for t in r['turns']
                  for e in t['result']['trace'])
        rows.append({'cohort':'local_11_real','arm':arm,'planned':11,'scored':11,
            'success':s['task_pass'],'denominator':11,'pass_1':None,'pass_3':None,'trials':1,
            'fact_pass':s['facts_pass'],'requests':s['requests'],'tool_calls':tools,
            'observed_input_tokens':s['usage']['prompt_tokens'],
            'observed_output_tokens':s['usage']['completion_tokens'],
            'business_unchanged':sum(r['business_unchanged'] for r in samples),
            'synchronized_conversations':sum(r['all_turns_synchronized'] for r in samples),
            'visible_error_conversations':s['visible_error_conversations'],
            'identified_wrong_frames':s['identified_wrong_frames'],
            'cost_estimate_cny':s['estimated_invoice_cny'],
            'latency_scope':'turn, n=18; original reported percentiles',
            'p50_s':s['turn_ms']['p50']/1000,'p95_s':s['turn_ms']['p95']/1000,
            'first_answer_p50_ms':s['first_answer_ms']['p50'],
            'source':'results/final_delivery_c9afdbf/live/analysis.json'})
    for arm,directory in [('guard_off','paired_off'),('old_guard_on','paired_before'),
                          ('mixed_guard_on','final_pair')]:
        name='results/v21_mixed_answers_20261022/'+directory
        s=read(name+'/summary.json')
        cases=[read(str(p.relative_to(ROOT))) for p in sorted((ROOT/name).glob('C*.json'))]
        assert len(cases)==24
        rows.append({'cohort':'offline_24_fixed_decisions','arm':arm,'planned':24,
            'scored':24,'success':None,'denominator':None,'pass_1':None,'pass_3':None,
            'frozen_fact_list_pass':s['consultation_complete'],
            'frozen_safety_contract_pass':s['frozen_task_complete'],
            'counterexamples_are_not_user_success':True,
            'requests':0,'scripted_model_calls':s['scripted_model_calls'],
            'context_probe_calls':s['context_probe_calls'],
            'tool_calls':sum(e['type']=='tool_call' for r in cases for e in r['trace']),
            'observed_input_tokens':None,'observed_output_tokens':None,
            'literal_false_completion_frames':s['visible_false_completion_frames'],
            'new_applications':s['unexpected_applications'],
            'mean_next_input_chars':s['mean_next_input_chars'],'paid_cost_cny':'0',
            'source':name+'/summary.json'})
    read('results/v21_integration_20261022/offline_grading_audit.json')
    paired=read('results/v21_integration_20261022/paired_review.json')
    paid=read('results/v21_integration_20261022/paired_live/requests.json')
    for arm,s in paired['metrics'].items():
        samples=[r for r in paired['rows'] if r['arm']==arm]
        rows.append({'cohort':'local_8_real','arm':arm,'planned':8,'scored':8,
            'success':s['complete'],'denominator':8,'trials':1,'pass_1':None,'pass_3':None,
            'consultation_complete':sum(r['consultation_complete'] for r in samples),
            'state_goal_complete':sum(r['business_complete'] for r in samples),
            'synchronized_conversations':sum(r['synchronized'] for r in samples),
            'new_applications':sum(r['wrong_application_writes'] for r in samples),
            'requests':s['requests'],'tool_calls':s['tool_calls'],
            'observed_input_tokens':s['input_tokens'],'observed_output_tokens':s['output_tokens'],
            'guard_cny':s['guard_cny'],'cost_estimate_cny':s['configured_usage_estimate_cny'],
            'latency_scope':'episode, n=8; Python worker import excluded',
            'p50_s':s['episode_p50_s'],'max_s':s['episode_max_s'],
            'first_answer_p50_ms':s['user_visible_first_answer_p50_ms'],
            'route_events':s['route_events'],'fallback_routes':s['fallback_routes'],
            'retries':s['retry_scheduled'],'consultation_triggers':s['consultation_triggers'],
            'source':'results/v21_integration_20261022/paired_review.json'})
    result={'version':'scorecard-20261023-v1','paid_requests_this_audit':0,
        'success_definitions_remain_per_cohort':True,'rows':rows,
        'accounting':{'formal_guard_total_cny':formal['guard_spent_cny'],
            'formal_total_requests':formal['actual_requests'],
            'formal_superseded_requests':len(formal['supplement']['ineffective_request_sequences']),
            'formal_unknown_usage_sequences':formal['supplement']['historical_unknown_usage'],
            'local11_guard_cny':local_summary['guard_spent_cny'],
            'local11_requests':len(local_ledger['calls']),
            'local8_guard_cny':paid['spent_guard_cny'],'local8_requests':len(paid['calls'])},
        'missing_values':'null means unrecorded/not applicable, never free or zero error',
        'sources_sha256':SOURCES}
    out.mkdir(parents=True)
    (out/'scorecard.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    fields=['cohort','arm','planned','scored','success','denominator','pass_1','pass_3',
            'requests','tool_calls','observed_input_tokens','observed_output_tokens',
            'guard_cny','cost_estimate_cny','p50_s','p95_s','first_answer_p50_ms',
            'fact_pass','frozen_fact_list_pass','frozen_safety_contract_pass',
            'consultation_complete','state_goal_complete','business_unchanged',
            'synchronized_conversations','visible_error_conversations','identified_wrong_frames',
            'literal_false_completion_frames','new_applications','scripted_model_calls',
            'context_probe_calls','mean_next_input_chars','usage_scope','latency_scope','source']
    with (out/'scorecard.csv').open('w') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction='ignore',lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({'rows':len(rows),'sources':len(SOURCES),'paid_requests':0}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    run(parser.parse_args().out)
