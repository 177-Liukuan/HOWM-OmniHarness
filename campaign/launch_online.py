#!/usr/bin/env python3
"""Launch a fresh online manifest run with an explicitly human-reviewed frozen bundle."""
import argparse,json,os,shutil,subprocess
from pathlib import Path
from common import read,hashfile
from contracts import require
from run import frozen

def main():
    p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--policy',type=Path,required=True);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--container-name',required=True);a=p.parse_args()
    bundle=read(a.policy);require(bundle['status']=='frozen' and bundle.get('review'),'explicitly reviewed frozen bundle required')
    ids=read(a.manifest)['combined_question_ids'];require(len(ids)==265 and len(set(ids))==265,'this entrypoint is for fixed original265 manifest')
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    record={'manifest_sha256':hashfile(a.manifest),'policy_sha256':hashfile(a.policy),'source':str(a.source.resolve()),'container_name':a.container_name}
    frozen(out/'launch-config.json',record)
    if not (out/'source').exists():shutil.copytree(a.source,out/'source',ignore=shutil.ignore_patterns('__pycache__'))
    for src,dest in [(a.manifest,out/'input-manifest.json'),(a.policy,out/'frozen-policies.json')]:
        if dest.exists():require(hashfile(dest)==hashfile(src),'frozen inputs changed')
        else:shutil.copy2(src,dest)
    cmd=['docker','run','-d','--init','--name',a.container_name,'--user',f'{os.getuid()}:{os.getgid()}','-e','HOME=/tmp','--network','container:howm-omniharness-qwen9b','--entrypoint','/opt/vllm/bin/python','-v',f'{out}/source:/method:ro','-v',f'{out}:/work:rw','-v','/storage1/HOWM-LAB-Project/datasets/HomeHWM/extracted/HomeHWM_preliminary_questions_5000_schema_3_1:/data:ro','-e','PYTHONDONTWRITEBYTECODE=1','-e','PYTHONHASHSEED=20260929','howm-codex-qwen9b:20260930-visual-causal-v8','/method/run.py','--manifest','/work/input-manifest.json','--policy-file','/work/frozen-policies.json']
    # No teacher corpus or labels mounted. Keep exited container for inspect/logs.
    subprocess.run(cmd,check=True)
if __name__=='__main__':main()
