"""Native Codex CLI transport. One persisted thread per question, no model tools."""
import base64, hashlib, io, json, os, signal, subprocess
from pathlib import Path
from PIL import Image
from jsonschema import Draft202012Validator,ValidationError
from contracts import require
from schemas import ROLE_SCHEMAS
from vendor.submission_io import unique_object


def run_process(command,prompt,cwd,env,stdout,stderr,timeout):
    with stdout.open('w') as out,stderr.open('w') as err:
        p=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=out,stderr=err,text=True,cwd=cwd,env=env,start_new_session=True)
        try:
            p.communicate(prompt,timeout=timeout)
        except BaseException:
            try: os.killpg(p.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            try: p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid,signal.SIGKILL); p.wait()
            raise
    require(p.returncode==0,f'process failed {p.returncode}: {stderr.read_text()[-1500:]}')


class CodexTeam:
    def __init__(self,root,method,state):
        self.root=root; self.method=method; self.state=state
        self.home=root/'codex-home'; self.task=root/'task'; self.logs=root/'turns'
        for d in (self.home,self.task,self.logs): d.mkdir(parents=True,exist_ok=True)
        config=(method/'config/codex.toml').read_text()
        config_path=self.home/'config.toml'
        if not config_path.exists(): config_path.write_text(config)

    def call(self,role,payload,images,number,timeout):
        marker=f'HOWM_CALL_{number:03d}_{role}'
        prompt=marker+'\n'+(self.method/f'prompts/{role}.txt').read_text()+'\nPAYLOAD:\n'+json.dumps(payload,ensure_ascii=False)
        stem=self.logs/f'{number:03d}-{role}'
        command=['codex','exec','--strict-config','--skip-git-repo-check','--json','--output-last-message',str(stem.with_suffix('.final.txt'))]
        sid=self.state.get('session_id')
        if sid: command+=['resume',sid]
        else: command+=['--cd',str(self.task)]
        if role in ROLE_SCHEMAS:
            schema_path=stem.with_suffix('.schema.json')
            schema_path.write_text(json.dumps(ROLE_SCHEMAS[role](payload),ensure_ascii=False))
            command+=['--output-schema',str(schema_path)]
        for p in images: command+=['--image',str(p)]
        command+=['--','-']
        stem.with_suffix('.prompt.txt').write_text(prompt)
        stem.with_suffix('.command.json').write_text(json.dumps(command))
        env={k:v for k,v in os.environ.items() if k not in ('OPENAI_API_KEY','OPENAI_BASE_URL','OPENAI_ORG_ID','OPENAI_PROJECT_ID')}
        env.update(CODEX_HOME=str(self.home),PYTHONHASHSEED='20260929')
        run_process(command,prompt,self.task,env,stem.with_suffix('.events.jsonl'),stem.with_suffix('.stderr.log'),timeout)
        events=[json.loads(l) for l in stem.with_suffix('.events.jsonl').read_text().splitlines() if l.strip()]
        threads=[e['thread_id'] for e in events if e.get('type')=='thread.started']
        require(len(threads)==1 and (sid is None or sid==threads[0]),'native thread changed')
        self.state['session_id']=threads[0]
        require(any(e.get('type')=='turn.completed' for e in events),'native turn incomplete')
        for e in events:
            if e.get('type')=='item.completed':
                require(e.get('item',{}).get('type') in ('agent_message','reasoning'),'unexpected native tool execution')
        receipt=self.audit(marker,images)
        receipt['usage']=[e.get('usage') for e in events if e.get('type')=='turn.completed']
        stem.with_suffix('.audit.json').write_text(json.dumps(receipt,indent=2))
        raw=stem.with_suffix('.final.txt').read_text().strip()
        if raw.startswith('```'):
            raw=raw.partition('\n')[2].rsplit('```',1)[0].strip()
        value=json.loads(raw,object_pairs_hook=unique_object)
        require(isinstance(value,dict),'expected JSON object')
        if role in ROLE_SCHEMAS:
            try:Draft202012Validator(ROLE_SCHEMAS[role](payload)).validate(value)
            except ValidationError as exc:raise ValueError('structured output constraint: '+exc.message[:700]) from exc
        return value

    def audit(self,marker,images):
        matches=[]; tool_calls=0; compactions=0
        for f in (self.home/'sessions').rglob('*.jsonl'):
            for line in f.open():
                e=json.loads(line); p=e.get('payload',{})
                if e.get('type')=='compacted': compactions+=1
                if p.get('type') in ('function_call','custom_tool_call'): tool_calls+=1
                if e.get('type')=='response_item' and p.get('role')=='user' and any(marker in c.get('text','') for c in p.get('content',[])):
                    matches.append(p['content'])
        require(tool_calls==0,'tools disabled but native tool call recorded')
        require(compactions==0,'automatic compaction encountered; explicit recovery not implemented')
        require(len(matches)==1,'missing/ambiguous native input receipt')
        blocks=[b for b in matches[0] if b.get('type')=='input_image']
        require(len(blocks)==len(images),'image count mismatch')
        receipts=[]
        for block,path in zip(blocks,images):
            url=block['image_url']; require(url.startswith('data:image/') and ';base64,' in url,'no native encoded image')
            data=base64.b64decode(url.partition(';base64,')[2],validate=True)
            with Image.open(io.BytesIO(data)) as im: size=im.size; im.verify()
            with Image.open(path) as original: w,h=original.size
            scale=min(1,2048/max(w,h)); expected=(round(w*scale),round(h*scale))
            require(size==expected,'native image dimensions mismatch')
            # Native CLI may re-encode/resize: archive byte hashes and wrapper path,
            # not a claim that dimensions prove semantic content or model attention.
            require(any(str(path) in c.get('text','') for c in matches[0]),'missing native image path wrapper')
            receipts.append({'path':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'native_sha256':hashlib.sha256(data).hexdigest(),'native_dimensions':size})
        return {'session_id':self.state['session_id'],'images':receipts,'tool_calls':tool_calls,'compactions':compactions}
