#!/usr/bin/env python3
"""Grade blind A/B runs outside solver containers; prepare a reviewable bundle."""
import argparse,collections,json
from pathlib import Path
from common import read,atomic,hashfile
from evaluate import evaluate,records
from contracts import require

def main():
    p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--reference',type=Path,required=True);p.add_argument('--cards',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    require(read(a.baseline/'manifest.json')==read(a.candidate/'manifest.json'),'paired manifests differ')
    ca=read(a.baseline/'run-config.json');cb=read(a.candidate/'run-config.json');require({k:v for k,v in ca.items() if k!='policy_bundle'}=={k:v for k,v in cb.items() if k!='policy_bundle'},'A/B configurations differ beyond policy bundle')
    aa=evaluate(a.baseline,a.reference);bb=evaluate(a.candidate,a.reference);atomic(a.output/'baseline-teacher.json',aa);atomic(a.output/'candidate-teacher.json',bb)
    _,pa=records(a.baseline/'submission.jsonl',a.baseline/'manifest.json');_,pb=records(a.candidate/'submission.jsonl',a.candidate/'manifest.json');ref={r['question_id']:r['answer'] for r in map(json.loads,a.reference.read_text().splitlines())}
    both=pa.keys()&pb.keys();improved=sorted(q for q in both if pa[q]!=ref[q] and pb[q]==ref[q]);regressed=sorted(q for q in both if pa[q]==ref[q] and pb[q]!=ref[q]);changed=sorted(q for q in both if pa[q]!=pb[q]);usage=collections.Counter()
    for path in (a.candidate/'questions').glob('*/planning.json'):usage.update(read(path)['plan']['policy_ids'])
    value={'experimental_bundle_sha256':hashfile(a.cards),'reference_kind':'teacher_pseudo','official_accuracy':None,'planned':aa['planned'],'paired_completed':len(both),'improved':improved,'regressed':regressed,'changed':changed,'baseline_agreement':aa['reference_match_all_eligible'],'candidate_agreement':bb['reference_match_all_eligible'],'policy_selection_counts':dict(usage),'baseline_efficiency':read(a.baseline/'summary.json'),'candidate_efficiency':read(a.candidate/'summary.json'),'status':'pending_human_review','automatic_publication':False}
    atomic(a.output/'comparison.json',value)
    cards=read(a.cards)['policies']
    lines=['# HOWM-OmniHarness 方法库审核包','','这是教师伪标签对照，不是官方准确率；64题与练习集按首尾状态分组，但共享部分候选视频。','','| 指标 | 空库 | 候选库 |','|---|---:|---:|',f"| 计划题数 | {aa['planned']} | {bb['planned']} |",f"| 完成题数 | {aa['valid_with_reference']} | {bb['valid_with_reference']} |",f"| 与教师一致（全部计划题作分母） | {aa['reference_match_all_eligible']:.2%} | {bb['reference_match_all_eligible']:.2%} |",'',f'配对变好 {len(improved)} 题，变差 {len(regressed)} 题，预测变化 {len(changed)} 题。详见 comparison.json；不能仅凭汇总提升认定每张卡有效。','','正式库尚未发布，完整265题的冻结库在线运行尚未启动。请审核每张卡的通用性、真实证据、适用边界、是否记忆题目、以及耗时与回退情况。','']
    for c in cards:
        lines.extend([f"## {c['id']}：{c['title']}",'',f"能力：{', '.join(c['capability_ids'])}；验证集中被 Planning 选用 {usage[c['id']]} 次。",'',f"适用：{c['when']}",'',*[f'{i+1}. {s}' for i,s in enumerate(c['procedure'])],'',f"限制：{c['limitations']}",''])
    (a.output/'REVIEW.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:value[k] for k in ('paired_completed','improved','regressed','status')},ensure_ascii=False))
if __name__=='__main__':main()
