#!/usr/bin/env python3
"""Record an actual human review and freeze selected experimental cards.
This command records review; supplying reviewer text is not a substitute for it.
"""
import argparse,json
from pathlib import Path
from common import read,hashfile
from contracts import require,text

def main():
    p=argparse.ArgumentParser();p.add_argument('--experimental',type=Path,required=True);p.add_argument('--comparison',type=Path,required=True);p.add_argument('--approved-ids',required=True,help='comma-separated IDs explicitly approved by reviewer; use none for empty bundle');p.add_argument('--reviewer',required=True);p.add_argument('--note',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    require(text(a.reviewer) and text(a.note),'actual reviewer and review note required');require(not a.output.exists(),'never overwrite a frozen bundle')
    exp=read(a.experimental);comparison=read(a.comparison);require(exp['status']=='experimental_not_human_approved','expected experimental bundle');require(comparison['status']=='pending_human_review' and comparison['paired_completed']==comparison['planned'],'finish paired validation before publication')
    require(comparison['experimental_bundle_sha256']==hashfile(a.experimental),'comparison is for a different experimental bundle')
    require(comparison['candidate_efficiency']['submission']['complete'],'candidate validation incomplete')
    ids=[] if a.approved_ids=='none' else a.approved_ids.split(',');known={c['id']:c for c in exp['policies']};require(len(ids)==len(set(ids)) and set(ids)<=known.keys(),'unknown/duplicate approved ID')
    bundle={'version':1,'status':'frozen','policies':[known[i] for i in ids],'review':{'reviewer':a.reviewer,'note':a.note,'approved_ids':ids,'experimental_sha256':hashfile(a.experimental),'comparison_sha256':hashfile(a.comparison),'reference_kind':'teacher_pseudo','official_accuracy':None}}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x') as f:json.dump(bundle,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'output':str(a.output),'cards':len(ids),'sha256':hashfile(a.output)}))
if __name__=='__main__':main()
