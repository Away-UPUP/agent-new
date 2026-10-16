from __future__ import annotations
import hashlib,json,time,traceback,fcntl
from pathlib import Path
from .documents import parse,digest,save_json,read_json
from .model import Model
from .extraction import extract_all
from .history import match_history
from .agent import synthesize,validate_report
from .render import render
from .selection import selection


def code_hash():
    h=hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob('*.py')):h.update(p.name.encode());h.update(p.read_bytes())
    return h.hexdigest()


def contract_hash(root):
    h=hashlib.sha256()
    for p in sorted((Path(root)/'assets').glob('*')):h.update(p.name.encode());h.update(p.read_bytes())
    return h.hexdigest()


def process(source,root='.',output='result',work='work/jobs',concurrency=4,budget=22000):
    root=Path(root).resolve();source=Path(source).resolve();output=Path(output).resolve();work=Path(work).resolve()
    source_hash=digest(source);configuration=Model(root,work/'connection',concurrency)
    manifest={'source_hash':source_hash,'filename':source.name,'code_hash':code_hash(),'contract_hash':contract_hash(root),'model':configuration.name,'api_base':configuration.base,'budget':budget}
    job_id=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()[:24]
    directory=work/job_id;directory.mkdir(parents=True,exist_ok=True)
    lock=(directory/'job.lock').open('w')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise RuntimeError('This exact job is already running: '+job_id)
    save_json(directory/'manifest.json',manifest)
    model=Model(root,directory,concurrency);start=time.monotonic();timings={};state={'job_id':job_id,'source':source.name,'status':'running','stage':'parse','output':None}
    def stage(name):state.update(stage=name,elapsed_seconds=round(time.monotonic()-start,3));save_json(directory/'status.json',state)
    def timed(name,callback):
        stage(name);t=time.monotonic();result=callback();timings[name]=round(time.monotonic()-t,3);return result
    try:
        doc=timed('parse',lambda:parse(source,root/'work/cache'))
        if doc['chars']<300 and doc['images']>0:raise ValueError('Image-only report requires OCR; no complete report exported')
        # All files are reusable only inside this content/model/code/contract-specific job.
        findings_path=directory/'findings.json'
        if findings_path.exists():findings=read_json(findings_path)
        else:
            findings=timed('extract',lambda:extract_all(model,doc,directory/'batches',concurrency,budget));save_json(findings_path,findings)
        choice_path=directory/'selection.json'
        choice=read_json(choice_path) if choice_path.exists() else timed('select',lambda:selection(model,doc,findings,choice_path))
        selected_path=directory/'selected_findings.json'
        if selected_path.exists():findings=read_json(selected_path)
        else:
            selected=set(choice['ids']);findings={**findings,'diseases':[d for d in findings['diseases'] if d['id'] in selected]}
        if 'history_status' not in findings:
            findings['history_status']=timed('history',lambda:match_history(model,doc,findings,directory/'history',concurrency));save_json(selected_path,findings)
        report_path=directory/'analysis.json'
        if report_path.exists():report=read_json(report_path)
        else:report=timed('write',lambda:synthesize(model,doc,findings,root,directory));save_json(report_path,report)
        suffix='含有对比年度' if report['has_history'] else '无对比年度'
        dest=output/(source.stem+'-'+suffix+'的信息提取报告.docx')
        rendered=timed('render_validate',lambda:render(dest,report,findings,root));save_json(directory/'report.json',rendered)
        elapsed=round(time.monotonic()-start,3)
        metrics={'job_id':job_id,'source':source.name,'source_hash':source_hash,'model':model.name,'code_hash':manifest['code_hash'],'contract_hash':manifest['contract_hash'],'status':'succeeded','elapsed_seconds':elapsed,'stages_seconds':timings,'pages_metadata':doc['pages_metadata'],'source_chars':doc['chars'],'source_blocks':len(doc['blocks']),'image_count':doc['images'],'batches':findings['coverage']['batches'],'diseases':len(rendered['diseases']),'recommendations':len(rendered['recommendations']),'model_requests':sum(not r.get('cache') and 'finish_reason' in r for r in model.records),'cache_hits':sum(r.get('cache',False) for r in model.records),'request_errors':sum('error' in r for r in model.records),'input_tokens':sum(r.get('usage',{}).get('prompt_tokens',0) for r in model.records),'output_tokens':sum(r.get('usage',{}).get('completion_tokens',0) for r in model.records),'output':str(dest),'output_sha256':digest(dest),'coverage':findings['coverage'],'semantic_quality':'Source links and Word structure validated. Compare held-out labels separately; not an official score.','ocr_status':'not_used; embedded-image text is not read'}
        metrics['candidate_diseases']=choice['total_candidates'];metrics['selection_reason']=choice['reason']
        all_calls=[json.loads(line) for line in (directory/'model_metrics.jsonl').read_text().splitlines()] if (directory/'model_metrics.jsonl').exists() else []
        metrics['lifetime_model_requests']=sum(not r.get('cache') and 'finish_reason' in r for r in all_calls)
        metrics['lifetime_input_tokens']=sum(r.get('usage',{}).get('prompt_tokens',0) for r in all_calls)
        metrics['lifetime_output_tokens']=sum(r.get('usage',{}).get('completion_tokens',0) for r in all_calls)
        save_json(directory/f'metrics_{time.time_ns()}.json',metrics);save_json(directory/'metrics.json',metrics);state.update(status='succeeded',stage='complete',elapsed_seconds=elapsed,output=str(dest));save_json(directory/'status.json',state)
        return metrics
    except Exception as e:
        state.update(status='failed',error=str(e),elapsed_seconds=round(time.monotonic()-start,3));save_json(directory/'status.json',state)
        (directory/'error.log').write_text(traceback.format_exc(),encoding='utf-8');raise
    finally:fcntl.flock(lock,fcntl.LOCK_UN);lock.close();model.client.close();configuration.client.close()
