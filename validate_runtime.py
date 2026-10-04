#!/usr/bin/env python3
"""Post-implementation probes using a real completed question and actual media.
Not an accuracy test. Writes only to a separate validation output directory.
"""
import argparse,copy,json,tempfile,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import contracts
from evidence import load_question,acquire
from state import Budget
from vendor.submission_io import append_answer,validate_submission,repair_tail
from run import distribution


def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('/data'));p.add_argument('--completed',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output;out.mkdir(parents=True,exist_ok=True);checks=[]
    def passed(name):checks.append({'name':name,'passed':True})
    def rejected(name,fn):
        try:fn()
        except (ValueError,KeyError,TypeError):passed(name);return
        raise AssertionError('accepted invalid case: '+name)
    q,d=load_question(a.data,'094');s=json.loads((a.completed/'questions/094/state.json').read_text());plan=s['planning'];evidence=s['evidence']
    contracts.plan(plan,'094',set());contracts.observation(s['observation'],'094',plan,evidence);contracts.review(s['review'],'094',plan,evidence,0,[]);passed('real_094_contracts')
    bad=copy.deepcopy(s['observation']);bad['candidates'][0]=bad['candidates'][1]
    rejected('duplicate_candidate_rejected',lambda:contracts.observation(bad,'094',plan,evidence))
    bad=copy.deepcopy(s['observation']);bad['candidates'][0]['evidence_refs']=['options_21_30.jpg']
    rejected('wrong_candidate_image_rejected',lambda:contracts.observation(bad,'094',plan,evidence))
    rejected('new_evidence_requires_review',lambda:contracts.review(s['review'],'094',plan,{**evidence,'new.jpg':{}},0,['new.jpg']))
    r={'action':'sample_video','option_id':17,'time_range_seconds':[0.5,1.5],'frames':3,'affected_candidates':[17,21],'expected_observation':'区分装入/取出方向','decision_impact':'装入支持17，取出削弱17'}
    v=copy.deepcopy(s['review']);v.update(decision_status='needs_evidence',could_change_decision=True,unresolved_issue='装入/取出方向不清',evidence_requests=[r])
    assert contracts.route(v,q,plan,set())[0]==[r];passed('specific_issue_admitted')
    assert not contracts.route(v,q,plan,{contracts.request_key(r)})[0];passed('repeat_request_rejected')
    v['could_change_decision']=False;assert not contracts.route(v,q,plan,set())[0];passed('no_impact_stops')
    v['could_change_decision']=True;v['evidence_requests'][0]['time_range_seconds']=[-1,2]
    assert not contracts.route(v,q,plan,set())[0];passed('invalid_interval_rejected')
    # Execute both actual tools against original media, not mocks.
    media=out/'actual-media';media.mkdir(exist_ok=True);r['time_range_seconds']=[0.5,1.5]
    name,meta=acquire(q,d,r,media,lambda:60);assert meta['unique_frames']==3 and len(meta['actual_times'])==3;passed('actual_video_frames_and_pts')
    crop={'action':'crop_evidence','source':'init','bbox':[.15,.1,.6,.7],'affected_candidates':[17,21],'expected_observation':'部件是否在位','decision_impact':'决定安装与拆卸方向'}
    name,meta=acquire(q,d,crop,media,lambda:60);assert meta['original_size'][0]>meta['image']['width'];passed('actual_original_resolution_crop')
    ids=[f'{i:03d}' for i in range(1,21)];manifest=out/'probe-manifest.json';manifest.write_text(json.dumps({'combined_question_ids':ids}));submission=out/'not-for-submission.jsonl'
    assert not submission.exists(),'validation requires fresh output'
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(lambda qid:append_answer(submission,qid,1,manifest),ids*2))
    assert validate_submission(submission,manifest)['complete'] and len(submission.read_text().splitlines())==20;passed('concurrent_append_dedup')
    rejected('conflicting_append_rejected',lambda:append_answer(submission,'001',2,manifest))
    with submission.open('ab') as f:f.write(b'{"question_id":')
    result=repair_tail(submission,manifest);assert result['status']=='repaired' and Path(result['archive']).read_bytes()==b'{"question_id":' and validate_submission(submission,manifest)['complete'];passed('torn_tail_archived_and_repaired')
    config=json.loads((Path(__file__).parent/'config/runtime.json').read_text());b=Budget(out/'budget.json',config)
    b.reserve('turns');before=b.s['turns'];reserved=b.s['reservation']['seconds'];elapsed=b.s['elapsed'];b2=Budget(out/'budget.json',config)
    assert b2.s['turns']==before and b2.s['elapsed']>=elapsed+reserved and b2.s['status']=='failed';passed('interrupted_budget_not_reset')
    b3=Budget(out/'soft-budget.json',config);b3.s['requests']=config['soft']['requests'];assert b3.admit([r],s['review'])=='soft_requests';passed('soft_budget_blocks_without_progress')
    stats=distribution([3,3,4,8]);assert stats=={'n':4,'mean':4.5,'p50':3,'p95':8};passed('nearest_rank_metrics')
    report={'passed':len(checks),'checks':checks,'scope':'Implementation checks and actual media execution, not model accuracy or a natural Slow Path observation.'};(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
