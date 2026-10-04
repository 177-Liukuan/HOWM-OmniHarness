#!/usr/bin/env python3
"""HOWM-OmniHarness: Planning -> Observe all 30 -> Review -> issue-driven evidence."""
import argparse,fcntl,hashlib,json,math,os,subprocess,sys,time,traceback,urllib.request
from pathlib import Path
import contracts
from codex_team import CodexTeam,run_process
from evidence import load_question,acquire
from state import Budget,atomic
from vendor import submission_io as submission
from vendor.prepare_evidence import IMAGE_NAMES

METHOD=Path(__file__).resolve().parent


def frozen(path,value):
    if path.exists(): contracts.require(json.loads(path.read_text())==value,'run configuration/source changed: use a new output directory')
    else: atomic(path,value)


def preflight():
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=10) as f: models=json.load(f)
    contracts.require(any(m['id']=='howm-qwen35-9b' and m.get('max_model_len')==131072 for m in models['data']),'wrong model/context')
    return {'models':models,'codex':subprocess.check_output(['codex','--version'],text=True).strip(),'python':sys.version,'ffmpeg':subprocess.check_output(['ffmpeg','-version'],text=True).splitlines()[0]}


def run_question(data,out,qid,config,practice_focus=None,commit=True,policy_bundle=None,feedback=False):
    contracts.require(isinstance(qid,str) and qid.isascii() and qid.isdecimal() and int(qid)>0 and qid==str(int(qid)).zfill(3),'invalid raw question_id')
    root=out/'questions'/qid; root.mkdir(parents=True,exist_ok=True)
    b=Budget(root/'state.json',config); s=b.s
    if s['status']=='failed': return s
    if feedback and s['status']=='completed':
        if s.get('feedback_used'): return s
        s['first_pass_answer']=s['answer']; s['feedback_used']=True
        if (s['attempts']>=min(s['limits']['attempts'],config['hard']['attempts']) or s['turns']+2>min(s['limits']['turns'],config['hard']['turns']) or s['finish_only'] or b.elapsed()>=s['limits']['seconds']):
            s['feedback_skipped']='no_remaining_budget'; b.save(); return s
        s['attempts']+=1; s['phase']='review'; s['status']='running'; s['revision']+=1
        s['feedback_pending']=True
        s['result_versions']={k:{'revision':s['revision'],'status':'stale'} for k in s.get('result_versions',{})}
        b.save()
    elif s['status']=='completed':
        return s
    team=CodexTeam(root,METHOD,s)
    evdir=out/'evidence'/qid
    try:
        q,directory=load_question(data,qid)
        question_hash=hashlib.sha256((directory/'question.json').read_bytes()).hexdigest()
        if 'question_sha256' in s: contracts.require(s['question_sha256']==question_hash,'input changed during resume')
        s['question_sha256']=question_hash
        if s['phase']=='base':
            timeout=b.reserve('prepare')
            command=[sys.executable,str(METHOD/'vendor/prepare_evidence.py'),'--start',str(int(qid)),'--count','1','--workers','1','--data',str(data),'--output',str(out/'evidence')]
            run_process(command,'',root,os.environ,root/'prepare.log',root/'prepare.stderr',timeout)
            b.release()
            manifest=json.loads((evdir/'manifest.json').read_text())
            s['evidence']={x['file']:x for x in manifest['images']}; s['phase']='planning'; b.save()
        policies=(policy_bundle if policy_bundle is not None else json.loads((METHOD/'config/policies.json').read_text()))['policies']
        def call(role,validator,extra):
            error=None
            call_key=f'{role}:{s["revision"]}:{s["attempts"]}'
            tries=s.setdefault('role_tries',{}).get(call_key,0)
            contracts.require(tries<2,'protocol retry budget exhausted for '+call_key)
            for retry in range(tries,2):
                image_names=(['states.jpg']+list(s.get('review_boards',{}).values())+s.get('last_batch',[])) if role=='review' and config.get('focused_review',{}).get('enabled') else list(IMAGE_NAMES)+s.get('last_batch',[])
                image_names=list(dict.fromkeys(image_names)); images=[evdir/n for n in image_names]
                payload={'question_id':qid,'instruction':q['instruction'],'options':[{'option_id':o['option_id'],'duration_seconds':o['duration_seconds']} for o in q['options']],
                         'evidence_catalog':list(s['evidence']),'evidence_details':[{ 'evidence_id':n,'request':s['evidence'][n]['request'],**{k:s['evidence'][n][k] for k in ('actual_times','actual_time','pixel_bbox','original_size') if k in s['evidence'][n]}} for n in image_names if 'request' in s['evidence'][n]],'current_image_ids':image_names,'revision':s['revision'],**extra}
                if error: payload['protocol_error']=error; payload['repair_instruction']='只修正具体格式/引用问题，仍需返回完整本角色 JSON。'
                s['role_tries'][call_key]=retry+1
                timeout=b.reserve('turns',review=role=='review')
                try:
                    value=team.call(role,payload,images,s['turns'],timeout)
                    if role=='planning':value=contracts.bind_planning(value)
                    if role in ('observation','review'):contracts.bind_comparison_ranking(value,review=role=='review')
                    if role=='observation':
                        contracts.bind_base_provenance(value)
                        changes=contracts.bind_observation(value,s['planning'])
                        atomic(root/f'binding-{s["turns"]:03d}.json',changes)
                    validator(value)
                    if role=='review' and config.get('focused_review',{}).get('enabled'):
                        contracts.require(set(image_names)<=set(value['reviewed_evidence']),'Review must account for every supplied focused image, including unknowns')
                        board=s.get('review_boards',{}).get(str(value['best_candidate']))
                        if board:contracts.require(board in value['decision']['evidence_refs'],f"Candidate {value['best_candidate']} corresponds to board {board}, but decision references {value['decision']['evidence_refs']}. Check the visible Option labels: correct either the candidate number or the actual evidence citation based on what you see; do not cite an unrelated board.")
                    b.release(); return value
                except (ValueError,KeyError,TypeError) as exc:
                    b.release(); error=f'{type(exc).__name__}: {exc}'
                    atomic(root/f'protocol-error-{s["turns"]:03d}.json',{'error':error})
                    if retry or not s.get('session_id'): raise
                    s['protocol_retries']+=1; b.save()
            raise RuntimeError(error)
        if s['phase']=='planning':
            s['planning']=call('planning',lambda v:contracts.plan(v,qid,{p['id'] for p in policies}),{'policies':policies,'practice_focus':practice_focus})
            atomic(root/'planning.json',s['planning']); s['phase']='observation'; b.save()
        if s['phase']=='observation':
            s['observation']=call('observation',lambda v:contracts.observation(v,qid,s['planning'],s['evidence']),{'planning':s['planning']})
            contracts.require('main_competitors' in s['observation']['comparison'] and 'not_ruled_out' in s['observation']['comparison'],'focused shortlist missing')
            atomic(root/'observation.json',s['observation']); s['phase']='review_prepare' if config.get('focused_review',{}).get('enabled') else 'review'; b.save()
        if s['phase']=='review_prepare':
            focused=config['focused_review']; selected,deferred=contracts.review_candidates(s['observation'],focused['max_initial_candidates'])
            s['focus_candidates']=selected; s['deferred_candidates']=deferred; s.setdefault('review_boards',{})
            s['limits']['requests']=min(config['hard']['requests'],len(selected)+focused['interactive_request_allowance']); b.save()
            for oid in selected:
                if str(oid) in s['review_boards']:continue
                duration=float(q['options'][oid-1]['duration_seconds'])
                request={'action':'sample_video','option_id':oid,'time_range_seconds':[duration*focused['start_fraction'],duration*focused['end_fraction']],'frames':focused['frames_per_candidate'],'affected_candidates':selected,'expected_observation':'复核候选实际动作、时序、作用部位及与首尾状态的关系','decision_impact':'支持或否定初选及主要竞争候选；不以图板列表限制最终答案'}
                b.reserve('requests'); s['automatic_requests']=s.get('automatic_requests',0)+1; b.save()
                name,meta=acquire(q,directory,request,evdir,lambda:b.remaining()); meta['origin']='automatic_review_board'
                s['evidence'][name]=meta; s['review_boards'][str(oid)]=name; s['pending_evidence'].append(name); s['seen_requests'].append(contracts.request_key(request))
                s['automatic_media_seconds']=s.get('automatic_media_seconds',0)+meta['execution_seconds']; s['decoded_frames']=s.get('decoded_frames',0)+meta.get('decoded_frames',0)
                b.release()
            atomic(root/'review-inputs.json',{'candidate_boards':s['review_boards'],'automatic_candidates':selected,'deferred_not_excluded':deferred,'frames_per_candidate':focused['frames_per_candidate'],'automatic_requests':s.get('automatic_requests',0),'automatic_media_seconds':s.get('automatic_media_seconds',0)})
            s['phase']='review'; b.save()
        while s['phase']=='review':
            extra={'focused_candidate_boards':s.get('review_boards',{}),'deferred_not_excluded':s.get('deferred_candidates',[]),'planning':s['planning'],'observation':s['observation'],'pending_evidence':s['pending_evidence'],'previous_review':s.get('review'),'current_observation_updates':s.get('current_observation_updates',[]),'required_checks':[{"step_id":step["step_id"],"checks":[{"index":i+1,"criterion":c,"instruction":"分别填写本项检查结论；标准本身不成立则说明不适用，不要删掉这一项"} for i,c in enumerate(step["criteria"])]} for step in s['planning']['plan']['steps']]}
            if s.get('feedback_pending'):
                extra['practice_feedback']={'reference_kind':'teacher_pseudo','reference_match':False,'instruction':'本次预测与教师伪标签不一致，不代表一定错误。参考选项不会提供。回看实际媒体，识别可检验的误读或具体疑点；不因反馈任意换答案。如无可区分证据，保留未知。'}
            result=call('review',lambda v:contracts.review(v,qid,s['planning'],s['evidence'],s['revision'],s['pending_evidence']),extra)
            s['review']=result; s['pending_evidence']=[]; s['feedback_pending']=False
            s.setdefault('current_observation_updates',[]).extend(result['observation_updates'])
            s['result_versions']={step['step_id']:{'revision':s['revision'],'status':'valid'} for step in s['planning']['plan']['steps']}
            if s['revision']:
                previous_facts={u['fact'] for u in s['current_observation_updates'][:-len(result['observation_updates'])]} if result['observation_updates'] else {u['fact'] for u in s['current_observation_updates']}
                progress=any(u['fact'] not in previous_facts and set(u['evidence_refs']).intersection(s.get('last_batch',[])) for u in result['observation_updates'])
                s['no_progress_batches']=0 if progress else s.get('no_progress_batches',0)+1
                s['last_batch_progress']=progress
            atomic(root/f'review-{s["revision"]:02d}.json',result); b.save()
            requests,reason=contracts.route(result,q,s['planning'],set(s['seen_requests']))
            if requests and s.get('no_progress_batches',0)>=2:
                requests=[]; reason='no_progress'
            if requests:
                budget_reason=b.admit(requests,result)
                if budget_reason: requests=[]; reason=budget_reason
            if not requests:
                s['stop_reason']=reason; s['unresolved']=(result['decision']['uncertain']+([result['unresolved_issue']] if result['decision_status']=='needs_evidence' and result['unresolved_issue'] else [])); s['phase']='commit'; b.save(); break
            # Immediately invalidate the old decision. All new evidence must be reviewed.
            s['revision']+=1; s['phase']='acquiring'; s['last_batch']=[]
            s['result_versions']={step['step_id']:{'revision':s['revision'],'status':'stale'} for step in s['planning']['plan']['steps']}
            b.save()
            for r in requests:
                b.reserve('requests'); s['requested_requests']=s.get('requested_requests',0)+1; b.save()
                name,meta=acquire(q,directory,r,evdir,lambda:b.remaining())
                meta['origin']='review_requested'; s['evidence'][name]=meta; s['last_batch'].append(name); s['pending_evidence'].append(name)
                s['requested_media_seconds']=s.get('requested_media_seconds',0)+meta['execution_seconds']; s['decoded_frames']=s.get('decoded_frames',0)+meta.get('decoded_frames',0)
                s['seen_requests'].append(contracts.request_key(r)); b.release()
            s['phase']='review'; b.save()
        if s['phase']=='commit':
            contracts.require(not s['pending_evidence'] and s['review']['revision']==s['revision'],'unreviewed/stale evidence')
            contracts.require(b.elapsed()<config['hard']['seconds'],'hard_time')
            if commit:
                submission.append_answer(out/'submission.jsonl',qid,s['review']['best_candidate'],out/'manifest.json')
            s['status']='completed'; s['answer']=s['review']['best_candidate']; s['ground_truth']=None; s['is_correct']=None
        else: raise RuntimeError('incomplete operation recovery deferred; never reset budget')
    except Exception as exc:
        b.release()
        s['status']='failed'; s['error']=f'{type(exc).__name__}: {exc}'; s['stop_reason']='technical_failure'
        (root/'error.log').write_text(traceback.format_exc())
        if isinstance(exc,subprocess.TimeoutExpired) or 'budget' in str(exc) or 'hard_time' in str(exc): s['stop_reason']='budget_exhausted_without_valid_decision'
    finally: b.save()
    return s


def distribution(xs):
    if not xs: return {'n':0,'mean':None,'p50':None,'p95':None}
    xs=sorted(xs); return {'n':len(xs),'mean':sum(xs)/len(xs),'p50':xs[math.ceil(.5*len(xs))-1],'p95':xs[math.ceil(.95*len(xs))-1]}


def summarize(out,wall,gpus):
    states=[json.loads(p.read_text()) for p in (out/'questions').glob('*/state.json')]
    done=[s for s in states if s['status'] in ('completed','failed')]; n=len(done)
    def group(ss): return {k:distribution([s[k] for s in ss]) for k in ('turns','requests','elapsed')}
    result={'started':len(states),'completed':sum(s['status']=='completed' for s in done),'failed':sum(s['status']=='failed' for s in done),'running':len(states)-n,
            'fast_path_share':sum(not s['entered_slow_path'] for s in states)/len(states) if states else None,'slow_path_share':sum(s['entered_slow_path'] for s in states)/len(states) if states else None,
            'all_terminal':group(done),'groups':{key:{'count':len(ss),'completed':sum(s['status']=='completed' for s in ss),**group(ss)} for key,ss in [('fast',[s for s in done if not s['entered_slow_path']]),('slow',[s for s in done if s['entered_slow_path']]),('success',[s for s in done if s['status']=='completed']),('failure',[s for s in done if s['status']=='failed'])]},
            'automatic_requests':distribution([s.get('automatic_requests',0) for s in done]),'review_requested_requests':distribution([s.get('requested_requests',0) for s in done]),'automatic_media_seconds':distribution([s.get('automatic_media_seconds',0) for s in done]),'requested_media_seconds':distribution([s.get('requested_media_seconds',0) for s in done]),'additional_decoded_frames':distribution([s.get('decoded_frames',0) for s in done]),'request_accounting':'requests includes automatic boards and Review requests; base 30-video preparation is included in elapsed time; additional_decoded_frames excludes the fixed 90 base frames',
            'budget_terminated':sum(s['stop_reason'].startswith(('soft_','hard_','budget_','finish_only')) for s in done),'extension_events':sum(s['extensions'] for s in states),'measured_wall_seconds':wall,'allocated_gpus':gpus,
            'estimated_5000_firstpass_wall_hours':5000*wall/n/3600 if n else None,'estimated_5000_firstpass_allocated_gpu_hours':gpus*5000*wall/n/3600 if n else None,
            'estimate_scope':'Sequential, includes failures and preprocessing; not a promise of 5000 valid answers. GPU allocation, not kernel busy time. Small development sample.', 'accuracy':None,
            'submission':submission.validate_submission(out/'submission.jsonl',out/'manifest.json')}
    atomic(out/'summary.json',result); return result


def main():
    p=argparse.ArgumentParser(); p.add_argument('--data',type=Path,default=Path('/data')); p.add_argument('--output',type=Path,default=Path('/work')); p.add_argument('--ids',default='001'); p.add_argument('--all',action='store_true'); p.add_argument('--manifest',type=Path); p.add_argument('--policy-file',type=Path);
    a=p.parse_args(); out=a.output.resolve(); out.mkdir(parents=True,exist_ok=True)
    with (out/'.runner.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        config=json.loads((METHOD/'config/runtime.json').read_text())
        ids=json.loads(a.manifest.read_text())['combined_question_ids'] if a.manifest else ([f'{i:03d}' for i in range(1,5001)] if a.all else a.ids.split(','))
        policy_bundle=json.loads((a.policy_file or METHOD/'config/policies.json').read_text())
        contracts.require(len(ids)==len(set(ids)) and ids,'duplicate/empty question list')
        frozen(out/'manifest.json',{'combined_question_ids':ids})
        source={str(f.relative_to(METHOD)):hashlib.sha256(f.read_bytes()).hexdigest() for f in METHOD.rglob('*') if f.is_file() and '__pycache__' not in str(f) and f.suffix!='.pyc'}
        frozen(out/'run-config.json',{'source_sha256':source,'config':config,'ids':ids,'data':str(a.data),'concurrency':1,'policy_bundle':policy_bundle})
        if not (out/'preflight.json').exists(): atomic(out/'preflight.json',preflight())
        valid=submission.validate_submission(out/'submission.jsonl',out/'manifest.json'); submission.require_clean(valid)
        completed=set(ids)-set(valid['missing'])
        prior=json.loads((out/'run-time.json').read_text())['seconds'] if (out/'run-time.json').exists() else 0
        start=time.monotonic()
        for qid in ids:
            if qid in completed: continue
            s=run_question(a.data,out,qid,config,policy_bundle=policy_bundle)
            wall=prior+time.monotonic()-start; atomic(out/'run-time.json',{'seconds':wall})
            summarize(out,wall,config['gpus'])
            if (out/'STOP_AFTER_QUESTION').exists():
                print('Stopped at question boundary by STOP_AFTER_QUESTION',flush=True); break
            print(json.dumps({'question_id':qid,'status':s['status'],'turns':s['turns'],'requests':s['requests'],'seconds':s['elapsed'],'answer':s.get('answer'),'error':s.get('error')},ensure_ascii=False),flush=True)
        result=summarize(out,prior+time.monotonic()-start,config['gpus'])
        print(json.dumps({'completed':result['completed'],'failed':result['failed']},ensure_ascii=False),flush=True)
        return 0 if result['submission']['complete'] else 1

if __name__=='__main__': sys.exit(main())
