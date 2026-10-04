#!/usr/bin/env python3
"""Summarize actual first-pass/feedback costs separately; no model calls."""
import argparse,json
from pathlib import Path
from common import read,atomic
from contracts import require
from run import distribution

def audit(practice):
    episodes=sorted((practice/'episodes').glob('*/record.json'));records=[read(p) for p in episodes];first=[];final=[];receipt_count=0
    for path,r in zip(episodes,records):
        ep=path.parent;one=read(ep/'first-pass.json');last=read(ep/'solve/questions'/r['question_id']/'state.json');first.append(one);final.append(last)
        require(one['attempts']==1,'first pass was overwritten by feedback')
        require(last['attempts']<=2,'more than one teacher-feedback attempt')
        require(last['turns']>=one['turns'] and last['elapsed']>=one['elapsed'],'budget reset after feedback')
        require(r['first_answer']==one.get('answer'),'first answer was changed')
        for f in (ep/'solve/questions'/r['question_id']/'turns').glob('*.audit.json'):
            a=read(f);focused=read(practice/'source/config/runtime.json').get('focused_review',{}).get('enabled',False) and '-review.audit.json' in f.name
            require(a['tool_calls']==0 and a['compactions']==0 and len(a['images'])>=(3 if focused else 4),'missing native receipt or unauthorized tool')
            if focused:require(not any(Path(x['path']).name.startswith('options_') for x in a['images']),'focused Review resent overview pages')
            receipt_count+=1
    def metrics(states):
        return {'turns':distribution([s['turns'] for s in states]),'requests':distribution([s['requests'] for s in states]),'seconds':distribution([s['elapsed'] for s in states]),'fast_path_share':sum(not s['entered_slow_path'] for s in states)/len(states) if states else None,'slow_path_share':sum(s['entered_slow_path'] for s in states)/len(states) if states else None,'budget_terminated':sum(s.get('stop_reason','').startswith(('soft_','hard_','budget_','finish_only')) for s in states)}
    offline=[read(p) for p in (practice/'batches').glob('*/state.json')]
    return {'finished':len(records),'first_pass_technical_failures':sum(not r['first_completed'] for r in records),'feedback_technical_failures':sum(r['first_completed'] and not r['final_completed'] for r in records),'first_pass':metrics(first),'including_feedback':metrics(final),'native_audited_turns':receipt_count,'proposer_turns':sum(s['turns'] for s in offline),'proposer_seconds':sum(s['elapsed'] for s in offline),'reference_kind':'teacher_pseudo','official_accuracy':None,'scope':'Actual persisted runs; technical failures and semantic mismatches separated. Per-question time excludes proposer/distiller; no GPU kernel-utilization claim.'}

def main():
    p=argparse.ArgumentParser();p.add_argument('practice',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args();v=audit(a.practice);atomic(a.output,v);print(json.dumps(v,ensure_ascii=False))
if __name__=='__main__':main()
