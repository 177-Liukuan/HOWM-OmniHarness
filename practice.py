#!/usr/bin/env python3
"""Offline teacher-supervised practice; no label enters the solving thread.

Runs a few original 30-choice questions, not relabelled micro-QA. Candidate policy
publication is a separate explicit human-reviewed operation in policy.py.
"""
import argparse,hashlib,json,time
from pathlib import Path
from codex_team import CodexTeam
from contracts import require,text
from run import METHOD,preflight,run_question,frozen
from state import Budget,atomic
from evaluate import evaluate


def offline_call(root,role,payload,config):
    root.mkdir(parents=True,exist_ok=True)
    budget=Budget(root/'state.json',config)
    require(budget.s['status']!='failed','interrupted offline call; inspect previous attempt')
    team=CodexTeam(root,METHOD,budget.s)
    timeout=budget.reserve('turns')
    try:
        value=team.call(role,payload,[],budget.s['turns'],timeout)
        atomic(root/'result.json',value); budget.s['status']='completed'; return value
    finally: budget.release()


def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('/data'));p.add_argument('--output',type=Path,default=Path('/work'));p.add_argument('--reference',type=Path,required=True);p.add_argument('--ids',required=True);p.add_argument('--rounds',type=int,default=1)
    a=p.parse_args();out=a.output
    require(not (out/'practice-config.json').exists(),'practice output must be fresh; interrupted practice is not silently restarted')
    out.mkdir(parents=True,exist_ok=True)
    ids=a.ids.split(',');require(1<=a.rounds<=len(ids) and len(set(ids))==len(ids),'invalid practice rounds/pool')
    capabilities=json.loads((METHOD/'config/capabilities.json').read_text()); allowed={x['id'] for x in capabilities}
    config=json.loads((METHOD/'config/runtime.json').read_text())
    ref={x['question_id']:x['answer'] for x in map(json.loads,a.reference.read_text().splitlines())}
    require(all(q in ref for q in ids),'all practice questions need frozen teacher reference')
    atomic(out/'practice-config.json',{'reference_kind':'teacher_pseudo','reference_sha256':hashlib.sha256(a.reference.read_bytes()).hexdigest(),'question_pool':ids,'rounds':a.rounds,'config':config,'capabilities':capabilities,'preflight':preflight()})
    history=[];remaining=list(ids)
    for i in range(a.rounds):
        episode=out/f'episode-{i+1:03d}';episode.mkdir()
        proposal=offline_call(episode/'proposer','propose',{'capabilities':capabilities,'available_questions':remaining,'history':history},config)
        require(proposal['question_id'] in remaining and 1<=len(proposal['capability_ids'])<=3 and set(proposal['capability_ids'])<=allowed and text(proposal['objective']) and text(proposal['rationale']),'invalid practice proposal')
        qid=proposal['question_id'];remaining.remove(qid)
        # Label and match feedback are NOT passed to run_question / Codex solver.
        solve=episode/'solve';solve.mkdir();atomic(solve/'manifest.json',{'combined_question_ids':[qid]})
        result=run_question(a.data,solve,qid,config,practice_focus=proposal)
        score=evaluate(solve,a.reference);atomic(episode/'score.json',score)
        feedback={'question_id':qid,'capability_ids':proposal['capability_ids'],'completed':result['status']=='completed','reference_match':result.get('answer')==ref[qid] if result['status']=='completed' else None}
        history.append(feedback)
        if result['status']=='completed':
            card=offline_call(episode/'distiller','distill',{'capability_ids':proposal['capability_ids'],'reference_kind':'teacher_pseudo','reference_match':feedback['reference_match'],'planning':result['planning'],'observation':result['observation'],'review':result['review']},config)
            require(card['kind'] in ('method','failure_lesson') and (feedback['reference_match'] or card['kind']=='failure_lesson'),'mismatch cannot yield a successful method')
            require(set(card['capability_ids'])<=allowed and card['capability_ids'],'unknown capability')
            for key in ('title','when','limitations'):require(text(card[key]),'missing '+key)
            for key in ('procedure','checks','pitfalls'):require(isinstance(card[key],list) and card[key] and all(text(v) for v in card[key]),'missing '+key)
            digest=hashlib.sha256(json.dumps(card,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
            atomic(episode/'candidate-policy.json',{'id':'candidate-'+digest,'status':'pending_human_review','card':card,'provenance':{'question_id':qid,'reference_kind':'teacher_pseudo','reference_match':feedback['reference_match'],'official_correctness':None}})
        atomic(out/'practice-history.json',history)
        print(json.dumps(feedback,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
