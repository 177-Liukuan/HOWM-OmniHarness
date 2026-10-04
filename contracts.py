"""Small executable contracts. No model calls for validation or routing."""
import json, math

class ContractError(ValueError): pass

def require(ok, message):
    if not ok: raise ContractError(message)

def text(value): return isinstance(value,str) and bool(value.strip())
def option(value): return type(value) is int and 1 <= value <= 30

def refs(value, evidence):
    require(isinstance(value,list) and len(value)>0,'nonempty evidence_refs required')
    require(all(isinstance(x,str) and x in evidence for x in value),'unknown evidence reference')

def plan(value, qid, policies):
    require(value.get('question_id')==qid,'wrong question_id')
    p=value['plan']; d=value['draft']; steps=p['steps']
    require(p['strategy'] in ('build','reuse','adapt','compose'),'invalid strategy')
    require(isinstance(p['policy_ids'],list) and all(x in policies for x in p['policy_ids']),'unknown policy')
    require(p['strategy']=='build' or p['policy_ids'],'reuse/adapt/compose requires policy')
    require(text(p['rationale']),'missing planning rationale')
    require(isinstance(steps,list) and 3<=len(steps)<=24,'plan requires 3..24 steps')
    seen=set(); operations=set(); ancestors={}; operation_by_id={}
    for s in steps:
        require(text(s['step_id']) and s['step_id'] not in seen,'duplicate/invalid step_id')
        require(isinstance(s['depends_on'],list) and all(x in seen for x in s['depends_on']),'dependency not earlier step')
        require(s['operation'] in ('observe_states','observe_candidates','compare'),'unsupported operation')
        require(s['operation'] not in operations,'minimal runtime supports one step per operation; merge repeated focus/criteria into the existing step')
        require(text(s['focus']) and isinstance(s['criteria'],list) and s['criteria'] and all(text(x) for x in s['criteria']),'missing focus/criteria')
        parents=set(s['depends_on'])
        closure=parents | set().union(*(ancestors[x] for x in parents))
        upstream={operation_by_id[x] for x in closure}
        if s['operation']=='observe_candidates': require('observe_states' in upstream,'candidate observation must depend on state observation')
        if s['operation']=='compare': require({'observe_states','observe_candidates'}<=upstream,'comparison must depend on both observation stages')
        ancestors[s['step_id']]=closure; operation_by_id[s['step_id']]=s['operation']
        seen.add(s['step_id']); operations.add(s['operation'])
    require(operations=={'observe_states','observe_candidates','compare'},'missing required operations')
    bindings=d['bindings']
    require(len(bindings)==len(steps) and {b['step_id'] for b in bindings}==seen,'bindings must cover plan')
    for b in bindings:
        operation=next(s['operation'] for s in steps if s['step_id']==b['step_id'])
        expected=['init','final'] if operation=='observe_states' else list(range(1,31))
        require(b['targets']==expected,'binding must preserve full original coverage')
    require(isinstance(d['allowed_evidence_actions'],list) and set(d['allowed_evidence_actions'])<= {'sample_video','crop_evidence'},'unsupported evidence action')
    return value

def observation(v,qid,p,evidence):
    require(v.get('question_id')==qid,'wrong question_id')
    byop={op:{s['step_id'] for s in p['plan']['steps'] if s['operation']==op} for op in ('observe_states','observe_candidates','compare')}
    state=v['states']; require(state['step_id'] in byop['observe_states'],'wrong state step')
    for key in ('initial','final','change','invariants','uncertain'): require(isinstance(state[key],str),'state fields must be text')
    refs(state['evidence_refs'],evidence); require('states.jpg' in state['evidence_refs'],'state image required')
    opts=v['candidates']; require(len(opts)==30 and {x['option_id'] for x in opts}==set(range(1,31)),'all 30 candidates required exactly once')
    for x in opts:
        require(option(x['option_id']) and x['step_id'] in byop['observe_candidates'],'invalid option step')
        require(text(x['observed']) and text(x['comparison']) and isinstance(x['uncertain'],str),'missing candidate observation/comparison')
        refs(x['evidence_refs'],evidence)
        page=f"options_{((x['option_id']-1)//10)*10+1:02d}_{((x['option_id']-1)//10)*10+10:02d}.jpg"
        require(page in x['evidence_refs'],'candidate must cite its actual base page')
    comp=v['comparison']; require(comp['step_id'] in byop['compare'] and option(comp['best_candidate']),'invalid comparison')
    require(text(comp['reason']),'missing comparison reason'); refs(comp['evidence_refs'],evidence)
    # Legacy records remain readable; new runs explicitly require the shortlist.
    if 'main_competitors' in comp or 'not_ruled_out' in comp:
        for key,minimum,maximum in [('main_competitors',1,5),('not_ruled_out',0,30)]:
            entries=comp[key]; ids=[x['option_id'] for x in entries]
            require(minimum<=len(entries)<=maximum and len(ids)==len(set(ids)) and all(option(i) and (key=='not_ruled_out' or i!=comp['best_candidate']) for i in ids),'invalid '+key)
            require(all(text(x['reason']) for x in entries),'missing candidate retention reason')
    return v

def review(v,qid,p,evidence,revision,pending):
    require(v.get('question_id')==qid and type(v.get('revision')) is int and v['revision']==revision,'wrong review revision/question')
    require(option(v['best_candidate']),'invalid best_candidate')
    competitors=v['main_competitors']
    require(isinstance(competitors,list) and 1<=len(competitors)<=5 and all(option(x) and x!=v['best_candidate'] for x in competitors) and len(set(competitors))==len(competitors),f'main_competitors must contain 1..5 distinct original integer IDs other than best_candidate {v["best_candidate"]}; include strongest rejected alternative even if no close contender')
    require(v['decision_status'] in ('sufficient','needs_evidence'),'invalid decision_status')
    require(type(v['could_change_decision']) is bool and isinstance(v['unresolved_issue'],str),'invalid route fields')
    checks=v['criteria']; steps=p['plan']['steps']
    require(len(checks)==len(steps) and {x['step_id'] for x in checks}=={s['step_id'] for s in steps},'review must check every step')
    for x in checks:
        step=next(s for s in steps if s['step_id']==x['step_id'])
        require(x['status'] in ('pass','uncertain'),'failed technical step cannot be committed')
        require(len(x['checks'])==len(step['criteria']) and all(text(y) for y in x['checks']),f'review step {x["step_id"]}: expected {len(step["criteria"])} separate checks in criterion order, received {len(x["checks"])}; do not merge or omit checks')
        refs(x['evidence_refs'],evidence)
    decision=v['decision']; require(text(decision['support']) and isinstance(decision['uncertain'],list) and all(text(x) for x in decision['uncertain']),'missing decision support/unknowns')
    refs(decision['evidence_refs'],evidence)
    differences=decision['competitor_differences']
    require(len(differences)==len(competitors) and {x['option_id'] for x in differences}==set(competitors),'explain each competitor')
    for x in differences:
        require(text(x['difference']),'missing competitor difference'); refs(x['evidence_refs'],evidence)
    require(isinstance(v['observation_updates'],list),'missing observation_updates')
    for x in v['observation_updates']:
        require(x['target']=='states' or option(x['target']),'invalid update target')
        require(text(x['fact']),'empty fact update'); refs(x['evidence_refs'],evidence)
    refs(v['reviewed_evidence'],evidence)
    require(set(pending)<=set(v['reviewed_evidence']),'new evidence not reviewed')
    require(isinstance(v['evidence_requests'],list),'requests must be list')
    return v

def request_key(r):
    # Motivation text cannot bypass duplicate-evidence detection.
    return json.dumps({k:r[k] for k in ('action','option_id','source','time_seconds','time_range_seconds','frames','bbox') if k in r},sort_keys=True,separators=(',',':'))

def route(v,q,p,seen):
    if v['decision_status']=='sufficient': return [],'sufficient'
    if not v['could_change_decision']: return [],'no_decision_impact'
    if not text(v['unresolved_issue']): return [],'missing_specific_issue'
    requests=v['evidence_requests']
    if not 1<=len(requests)<=4: return [],'invalid_request_count'
    keys=set(); main={v['best_candidate'],*v['main_competitors']}
    try:
        for r in requests:
            require(r['action'] in p['draft']['allowed_evidence_actions'],'action not bound in plan')
            require(text(r['expected_observation']) and text(r['decision_impact']),'no testable impact')
            a=r['affected_candidates']; require(isinstance(a,list) and len(set(a))>=2 and all(option(x) for x in a) and main.intersection(a),'missing candidate ambiguity')
            if r['action']=='sample_video':
                require(option(r['option_id']) and r['option_id'] in a,'invalid candidate target')
                times=r['time_range_seconds']; duration=float(q['options'][r['option_id']-1]['duration_seconds'])
                require(len(times)==2 and all(type(x) in (int,float) and math.isfinite(x) for x in times) and 0<=times[0]<times[1]<duration,'invalid time interval; end must precede duration')
                require(type(r['frames']) is int and 3<=r['frames']<=17,'frames must be 3..17')
            else:
                require(r['source'] in ('init','final','video'),'crop must use original media')
                if r['source']=='video':
                    require(option(r['option_id']) and r['option_id'] in a,'invalid crop candidate')
                    t=r['time_seconds']; require(type(t) in (int,float) and math.isfinite(t) and 0<=t<float(q['options'][r['option_id']-1]['duration_seconds']),'invalid crop time')
                b=r['bbox']; require(len(b)==4 and all(type(x) in (int,float) and math.isfinite(x) for x in b) and 0<=b[0]<b[2]<=1 and 0<=b[1]<b[3]<=1,'invalid crop bbox')
            key=request_key(r); require(key not in seen and key not in keys,'duplicate evidence request'); keys.add(key)
    except (KeyError,TypeError,ValueError) as exc: return [],f'request_rejected: {exc}'
    return requests,'specific_unresolved_issue'


def bind_observation(value, planning):
    """Bind deterministic step IDs without spending another model call.

    The raw response is preserved by transport. Never alter facts, option IDs,
    citations, or answers. Ambiguous plans still require explicit valid IDs.
    """
    changes=[]
    for operation,key in [('observe_states','states'),('observe_candidates','candidates'),('compare','comparison')]:
        matches=[s['step_id'] for s in planning['plan']['steps'] if s['operation']==operation]
        if len(matches)!=1: continue
        items=value[key] if key=='candidates' else [value[key]]
        for item in items:
            if item.get('step_id')!=matches[0]:
                changes.append({'operation':operation,'option_id':item.get('option_id'),'model_step_id':item.get('step_id'),'bound_step_id':matches[0]})
                item['step_id']=matches[0]
    return changes


def bind_base_provenance(value):
    """Attach source-row provenance, not model-claimed semantic verification.
    All four actual images must already have native delivery receipts.
    """
    value['states']['evidence_refs']=['states.jpg']
    for candidate in value['candidates']:
        require(option(candidate['option_id']),'invalid original option_id')
        offset=((candidate['option_id']-1)//10)*10
        candidate['evidence_refs']=[f'options_{offset+1:02d}_{offset+10:02d}.jpg']
    value['comparison']['evidence_refs']=['states.jpg','options_01_10.jpg','options_11_20.jpg','options_21_30.jpg']


def review_candidates(observation,maximum=4):
    """Keep all undecided IDs in the record; only bound automatic board cost."""
    comp=observation['comparison']; ranked=[comp['best_candidate']]+[x['option_id'] for x in comp['main_competitors']]+[x['option_id'] for x in comp['not_ruled_out']]
    unique=list(dict.fromkeys(ranked))
    return unique[:maximum],unique[maximum:]


def bind_comparison_ranking(value,review=False):
    """Mechanical serialization of the model's order, without adding/reordering facts."""
    comp=value if review else value['comparison']
    key='ranked_candidates' if review else 'ranking'
    ranking=comp[key]; ids=[x['option_id'] for x in ranking]
    require(2<=len(ids)<=6 and all(option(i) for i in ids) and len(ids)==len(set(ids)),f'{key} requires 2..6 different original option IDs, best first; received {ids}. Give another strongest alternative, not the same ID twice.')
    comp['best_candidate']=ids[0]
    if review:
        comp['main_competitors']=ids[1:]
        comp['decision']['competitor_differences']=[{'option_id':x['option_id'],'difference':x['comparison'],'evidence_refs':x['evidence_refs']} for x in ranking[1:]]
    else:
        comp['reason']=ranking[0]['reason'];comp['main_competitors']=ranking[1:]


def bind_planning(value):
    """Serialize the existing three-stage plan; the model owns focuses/criteria/strategy."""
    operations=[('states','observe_states',[]),('candidates','observe_candidates',['states']),('compare','compare',['states','candidates'])]
    steps=[{'step_id':sid,'operation':op,'depends_on':parents,**value[sid]} for sid,op,parents in operations]
    bindings=[{'step_id':sid,'targets':['init','final'] if sid=='states' else list(range(1,31))} for sid,_,_ in operations]
    return {'question_id':value['question_id'],'plan':{'strategy':value['strategy'],'policy_ids':value['policy_ids'],'rationale':value['rationale'],'steps':steps},'draft':{'bindings':bindings,'allowed_evidence_actions':['sample_video','crop_evidence']}}
