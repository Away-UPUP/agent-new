from __future__ import annotations
import hashlib, json, os, threading, time
from pathlib import Path
import httpx
from dotenv import dotenv_values
from .documents import save_json, read_json

class Model:
    def __init__(self, root, work, concurrency=4):
        c={**dotenv_values(Path(root)/'.env'),**os.environ}
        self.base=c.get('INSPECTION_API_BASE_URL','').rstrip('/')
        self.key=c.get('INSPECTION_API_KEY','');self.name=c.get('INSPECTION_MODEL','')
        if not all([self.base,self.key,self.name]):raise ValueError('Missing INSPECTION_API_BASE_URL / INSPECTION_API_KEY / INSPECTION_MODEL')
        self.work=Path(work); self.cache=self.work/'calls';self.cache.mkdir(parents=True,exist_ok=True)
        self.lock=threading.Lock();self.sem=threading.Semaphore(concurrency);self.records=[]
        self.client=httpx.Client(timeout=httpx.Timeout(240,connect=30),limits=httpx.Limits(max_connections=concurrency,max_keepalive_connections=concurrency))

    def call(self, messages, tools, choice='auto', max_tokens=6000):
        payload={'model':self.name,'messages':messages,'tools':tools,'tool_choice':choice,'max_tokens':max_tokens}
        key=hashlib.sha256(json.dumps({'base':self.base,'payload':payload},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        dest=self.cache/(key+'.json')
        if dest.exists():
            d=read_json(dest);self.record({'cache':True,'key':key,'seconds':0,'usage':{}});return d['message']
        # The exact request and response are local artifacts, never credentials.
        for attempt in range(3):
            start=time.monotonic()
            try:
                with self.sem:
                    r=self.client.post(self.base+'/chat/completions',headers={'Authorization':'Bearer '+self.key},json=payload)
                if r.status_code!=200:
                    raise RuntimeError('Model HTTP '+str(r.status_code)+': '+r.text[:400].replace(self.key,'<redacted>'))
                d=r.json();choice_data=d['choices'][0];msg=choice_data['message'];finish=choice_data.get('finish_reason')
                self.record({'cache':False,'key':key,'attempt':attempt+1,'seconds':round(time.monotonic()-start,3),'usage':d.get('usage',{}),'finish_reason':finish,'request_chars':len(json.dumps(messages,ensure_ascii=False))})
                if finish in ('length','content_filter'):raise ValueError('Model output incomplete: '+str(finish))
                if not msg.get('tool_calls') and not msg.get('content'):raise ValueError('Empty model message')
                save_json(dest,{'request':payload,'message':msg,'usage':d.get('usage',{}),'finish_reason':finish})
                return msg
            except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as e:
                if isinstance(e, ValueError) and 'incomplete' in str(e):raise
                self.record({'cache':False,'key':key,'attempt':attempt+1,'seconds':round(time.monotonic()-start,3),'error':str(e).replace(self.key,'<redacted>')})
                if attempt==2:raise
                time.sleep(1+attempt*2)
        raise RuntimeError('Model retries exhausted')

    def record(self,item):
        with self.lock:
            self.records.append(item)
            with (self.work/'model_metrics.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(item,ensure_ascii=False)+'\n')

def function(name, description, parameters):
    return {'type':'function','function':{'name':name,'description':description,'parameters':parameters}}

def obj(properties,required=None):
    return {'type':'object','properties':properties,'required':list(properties) if required is None else required,'additionalProperties':False}

STR={'type':'string'}
STRS={'type':'array','items':STR}
