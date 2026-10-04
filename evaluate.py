#!/usr/bin/env python3
"""Offline only: teacher agreement and empty-directory repeat comparisons."""
import argparse,hashlib,json
from pathlib import Path
from vendor.submission_io import inspect_bytes,require_clean


def records(path,manifest):
    summary,seen=inspect_bytes(path.read_bytes() if path.exists() else b'',manifest)
    require_clean(summary); return summary,seen


def evaluate(run,reference):
    manifest=run/'manifest.json'; ids=set(json.loads(manifest.read_text())['combined_question_ids'])
    check,pred=records(run/'submission.jsonl',manifest)
    ref={}
    for line in reference.read_text().splitlines():
        r=json.loads(line); q=r['question_id']; a=r['answer']
        if q in ref or type(a) is not int or not 1<=a<=30: raise ValueError('invalid reference')
        ref[q]=a
    eligible=ids & ref.keys(); valid=eligible & pred.keys()
    matches=sorted(q for q in valid if pred[q]==ref[q]); disagreements=sorted(valid-set(matches))
    return {'reference_kind':'teacher_pseudo','reference_sha256':hashlib.sha256(reference.read_bytes()).hexdigest(),'planned':len(ids),'with_reference':len(eligible),'missing_reference':sorted(ids-ref.keys()),'valid_with_reference':len(valid),'matched':len(matches),'completion_rate':len(pred)/len(ids),'reference_match_completed':len(matches)/len(valid) if valid else None,'reference_match_all_eligible':len(matches)/len(eligible) if eligible else None,'matches':matches,'disagreements':disagreements,'uncompleted':sorted(ids-pred.keys()),'official_accuracy':None,'warning':'Teacher predictions are noisy pseudo-labels. These development questions are not an independent test set.'}


def repeat(a,b):
    ids_a=json.loads((a/'manifest.json').read_text())['combined_question_ids']; ids_b=json.loads((b/'manifest.json').read_text())['combined_question_ids']
    if ids_a!=ids_b: raise ValueError('repeat runs must have identical ordered question manifest')
    ca=json.loads((a/'run-config.json').read_text()); cb=json.loads((b/'run-config.json').read_text())
    if ca!=cb: raise ValueError('repeat configurations/source differ')
    _,pa=records(a/'submission.jsonl',a/'manifest.json'); _,pb=records(b/'submission.jsonl',b/'manifest.json')
    both=pa.keys() & pb.keys(); differences=sorted(q for q in both if pa[q]!=pb[q])
    return {'planned':len(ids_a),'both_completed':len(both),'different':differences,'difference_rate':len(differences)/len(both) if both else None,'incomplete':sorted(set(ids_a)-both),'independent_run_directories':a.resolve()!=b.resolve(),'all_answers_identical':not differences and len(both)==len(ids_a),'scope':'Same recorded config/source; verify both were independently inferred, not copied or resumed from each other.'}


def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('teacher'); s.add_argument('run',type=Path); s.add_argument('reference',type=Path)
    s=sub.add_parser('repeat'); s.add_argument('a',type=Path); s.add_argument('b',type=Path)
    p.add_argument('--output',type=Path,required=True); a=p.parse_args()
    value=evaluate(a.run,a.reference) if a.command=='teacher' else repeat(a.a,a.b)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(value,ensure_ascii=False))

if __name__=='__main__':main()
