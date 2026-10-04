"""Native structured output shapes. Semantic truth remains the VLM's responsibility."""

def obj(properties):return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
def arr(items,minimum=0,maximum=None):
    v={'type':'array','items':items,'minItems':minimum}
    if maximum is not None:v['maxItems']=maximum
    return v
STRING={'type':'string','maxLength':400}
TEXT={'type':'string','minLength':1,'maxLength':240}
def short_text(limit=100,empty=False):return {'type':'string','minLength':0 if empty else 1,'maxLength':limit}
OPTION={'type':'integer','minimum':1,'maximum':30}
NUMBER={'type':'number'}
BOOL={'type':'boolean'}


def review_schema(payload):
    ref=arr({'type':'string','enum':payload['evidence_catalog']},1)
    request_common={'affected_candidates':arr(OPTION,2,30),'expected_observation':TEXT,'decision_impact':TEXT}
    requests={'anyOf':[
        obj({'action':{'const':'sample_video'},'option_id':OPTION,'time_range_seconds':arr(NUMBER,2,2),'frames':{'type':'integer','minimum':3,'maximum':17},**request_common}),
        obj({'action':{'const':'crop_evidence'},'source':{'type':'string','enum':['init','final']},'bbox':arr(NUMBER,4,4),**request_common}),
        obj({'action':{'const':'crop_evidence'},'source':{'const':'video'},'option_id':OPTION,'time_seconds':NUMBER,'bbox':arr(NUMBER,4,4),**request_common})]}
    steps=payload['planning']['plan']['steps']
    criterion={'anyOf':[obj({'step_id':{'const':s['step_id']},'status':{'type':'string','enum':['pass','uncertain']},'checks':arr(TEXT,len(s['criteria']),len(s['criteria'])),'evidence_refs':ref}) for s in steps]}
    return obj({'question_id':{'const':payload['question_id']},'revision':{'const':payload['revision']},'ranked_candidates':arr(obj({'option_id':OPTION,'comparison':TEXT,'evidence_refs':ref}),2,6),'decision_status':{'type':'string','enum':['sufficient','needs_evidence']},'unresolved_issue':STRING,'could_change_decision':BOOL,'criteria':arr(criterion,len(steps),len(steps)),
        'decision':obj({'support':TEXT,'uncertain':arr(TEXT),'evidence_refs':ref}),
        'reviewed_evidence':ref,'observation_updates':arr(obj({'target':{'anyOf':[{'const':'states'},OPTION]},'fact':TEXT,'evidence_refs':ref})),
        'evidence_requests':arr(requests,0,4)})


def planning_schema(payload):
    ids=[x['id'] for x in payload.get('policies',[])]
    focus=obj({'focus':short_text(100),'criteria':arr(short_text(100),1,3)})
    return obj({'question_id':{'const':payload['question_id']},'strategy':{'type':'string','enum':['build','reuse','adapt','compose'] if ids else ['build']},'policy_ids':arr({'type':'string','enum':ids},0,len(ids)) if ids else {'const':[]},'rationale':short_text(180),'states':focus,'candidates':focus,'compare':focus})


def observation_schema(payload):
    # Step identifiers and base-page provenance are deterministic runtime bindings.
    return obj({'question_id':{'const':payload['question_id']},'states':obj({k:STRING for k in ['initial','final','change','invariants','uncertain']}),'candidates':arr(obj({'option_id':OPTION,'observed':short_text(100),'comparison':short_text(100),'uncertain':short_text(100,True)}),30,30),'comparison':obj({'ranking':arr(obj({'option_id':OPTION,'reason':short_text(100)}),2,6),'not_ruled_out':arr(obj({'option_id':OPTION,'reason':short_text(100)}),0,30)})})


def propose_batch_schema(payload):
    ids=[x['question_id'] for x in payload['available_questions']]
    return obj({'proposals':arr(obj({'question_id':{'type':'string','enum':ids},'capability_ids':arr({'type':'string','enum':[f'C{i:02d}' for i in range(1,11)]},1,3),'objective':TEXT,'rationale':TEXT}),payload['batch_size'],payload['batch_size'])})


def distill_batch_schema(payload):
    ids=[x['question_id'] for x in payload['cases']]
    return obj({'card':obj({'title':TEXT,'kind':{'type':'string','enum':['method','failure_lesson']},'capability_ids':arr({'type':'string','enum':[f'C{i:02d}' for i in range(1,11)]},1,3),'when':TEXT,'procedure':arr(TEXT,2,6),'checks':arr(TEXT,1,5),'pitfalls':arr(TEXT,1,5),'limitations':TEXT}), 'support_cases':arr({'type':'string','enum':ids},3,len(ids)), 'counterexamples':arr(obj({'question_id':{'type':'string','enum':ids},'boundary':TEXT}),1,4),'visual_checks':arr(obj({'evidence_id':{'type':'string','enum':payload['evidence_catalog']},'observation':TEXT,'implication':TEXT}),1,8)})


ROLE_SCHEMAS={'planning':planning_schema,'observation':observation_schema,'review':review_schema,'propose_batch':propose_batch_schema,'distill_batch':distill_batch_schema}
