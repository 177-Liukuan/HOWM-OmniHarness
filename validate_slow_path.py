#!/usr/bin/env python3
"""Injected-issue integration probe on a real completed question.

Copy a completed experiment into an isolated diagnostic output first. This uses
its native thread, real media and new Review, but NEVER writes submission.jsonl.
The injected issue is a controller test fixture, not model-discovered uncertainty.
"""
import argparse,json,time
from pathlib import Path
import contracts
from codex_team import CodexTeam
from evidence import load_question,acquire
from state import Budget,atomic
from run import METHOD


def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('/data'));p.add_argument('--output',type=Path,default=Path('/work'));p.add_argument('--qid',default='094');a=p.parse_args()
    out=a.output;root=out/'questions'/a.qid;evdir=out/'evidence'/a.qid
    config=json.loads((METHOD/'config/runtime.json').read_text());b=Budget(root/'state.json',config);s=b.s
    require=contracts.require;require(s['status']=='completed','needs copied completed diagnostic question')
    require(not (out/'slow-diagnostic.json').exists(),'diagnostic must be fresh')
    q,d=load_question(a.data,a.qid)
    # Concrete real-media probe: panel location/direction. Test only; no label.
    candidate=next(i for i in range(1,31) if str(i) not in s.get('review_boards',{}) and i!=s['answer'])
    affected=[s['answer'],candidate]; duration=float(q['options'][candidate-1]['duration_seconds'])
    request={'action':'sample_video','option_id':candidate,'time_range_seconds':[duration*.05,duration*.95],'frames':6,'affected_candidates':affected,'expected_observation':'查看未进入初始图板的候选，核对部件位置与动作方向','decision_impact':'新候选若更能解释状态变化则改变排序，否则保留原判断'}
    crop={'action':'crop_evidence','source':'init','bbox':[.15,.15,.85,.85],'affected_candidates':affected,'expected_observation':'放大原始状态中部件位置关系','decision_impact':'确认是否在位会改变两个候选的解释力'}
    injected={**s['review'],'decision_status':'needs_evidence','could_change_decision':True,'unresolved_issue':'初始图板之外的候选是否提供不同方向解释，原状态局部是否支持？','evidence_requests':[request,crop]}
    requests,reason=contracts.route(injected,q,s['planning'],set(s['seen_requests']));require(requests,'fixture rejected: '+reason)
    require(b.admit(requests,injected) is None,'no remaining budget for diagnostic')
    s['revision']+=1;s['status']='running';s['phase']='acquiring';s['pending_evidence']=[];s['last_batch']=[];s['result_versions']={k:{'revision':s['revision'],'status':'stale'} for k in s['result_versions']};b.save();atomic(out/'stale-before-evidence.json',s['result_versions'])
    new_media=[]
    for r in requests:
        b.reserve('requests');s['requested_requests']=s.get('requested_requests',0)+1;b.save()
        name,metadata=acquire(q,d,r,evdir,lambda:b.remaining());s['evidence'][name]=metadata;s['pending_evidence'].append(name);s['last_batch'].append(name);s['seen_requests'].append(contracts.request_key(r));new_media.append(metadata);s['requested_media_seconds']=s.get('requested_media_seconds',0)+metadata['execution_seconds'];s['decoded_frames']=s.get('decoded_frames',0)+metadata.get('decoded_frames',0);b.release()
    images=['states.jpg',*s.get('review_boards',{}).values(),*s['last_batch']]
    team=CodexTeam(root,METHOD,s)
    payload={'question_id':a.qid,'instruction':q['instruction'],'planning':s['planning'],'observation':s['observation'],'previous_review':injected,'revision':s['revision'],'evidence_catalog':list(s['evidence']),'pending_evidence':s['pending_evidence'],'current_image_ids':images,'focused_candidate_boards':s.get('review_boards',{}),'evidence_details':[{ 'evidence_id':n,'request':s['evidence'][n]['request'],**{k:s['evidence'][n][k] for k in ('actual_times','actual_time','pixel_bbox','original_size') if k in s['evidence'][n]}} for n in images if 'request' in s['evidence'][n]], 'required_checks':[{ 'step_id':step['step_id'],'criteria':step['criteria'],'expected_count':len(step['criteria'])} for step in s['planning']['plan']['steps']], 'diagnostic_scope':'根据新的真实图片复核这个题目；这是隔离的流程诊断，不写正式提交。'}
    error=None
    for retry in range(2):
        if error:payload['protocol_error']=error
        timeout=b.reserve('turns',review=True)
        try:
            v=team.call('review',payload,[evdir/n for n in images],s['turns'],timeout)
            contracts.bind_comparison_ranking(v,review=True);contracts.review(v,a.qid,s['planning'],s['evidence'],s['revision'],s['pending_evidence']);require(set(images)<=set(v['reviewed_evidence']),'new image not reviewed')
            board=s.get('review_boards',{}).get(str(v['best_candidate']))
            require(not board or board in v['decision']['evidence_refs'],'decision must cite its actual candidate board')
            b.release();break
        except (ValueError,KeyError,TypeError) as exc:
            b.release();error=str(exc)
            if retry:raise
    s['review']=v;s['pending_evidence']=[];s['result_versions']={k:{'revision':s['revision'],'status':'valid'} for k in s['result_versions']};s['status']='diagnostic_completed';s['phase']='diagnostic_only';b.save()
    atomic(out/'slow-diagnostic.json',{'scope':'Injected specific unresolved issue on real 094; not naturally entered Slow Path; excluded from accuracy/reproducibility/throughput statistics.','new_evidence':new_media,'outside_initial_candidate':candidate,'initial_candidates':s.get('focus_candidates',[]),'review_image_ids':images,'review':v,'thread_id':s['session_id'],'total_turns':s['turns'],'total_requests':s['requests'],'elapsed':s['elapsed'],'submission_written':False,'passed':True})
    print(json.dumps({'passed':True,'turns':s['turns'],'requests':s['requests'],'best_candidate':v['best_candidate']}))

if __name__=='__main__':main()
