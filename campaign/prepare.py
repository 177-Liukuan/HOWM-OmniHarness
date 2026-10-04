"""Freeze split before reading teacher analyses; only practice-side traces exported."""
import argparse,hashlib,json,random
from collections import defaultdict,Counter
from pathlib import Path

ROOT=Path('/storage1/HOWM-LAB-Project')
SMOKE={'001','003','026','036','049','076','094','4884'}
TERMS={
 'C01':['状态','初始','不变','位置','变化','仍在','已在'],
 'C02':['遮挡','可见','不可见','看清','不清','视角','出画','可见性','无法确认'],
 'C03':['人物','施事','受事','被动','动物','狗','猫','反应','他人','失衡'],
 'C04':['部件','组件','整体','边框','面板','滤芯','端帽','工具','功能','对象'],
 'C05':['方向','安装','拆卸','取出','放入','接触','空间','关系','连接','支撑','对齐'],
 'C06':['先后','阶段','工序','顺序','时序','步骤','此前','之后','持续'],
 'C07':['危险','致因','触发','机制','起火','烧','电','滑倒','烫','因果'],
 'C08':['跨','背景','场景','身份','视角','外观','不同人物','功能对应'],
 'C09':['反证','近邻','区分','干扰','排除','矛盾','粒度','相似'],
 'C10':['细查','补证据','抽帧','不确定','看不清','确认','无法','未知','不清楚']}

def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()
def write(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output
 if (out/'split.json').exists():raise SystemExit('Frozen split exists; do not overwrite')
 data=ROOT/'datasets/HomeHWM/extracted/HomeHWM_preliminary_questions_5000_schema_3_1'
 original=ROOT/'evaluations/codex-gpt6-factor-agent-265-20260929'
 manifest=original/'sampling_manifest.json';ids=json.loads(manifest.read_text())['combined_question_ids']
 assert len(ids)==len(set(ids))==265
 groups=defaultdict(list);media={};questions={}
 for qid in ids:
  qdir=data/qid;q=json.loads((qdir/'question.json').read_text());questions[qid]=q
  pair=tuple(digest(qdir/q[x]) for x in ('init','final'));groups[pair].append(qid)
  # Hash referenced original media afresh, rather than trusting stale file sizes.
  media[qid]={'question_sha256':digest(qdir/'question.json'),'state_pair':pair,'videos':{o['option_id']:digest(qdir/o['video']) for o in q['options']}}
 eligible=[sorted(g,key=int) for _,g in sorted(groups.items()) if not SMOKE.intersection(g)]
 random.Random(20261004).shuffle(eligible)
 prefixes=[];dev=[]
 for i,g in enumerate(eligible):
  dev+=g
  if 50<=len(dev)<=100:prefixes.append((abs(len(dev)-64),i+1,len(dev)))
 _,prefix_count,_=min(prefixes)
 dev=sorted([q for g in eligible[:prefix_count] for q in g],key=int);practice=sorted(set(ids)-set(dev),key=int)
 assert not SMOKE.intersection(dev)
 assert not {tuple(media[q]['state_pair']) for q in dev}&{tuple(media[q]['state_pair']) for q in practice}
 for name,seq in [('all265',ids),('practice',practice),('dev',dev)]:write(out/'manifests'/f'{name}.json',{'combined_question_ids':seq})
 pv={v for q in practice for v in media[q]['videos'].values()};dv={v for q in dev for v in media[q]['videos'].values()}
 write(out/'input-fingerprints.json',media)
 split={'seed':20261004,'source_manifest_sha256':digest(manifest),'practice_ids':practice,'dev_ids':dev,'practice_count':len(practice),'dev_count':len(dev),'same_state_groups':list(groups.values()),'state_overlap':0,'shared_video_hashes':len(pv&dv),'scope':'Internal development split of already explored 265; not an independent test set.'}
 write(out/'split.json',split)
 reference=original/'official-submission-001/submission.jsonl';ref=[json.loads(l) for l in reference.read_text().splitlines()]
 assert {r['question_id'] for r in ref}==set(ids)
 for name,seq in [('practice',practice),('dev',dev),('all265',ids)]:
  path=out/'private'/f'{name}-reference.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
  path.write_text(''.join(json.dumps(r,separators=(',',':'))+'\n' for r in ref if r['question_id'] in seq))
 # Only NOW read analyses, only for the practice partition.
 index=[];safe_traces={}
 analysis_root=ROOT/'outputs/codex-gpt6-factor-agent-265-20260929/analysis'
 for qid in practice:
  trace=json.loads((analysis_root/f'{qid}.json').read_text())
  text='；'.join(str(trace.get(k,'')) for k in ('state_change','key_factors','uncertainty'))
  matches={c:[t for t in terms if t in text] for c,terms in TERMS.items()}
  tags=[c for c,m in matches.items() if m]
  index.append({'question_id':qid,'capability_tags':tags,'tag_evidence':{c:m for c,m in matches.items() if m},'tag_kind':'keyword_weak_tags_not_skill_ground_truth','topic':trace.get('state_change',''),'factors':trace.get('key_factors',[]),'uncertainty':trace.get('uncertainty',[]),'state_group':hashlib.sha256(str(media[qid]['state_pair']).encode()).hexdigest()})
  safe_traces[qid]={k:trace[k] for k in ('question_id','instruction','initial_observation','final_observation','state_change','candidates','shortlist','reason','key_factors','uncertainty','evidence_files') if k in trace}
  write(out/'practice-corpus/analyses'/f'{qid}.json',safe_traces[qid])
 write(out/'practice-corpus/index.json',index)
 write(out/'practice-corpus/capabilities.json',json.loads((ROOT/'methods/howm-omniharness/config/capabilities.json').read_text()))
 (out/'practice-corpus/reference.jsonl').write_bytes((out/'private/practice-reference.jsonl').read_bytes())
 write(out/'practice-corpus/manifest.json',{'combined_question_ids':practice})
 write(out/'practice-corpus/fingerprints.json',{q:media[q] for q in practice})
 write(out/'reference-provenance.json',{'reference_kind':'teacher_pseudo','original_sha256':digest(reference),'original_count':265,'official_aggregate_correct':241,'per_question_gt_available':False})
 write(out/'capability-index-summary.json',{'questions':len(index),'weak_tag_counts':dict(Counter(c for r in index for c in r['capability_tags'])),'dev_traces_exported':False})
 print(json.dumps({'practice':len(practice),'dev':len(dev),'shared_video_hashes':len(pv&dv),'output':str(out)}))

if __name__=='__main__':main()
