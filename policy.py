#!/usr/bin/env python3
"""Publish a manually reviewed candidate as a NEW immutable policy bundle."""
import argparse,json,hashlib
from pathlib import Path
from contracts import require,text


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('candidate',type=Path);p.add_argument('--reviewer',required=True);p.add_argument('--review-note',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();require(text(a.reviewer) and text(a.review_note),'explicit human review record required')
    candidate=json.loads(a.candidate.read_text());require(candidate['status']=='pending_human_review','not a pending candidate')
    require(not a.output.exists(),'frozen bundle cannot be overwritten')
    # Publication intentionally requires an actual human to review generality,
    # evidence, and absence of question/answer memorization; CLI fields are not proof.
    card={'id':candidate['id'],**candidate['card']}
    bundle={'version':1,'status':'frozen','policies':[card],'review':{'reviewer':a.reviewer,'note':a.review_note,'candidate_sha256':hashlib.sha256(a.candidate.read_bytes()).hexdigest()}}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x') as f:json.dump(bundle,f,ensure_ascii=False,indent=2);f.write('\n')

if __name__=='__main__':main()
