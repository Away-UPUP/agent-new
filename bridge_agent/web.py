"""Local upload/status/download UI, using the Python standard library."""
import concurrent.futures,json,threading,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from .documents import save_json,read_json
from .pipeline import process

HTML='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>检测报告分析智能体</title><style>body{font:16px system-ui;max-width:960px;margin:50px auto;padding:0 24px;background:#f5f7fa;color:#14243b}h1{font-size:28px}button{background:#225acc;color:white;border:0;padding:12px 22px;border-radius:6px;cursor:pointer}input{padding:15px 0}article{background:white;padding:24px;margin:18px 0;border-radius:10px}p{line-height:1.7}a{color:#225acc}progress{width:100%}.error{color:#b42318}li{margin:16px 0}small{color:#526075}</style><h1>桥梁检测报告分析</h1><p>上传原始 Word 检测报告，智能体按训练标签规范生成完整分析报告。支持 DOC / DOCX、历史对比和长文档。处理期间可关闭此页面，重新打开查看任务。</p><article><label for="files">选择检测报告</label><br><input id="files" type="file" accept=".doc,.docx" multiple><br><button id="start">上传并开始分析</button><p id="message" aria-live="polite"></p></article><article><h2>处理任务</h2><ul id="jobs" aria-live="polite"></ul></article><script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stages={queued:'等待处理',parse:'解析文档',extract:'逐批分析',history:'核对历史',write:'撰写报告',render_validate:'生成并校验Word',complete:'已完成'};
async function refresh(){try{let a=await(await fetch('/api/jobs')).json();document.querySelector('#jobs').innerHTML=a.map(j=>`<li><b>${esc(j.filename)}</b>：${esc(stages[j.stage]||j.status)} ${j.status==='succeeded'?`<a href="/api/jobs/${j.id}/download">下载分析报告</a>`:j.status==='failed'?`<span class="error">${esc(j.error)}</span>`:'<progress aria-label="处理中"></progress>'}<br><small>${esc(j.id)}</small></li>`).join('')}catch(e){document.querySelector('#message').textContent='连接中断，请稍后刷新。'}}
document.querySelector('#start').onclick=async()=>{let button=document.querySelector('#start');button.disabled=true;try{for(let f of document.querySelector('#files').files){let r=await fetch('/api/jobs?filename='+encodeURIComponent(f.name),{method:'POST',body:f});let d=await r.json();if(!r.ok)throw Error(d.error)}document.querySelector('#message').textContent='已上传，任务将依次处理。';refresh()}catch(e){document.querySelector('#message').textContent=e.message}finally{button.disabled=false}};refresh();setInterval(refresh,3000);
</script></html>'''

def serve(root,host,port,concurrency):
    root=Path(root).resolve();base=root/'work/uploads';base.mkdir(parents=True,exist_ok=True);pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
    # Reports are serialized; independent extraction calls within a report use bounded concurrency.
    def status(p):
        data=read_json(p/'upload.json')
        manifests=list((p/'tasks').glob('*/status.json'))
        if manifests:data.update(read_json(max(manifests,key=lambda q:q.stat().st_mtime)))
        return data
    def run(directory):
        data=read_json(directory/'upload.json')
        try:
            m=process(directory/data['filename'],root,root/'result/uploads',directory/'tasks',concurrency)
            data.update(status='succeeded',stage='complete',output=m['output'])
        except Exception as e:data.update(status='failed',error=str(e))
        save_json(directory/'upload.json',data)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,format,*args):pass
        def send(self,code,data,content_type='application/json; charset=utf-8'):
            body=json.dumps(data,ensure_ascii=False).encode() if isinstance(data,(dict,list)) else data
            self.send_response(code);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_GET(self):
            path=urlparse(self.path).path
            if path=='/':return self.send(200,HTML.encode(),'text/html; charset=utf-8')
            if path=='/api/health':return self.send(200,{'status':'ok','service':'agent-new','upload_limit_mb':100})
            if path=='/api/jobs':return self.send(200,[status(p) for p in sorted(base.iterdir(),key=lambda q:q.stat().st_mtime,reverse=True) if (p/'upload.json').exists()])
            parts=path.strip('/').split('/')
            if len(parts)==4 and parts[:2]==['api','jobs'] and parts[3]=='download' and len(parts[2])==32 and all(c in '0123456789abcdef' for c in parts[2]):
                p=base/parts[2]
                if (p/'upload.json').exists():
                    s=status(p)
                    if s.get('status')=='succeeded':return self.send(200,Path(s['output']).read_bytes(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
            self.send(404,{'error':'Not found or report not complete'})
        def do_POST(self):
            parsed=urlparse(self.path)
            if parsed.path!='/api/jobs':return self.send(404,{'error':'Not found'})
            try:
                name=Path(parse_qs(parsed.query).get('filename',[''])[0]).name;length=int(self.headers.get('Content-Length','0'))
                if not name or name.startswith('.') or Path(name).suffix.lower() not in ['.doc','.docx']:raise ValueError('请选择DOC/DOCX文件')
                if not 0<length<=100*1024*1024:raise ValueError('文件为空或超过100MB')
                job_id=uuid.uuid4().hex;directory=base/job_id;directory.mkdir()
                data=self.rfile.read(length)
                if len(data)!=length:raise ValueError('Incomplete upload')
                (directory/name).write_bytes(data)
                from .documents import sniff
                sniff(directory/name)
                saved={'id':job_id,'filename':name,'status':'queued','stage':'queued'};save_json(directory/'upload.json',saved);pool.submit(run,directory);self.send(202,saved)
            except (ValueError,OSError) as e:self.send(400,{'error':str(e)})
    # Recover interrupted tasks automatically from compatible saved stage outputs.
    for p in base.iterdir():
        if (p/'upload.json').exists() and status(p).get('status') not in ['succeeded','failed']:pool.submit(run,p)
    print(f'Upload UI: http://{host}:{port}',flush=True)
    try:ThreadingHTTPServer((host,port),Handler).serve_forever()
    finally:pool.shutdown(wait=True)
