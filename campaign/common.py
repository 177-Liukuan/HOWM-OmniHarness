import fcntl,hashlib,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from contracts import require
from state import atomic,Budget
from codex_team import CodexTeam
from run import METHOD

def read(p):return json.loads(Path(p).read_text())
def hashfile(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def append_event(path,event,key='question_id'):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+') as f:
        fcntl.flock(f,fcntl.LOCK_EX);f.seek(0);old=[json.loads(l) for l in f if l.strip()]
        if any(x[key]==event[key] for x in old):
            require(next(x for x in old if x[key]==event[key])==event,'conflicting durable event');return
        f.write(json.dumps(event,ensure_ascii=False,separators=(',',':'))+'\n');f.flush();os.fsync(f.fileno())

def model_call(root,role,payload,images,config,validate):
    root.mkdir(parents=True,exist_ok=True)
    frozen={'role':role,'payload':payload,'images':[{ 'path':str(p),'sha256':hashfile(p)} for p in images]}
    if (root/'input.json').exists():require(read(root/'input.json')==frozen,'offline call inputs changed')
    else:atomic(root/'input.json',frozen)
    if (root/'result.json').exists():return read(root/'result.json')
    b=Budget(root/'state.json',config);require(b.s['status']!='failed','interrupted offline call needs inspection; budget retained')
    team=CodexTeam(root,METHOD,b.s)
    while b.s['turns']<2:
        timeout=b.reserve('turns')
        try:
            value=team.call(role,payload,images,b.s['turns'],timeout);validate(value)
            b.release();b.s['status']='completed';b.save();atomic(root/'result.json',value);return value
        except (ValueError,KeyError,TypeError) as exc:
            b.release();b.s['last_error']=str(exc);b.save()
            if not b.s.get('session_id') or b.s['turns']>=2:raise
            payload={**payload,'protocol_error':str(exc)}
    raise RuntimeError('offline protocol budget exhausted')
