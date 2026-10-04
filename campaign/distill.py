#!/usr/bin/env python3
"""Distill experimental cards from multiple completed practice cases and real images."""
import argparse,hashlib,json,re
from pathlib import Path
from common import read,atomic,hashfile,model_call
from contracts import require
from run import METHOD

def main():
    p=argparse.ArgumentParser();p.add_argument('--corpus',type=Path,default=Path('/corpus'));p.add_argument('--practice',type=Path,default=Path('/practice'));p.add_argument('--output',type=Path,default=Path('/work'));a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    config=read(METHOD/'config/runtime.json');caps=read(METHOD/'config/capabilities.json');index={v['question_id']:v for v in read(a.corpus/'index.json')}
    records={p.parent.name:read(p) for p in (a.practice/'episodes').glob('*/record.json')};bundle=[];skipped=[]
    for cap in caps:
        eligible=[q for q,r in records.items() if r['first_completed'] and cap['id'] in index[q]['capability_tags']]
        # Interleave matches and mismatches; no accuracy-based cherry-picked success-only context.
        good=sorted(q for q in eligible if records[q]['first_match']);bad=sorted(q for q in eligible if not records[q]['first_match']);ordered=[]
        while good or bad:
            if good:ordered.append(good.pop(0))
            if bad:ordered.append(bad.pop(0))
        selected=[];groups=set()
        for q in ordered:
            group=index[q]['state_group']
            if group not in groups:selected.append(q);groups.add(group)
            if len(selected)==6:break
        if len(selected)<4:skipped.append({'capability':cap['id'],'reason':'fewer_than_four_distinct_state_cases'});continue
        cases=[];images=[];catalog=[]
        for q in selected:
            ep=a.practice/'episodes'/q;first=read(ep/'first-pass.json');teacher=read(a.corpus/'analyses'/f'{q}.json')
            candidate_image=next((name for name,meta in reversed(list(first['evidence'].items())) if meta.get('request',{}).get('action')=='sample_video' and meta['request']['option_id']==first['answer']),f"options_{((first['answer']-1)//10)*10+1:02d}_{((first['answer']-1)//10)*10+10:02d}.jpg")
            names=['states.jpg',candidate_image]
            evidence=[]
            for name in names:
                key=f'{q}/{name}';catalog.append(key);evidence.append(key);images.append(ep/'solve/evidence'/q/name)
            cases.append({'question_id':q,'capability_tags':index[q]['capability_tags'],'first_reference_match':records[q]['first_match'],'final_reference_match':records[q]['final_match'],'student_states':first['observation']['states'],'student_review':first['review'],'teacher_interpretation_not_ground_truth':{k:teacher.get(k) for k in ('state_change','key_factors','uncertainty')},'evidence_ids':evidence})
        payload={'capability':cap,'reference_kind':'teacher_pseudo','official_correctness':None,'cases':cases,'evidence_catalog':catalog,'image_order':catalog}
        def validate(v):
            support=v['support_cases'];counter=[x['question_id'] for x in v['counterexamples']]
            require(len(set(support))>=3 and set(support)<=set(selected),'need at least three distinct support cases')
            require(set(counter)<=set(selected) and set(counter)-set(support),'include at least one counterexample not in support')
            require(cap['id'] in v['card']['capability_ids'],'card must cover selected capability')
            require(all(x['evidence_id'] in catalog for x in v['visual_checks']),'unknown visual evidence')
            body=json.dumps(v['card'],ensure_ascii=False)
            require(not re.search(r'(?:题号|question_id|选项|候选)\s*[#:：]?\s*\d+',body),'card body must not memorize original question/answer IDs')
            if v['card']['kind']=='method':require(sum(records[q]['first_match'] for q in set(support))>=2,'method needs at least two first-pass matching support cases; otherwise failure lesson')
        result=model_call(a.output/'calls'/cap['id'],'distill_batch',payload,images,config,validate)
        digest=hashlib.sha256(json.dumps(result['card'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
        card={'id':'candidate-'+digest,**result['card']};bundle.append(card)
        directory=a.output/'candidates';directory.mkdir(exist_ok=True)
        atomic(directory/(card['id']+'.json'),{'id':card['id'],'status':'pending_cross_question_validation_and_human_review','card':result['card'],'provenance':{k:v for k,v in result.items() if k!='card'},'reference_kind':'teacher_pseudo','official_correctness':None})
        print(json.dumps({'capability':cap['id'],'candidate':card['id'],'cases':selected},ensure_ascii=False),flush=True)
    require(bundle,'no valid multi-case candidate cards')
    atomic(a.output/'experimental-policies.json',{'version':1,'status':'experimental_not_human_approved','policies':bundle,'note':'Only for blind internal-dev comparison. Formal publication requires human review.'})
    atomic(a.output/'summary.json',{'candidate_cards':len(bundle),'skipped':skipped,'human_approved':False})
if __name__=='__main__':main()
