"""Conservative checkpoint: interrupted reservations are charged, never refunded."""
import json,os,time
from pathlib import Path
from contracts import require


def atomic(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w') as f:
        json.dump(value,f,ensure_ascii=False,indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    tmp.replace(path)


class Budget:
    def __init__(self,path,config):
        self.path=path; self.config=config; self.start=time.monotonic()
        self.s=json.loads(path.read_text()) if path.exists() else {'turns':0,'requests':0,'batches':0,'attempts':1,'elapsed':0,'extensions':0,'finish_only':False,'grace_used':False,'entered_slow_path':False,'revision':0,'phase':'base','seen_requests':[],'pending_evidence':[],'evidence':{},'status':'running','protocol_retries':0,'limits':dict(config['soft'])}
        if self.s.get('reservation'):
            self.s['elapsed']+=self.s['reservation']['seconds']; self.s['interrupted_reservations']=self.s.get('interrupted_reservations',0)+1
            # Never blindly repeat a pending acquisition/model turn on recovery.
            self.s['status']='failed'; self.s['stop_reason']='interrupted_operation'; self.s['error']='interrupted operation: inspect checkpoint; automatic complex recovery deferred'
            self.s.pop('reservation')
        self.offset=self.s['elapsed']; self.save()

    def elapsed(self): return self.offset+time.monotonic()-self.start
    def save(self):
        self.s['elapsed']=self.elapsed() if hasattr(self,'offset') else self.s['elapsed']
        atomic(self.path,self.s)
    def remaining(self,review=False):
        elapsed=self.elapsed()
        if elapsed>=self.s['limits']['seconds']:
            self.s['finish_only']=True
            if review and not self.s['grace_used']:
                self.s['grace_used']=True
                self.s['finish_deadline']=self.s['limits']['seconds']+self.config['finish_grace']
        deadline=self.s.get('finish_deadline',self.s['limits']['seconds'])
        remaining=min(deadline,self.config['hard']['seconds'])-elapsed
        require(remaining>0,'budget time exhausted')
        return remaining
    def reserve(self,kind,review=False):
        if review and not self.s['finish_only']:
            # A necessary Review may straddle the soft deadline, once. It may
            # finish within grace but cannot authorize fresh research afterwards.
            timeout=min(self.config['call_timeout'],min(self.config['hard']['seconds'],self.s['limits']['seconds']+self.config['finish_grace'])-self.elapsed())
            require(timeout>0,'budget time exhausted')
        else:
            timeout=min(self.config['call_timeout'],self.remaining(review))
        if kind in ('turns','requests'):
            require(self.s[kind]<min(self.s['limits'][kind],self.config['hard'][kind]),'budget '+kind+' exhausted')
            self.s[kind]+=1
        self.s['reservation']={'kind':kind,'seconds':timeout,'review':review}; self.save(); return timeout
    def release(self):
        reservation=self.s.pop('reservation',{})
        if reservation.get('review') and self.elapsed()>=self.s['limits']['seconds']:
            self.s['finish_only']=True; self.s['grace_used']=True
            self.s['finish_deadline']=min(self.config['hard']['seconds'],self.s['limits']['seconds']+self.config['finish_grace'])
        self.save()
    def admit(self,requests,review):
        if self.s['finish_only']: return 'finish_only'
        def lacking():
            for key,needed in [('turns',2),('batches',1),('requests',len(requests))]:
                if self.s[key]+needed>self.s['limits'][key]: return 'soft_'+key
            if self.elapsed()>=self.s['limits']['seconds']: return 'soft_time'
        reason=lacking()
        progress=self.s.get('last_batch_progress',False)
        if reason and progress and self.s['extensions']<self.config['extensions'] and not self.s['grace_used']:
            # Extensions are not granted on changes of answer/rewording alone.
            grants={'turns':2,'attempts':1,'batches':1,'requests':4,'seconds':120}
            fits=all(self.s[k]+n<=self.config['hard'][k] for k,n in [('turns',2),('batches',1),('requests',len(requests))]) and self.elapsed()<self.config['hard']['seconds']
            if fits:
                for k,n in grants.items(): self.s['limits'][k]=min(self.config['hard'][k],self.s['limits'][k]+n)
                self.s['extensions']+=1; reason=lacking()
        if reason:
            self.s['finish_only']=True; self.save(); return reason
        self.s['entered_slow_path']=True; self.s['batches']+=1; self.save(); return None
