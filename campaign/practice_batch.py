#!/usr/bin/env python3
"""Resumable capability-directed practice. References never enter solver prompts."""
import argparse,copy,fcntl,json,time
from pathlib import Path
from common import read,atomic,model_call,hashfile
from contracts import require
from run import METHOD,preflight,run_question,frozen,distribution

def summarize(out,ids,caps):
    records=[read(p) for p in sorted((out/'episodes').glob('*/record.json'))]
    first=[r for r in records if r['first_completed']]
    report={'reference_kind':'teacher_pseudo','official_accuracy':None,'planned':len(ids),'finished':len(records),'first_completed':len(first),'technical_failures':len(records)-len(first),'first_pass_matches':sum(r['first_match'] is True for r in records),'first_pass_agreement_all':sum(r['first_match'] is True for r in records)/len(records) if records else None,'feedback_attempted':sum(r['feedback_used'] for r in records),'final_matches':sum(r['final_match'] is True for r in records),'coverage':{c['id']:{'episodes':sum(c['id'] in r['capability_ids'] for r in records),'first_matches':sum(c['id'] in r['capability_ids'] and r['first_match'] is True for r in records)} for c in caps},'turns':distribution([r['turns'] for r in records]),'requests':distribution([r['requests'] for r in records]),'seconds':distribution([r['seconds'] for r in records]),'note':'Capability tags are practice topics, not independent skill accuracy. Feedback results are not first-pass performance.'}
    atomic(out/'summary.json',report);return report

def main():
    p=argparse.ArgumentParser();p.add_argument('--corpus',type=Path,default=Path('/corpus'));p.add_argument('--data',type=Path,default=Path('/data'));p.add_argument('--output',type=Path,default=Path('/work'));p.add_argument('--limit',type=int,default=32);a=p.parse_args()
    out=a.output;out.mkdir(parents=True,exist_ok=True)
    with (out/'.runner.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        config=read(METHOD/'config/runtime.json');caps=read(METHOD/'config/capabilities.json');capmap={c['id']:c['name'] for c in caps}
        index=read(a.corpus/'index.json');ids=[x['question_id'] for x in index];require(1<=a.limit<=len(ids),'limit outside pool')
        reference={x['question_id']:int(x['answer']) for x in map(json.loads,(a.corpus/'reference.jsonl').read_text().splitlines())}
        frozen(out/'practice-config.json',{'reference_kind':'teacher_pseudo','reference_sha256':hashfile(a.corpus/'reference.jsonl'),'index_sha256':hashfile(a.corpus/'index.json'),'config':config,'ids':ids,'source_sha256':{str(f.relative_to(METHOD)):hashfile(f) for f in METHOD.rglob('*') if f.is_file() and '__pycache__' not in str(f)}})
        if not (out/'preflight.json').exists():atomic(out/'preflight.json',preflight())
        (out/'episodes').mkdir(exist_ok=True);(out/'batches').mkdir(exist_ok=True)
        completed={p.parent.name for p in (out/'episodes').glob('*/record.json')}
        batchno=0
        while len(completed)<a.limit:
            batchno+=1;batch=out/'batches'/f'{batchno:03d}'
            if (batch/'result.json').exists(): proposals=read(batch/'result.json')['proposals']
            else:
                available=[x for x in index if x['question_id'] not in completed]
                count=min(8,a.limit-len(completed));report=summarize(out,ids,caps)
                payload={'capabilities':caps,'available_questions':available,'batch_size':count,'history_summary':report}
                def validate(v):
                    ps=v['proposals'];qs=[x['question_id'] for x in ps]
                    require(len(ps)==count and len(set(qs))==count and set(qs)<={x['question_id'] for x in available},'proposals must be distinct remaining questions')
                    for x in ps:require(1<=len(x['capability_ids'])<=3 and len(set(x['capability_ids']))==len(x['capability_ids']) and set(x['capability_ids'])<=set(capmap),'invalid capability selection')
                proposals=model_call(batch,'propose_batch',payload,[],config,validate)['proposals']
            for proposal in proposals:
                qid=proposal['question_id']
                if qid in completed:continue
                episode=out/'episodes'/qid;episode.mkdir(exist_ok=True);frozen(episode/'proposal.json',proposal)
                solve=episode/'solve';solve.mkdir(exist_ok=True);frozen(solve/'manifest.json',{'combined_question_ids':[qid]})
                # Do not transmit the teacher-derived topic, free objective or rationale.
                focus={'capabilities':[{'id':c,'name':capmap[c]} for c in proposal['capability_ids']],'instruction':'仅把能力名称作为核对方向。事实和答案必须依据本题实际媒体；不预设状态解释。'}
                if (episode/'first-pass.json').exists():first=read(episode/'first-pass.json')
                else:
                    first=copy.deepcopy(run_question(a.data,solve,qid,config,practice_focus=focus,commit=False,policy_bundle={'policies':[]}))
                    atomic(episode/'first-pass.json',first)
                ok=first['status']=='completed';match=first.get('answer')==reference[qid] if ok else None
                final=first
                if ok and not match:
                    final=run_question(a.data,solve,qid,config,practice_focus=focus,commit=False,policy_bundle={'policies':[]},feedback=True)
                record={'question_id':qid,'capability_ids':proposal['capability_ids'],'first_completed':ok,'first_answer':first.get('answer'),'first_match':match,'final_completed':final['status']=='completed','final_answer':final.get('answer') if final['status']=='completed' else None,'final_match':final.get('answer')==reference[qid] if final['status']=='completed' else None,'feedback_used':bool(final.get('feedback_used')) and not final.get('feedback_skipped'),'feedback_skipped':final.get('feedback_skipped'),'turns':final['turns'],'requests':final['requests'],'seconds':final['elapsed'],'error':final.get('error'),'reference_kind':'teacher_pseudo','official_correctness':None}
                atomic(episode/'record.json',record);completed.add(qid);summarize(out,ids,caps);print(json.dumps(record,ensure_ascii=False),flush=True)
                if (out/'STOP_AFTER_QUESTION').exists():return
                # Technical failures require inspection before spending a whole campaign.
                records=[read(p) for p in (out/'episodes').glob('*/record.json')]
                if sum(not r['first_completed'] for r in records)>=2:
                    raise RuntimeError('quality gate: two technical failures; inspect preserved checkpoints before continuing')
                if len(completed)>=a.limit:break
        print(json.dumps(summarize(out,ids,caps),ensure_ascii=False),flush=True)
if __name__=='__main__':main()
