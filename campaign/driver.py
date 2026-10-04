#!/usr/bin/env python3
"""Host orchestration: practice -> experimental distillation -> blind dev -> human review.
Never publishes policies, launches full265 with unreviewed cards, or alters GPU services.
"""
import argparse,fcntl,json,os,subprocess,sys,time,traceback
from pathlib import Path
from common import read,atomic,hashfile
from contracts import require
from audit_practice import audit

PROJECT=Path('/storage1/HOWM-LAB-Project')
IMAGE='howm-codex-qwen9b:20260930-visual-causal-v8'
DATA=PROJECT/'datasets/HomeHWM/extracted/HomeHWM_preliminary_questions_5000_schema_3_1'

def main():
    p=argparse.ArgumentParser();p.add_argument('--campaign',type=Path,required=True);p.add_argument('--practice',type=Path,required=True);p.add_argument('--wait-for-smoke',action='store_true');a=p.parse_args();root=a.campaign;practice=a.practice;source=practice/'source';logs=root/'logs';logs.mkdir(exist_ok=True)
    source_manifest={str(p.relative_to(source)):hashfile(p) for p in source.rglob('*') if p.is_file() and '__pycache__' not in str(p)}
    lock=(root/'.driver.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    def status(stage,**kw):atomic(root/'campaign-status.json',{'stage':stage,'updated_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'pid':os.getpid(),**kw})
    def docker(stage,script,out,args=(),mounts=()):
        out.mkdir(parents=True,exist_ok=True);receipt=root/(stage+'-exit.json')
        if receipt.exists() and read(receipt)['returncode']==0 and read(receipt).get('stage_complete',False):return
        status(stage,running=True)
        cmd=['docker','run','--rm','--init','--name','howm-omniharness-'+stage,'--user',f'{os.getuid()}:{os.getgid()}','-e','HOME=/tmp','--network','container:howm-omniharness-qwen9b','--entrypoint','/opt/vllm/bin/python','-v',f'{source}:/method:ro','-v',f'{out}:/work:rw','-v',f'{DATA}:/data:ro','-e','PYTHONDONTWRITEBYTECODE=1','-e','PYTHONHASHSEED=20260929']
        for host,container in mounts:cmd+=['-v',f'{host}:{container}:ro']
        cmd += [IMAGE,'/method/'+script,*args]
        atomic(root/(stage+'-command.json'),{'command':cmd,'teacher_reference_mounted':any('/corpus'==c for h,c in mounts)})
        start=time.monotonic()
        with (logs/(stage+'.log')).open('a') as log:result=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
        expected={'practice32':32,'practice201':201}.get(stage)
        complete=result.returncode==0 and (expected is None or read(out/'summary.json')['finished']>=expected)
        atomic(receipt,{'returncode':result.returncode,'seconds':time.monotonic()-start,'stage_complete':complete})
        require(complete,f'{stage} failed; inspect {logs/(stage+".log")}')
    try:
        if (root/'source-manifest.json').exists():require(read(root/'source-manifest.json')==source_manifest,'source snapshot changed')
        else:atomic(root/'source-manifest.json',source_manifest)
        # Wait for the separately supervised pilot to exit at a clean question boundary.
        if a.wait_for_smoke:
            deadline=time.monotonic()+8*2400+600
            while True:
                running=subprocess.run(['docker','inspect','--format','{{.State.Running}}','howm-omniharness-practice-265'],capture_output=True,text=True)
                if running.returncode or running.stdout.strip()!='true':break
                require(time.monotonic()<deadline,'smoke exceeded absolute campaign bound')
                status('supervised_practice8',running=True,practice=str(practice))
                time.sleep(10)
        # A supervised eight-question smoke batch is completed before handing off.
        smoke=root/'smoke8-summary.json'
        small=read(smoke) if smoke.exists() else read(practice/'summary.json')
        require(small['finished']>=8 and small['technical_failures']==0,'eight-question smoke gate not passed')
        if not smoke.exists():atomic(smoke,small)
        if not (root/'smoke8-audit.json').exists():atomic(root/'smoke8-audit.json',audit(practice))
        docker('practice32','campaign/practice_batch.py',practice,['--limit','32'],[(root/'practice-corpus','/corpus')])
        pilot_path=root/'pilot32-summary.json'
        pilot=read(pilot_path) if pilot_path.exists() else read(practice/'summary.json')
        require(pilot['finished']==32 and pilot['first_completed']>=31,'pilot completion gate failed')
        if not pilot_path.exists():atomic(pilot_path,pilot)
        if not (root/'pilot32-audit.json').exists():atomic(root/'pilot32-audit.json',audit(practice))
        require(read(root/'pilot32-audit.json')['feedback_technical_failures']<=1,'pilot feedback technical failures need inspection')
        # Live multi-case distillation check before committing the remaining practice budget.
        docker('distill-pilot','campaign/distill.py',root/'pilot-distillation',mounts=[(root/'practice-corpus','/corpus'),(practice,'/practice')])
        docker('practice201','campaign/practice_batch.py',practice,['--limit','201'],[(root/'practice-corpus','/corpus')])
        practice_summary=read(practice/'summary.json');require(practice_summary['finished']==201 and practice_summary['first_completed']>=200,'full practice incomplete')
        require(all(v['episodes'] for v in practice_summary['coverage'].values()),'one or more capabilities never practiced')
        full_audit=audit(practice);atomic(root/'practice201-audit.json',full_audit)
        require(full_audit['feedback_technical_failures']<=1,'feedback technical failures need inspection')
        docker('distill-final','campaign/distill.py',root/'distillation',mounts=[(root/'practice-corpus','/corpus'),(practice,'/practice')])
        policy=root/'distillation/experimental-policies.json'
        base=PROJECT/'outputs'/('howm-omniharness-dev64-empty-'+root.name.removeprefix('howm-omniharness-265-'))
        candidate=PROJECT/'outputs'/('howm-omniharness-dev64-candidate-'+root.name.removeprefix('howm-omniharness-265-'))
        for label,out,extra in [('empty',base,[]),('candidate',candidate,[(policy,'/policy.json')])]:
            args=['--manifest','/manifest.json']+(['--policy-file','/policy.json'] if extra else [])
            docker('dev64-'+label,'run.py',out,args,[(root/'manifests/dev.json','/manifest.json'),*extra])
            require(read(out/'summary.json')['completed']==64,f'dev64 {label}: missing valid predictions; technical repair required')
        # Host-only scoring; none of the private reference files are mounted online.
        compare=Path(__file__).with_name('compare.py')
        cmd=[sys.executable,str(compare),'--baseline',str(base),'--candidate',str(candidate),'--reference',str(root/'private/dev-reference.jsonl'),'--cards',str(policy),'--output',str(root/'review')]
        with (logs/'compare.log').open('a') as log:subprocess.run(cmd,check=True,stdout=log,stderr=subprocess.STDOUT)
        status('pending_human_review',running=False,review=str(root/'review/REVIEW.md'),full265_started=False,automatic_publication=False)
    except BaseException as exc:
        status('needs_inspection',running=False,error=f'{type(exc).__name__}: {exc}')
        (root/'driver-error.log').write_text(traceback.format_exc());raise
if __name__=='__main__':main()
