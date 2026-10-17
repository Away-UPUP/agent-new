import argparse,json,sys
from pathlib import Path
from .pipeline import process
from .documents import save_json

def main():
    parser=argparse.ArgumentParser(description='Word桥梁检测报告分析智能体')
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ['run','batch']:
        p=sub.add_parser(name);p.add_argument('input');p.add_argument('--root',default=str(Path(__file__).resolve().parent.parent));p.add_argument('--output',default='result');p.add_argument('--work',default='work/jobs');p.add_argument('--concurrency',type=int,choices=[1,2,4],default=4);p.add_argument('--chunk-chars',type=int,default=22000)
    p=sub.add_parser('serve');p.add_argument('--root',default=str(Path(__file__).resolve().parent.parent));p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8510);p.add_argument('--concurrency',type=int,choices=[1,2,4],default=4)
    args=parser.parse_args()
    if args.command=='serve':
        from .web import serve
        serve(args.root,args.host,args.port,args.concurrency);return
    source=Path(args.input)
    if args.command=='run':files=[source]
    else:files=sorted(p for p in source.rglob('*') if p.suffix.lower() in ['.doc','.docx'] and p.is_file() and not p.name.startswith('~$'))
    if not files:parser.error('No Word files found')
    results=[]
    for p in files:
        try:
            result=process(p,args.root,args.output,args.work,args.concurrency,args.chunk_chars);results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
        except Exception as e:
            result={'source':p.name,'status':'failed','error':str(e)};results.append(result);print(json.dumps(result,ensure_ascii=False),file=sys.stderr,flush=True)
        save_json(Path(args.output)/'batch_manifest.json',results)
    if any(r['status']!='succeeded' for r in results):sys.exit(1)

if __name__=='__main__':main()
