"""Exercise a delivery archive with no datasets, task state or model-response cache."""
import argparse,json,os,shutil,subprocess,sys,tarfile,tempfile,time,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bridge_agent.documents import save_json,digest
from docx import Document

def main():
 p=argparse.ArgumentParser();p.add_argument('--archive',default='agent-new-delivery.tar.gz');p.add_argument('--input',default='赛题一_训练集/2014年/59   人行天桥K31检测报告.doc');p.add_argument('--static-only',action='store_true');args=p.parse_args();root=Path(__file__).resolve().parents[1];archive=(root/args.archive).resolve();start=time.monotonic()
 with tempfile.TemporaryDirectory(prefix='agent-new-delivery-') as td:
  base=Path(td)
  with tarfile.open(archive) as tar:
   if any(n.startswith('/') or '..' in Path(n).parts or Path(n).name=='.env' for n in tar.getnames()):raise RuntimeError('Unsafe archive')
   tar.extractall(base,filter='data')
  manifest=json.loads((base/'design/package_manifest.json').read_text());verified=0
  for name,sha in manifest['files'].items():
   if digest(base/name)!=sha:raise RuntimeError('Content mismatch: '+name)
   verified+=1
  code=base/'code';env=dict(os.environ);env.pop('PYTHONPATH',None);env['INSPECTION_FRESH_RUN']='1'
  unit=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=code,env=env,capture_output=True,text=True)
  (root/'artifacts/clean_package_tests.log').write_text(unit.stdout+unit.stderr)
  if unit.returncode:raise RuntimeError('Extracted package unit tests failed')
  result={'archive_sha256':digest(archive),'code_hash':manifest['code_hash'],'contract_hash':manifest['contract_hash'],'verified_files':verified,'unit_tests':'passed','static_only':args.static_only}
  if not args.static_only:
   shutil.copyfile(root/'.env',code/'.env');(code/'.env').chmod(0o600)
   incoming=base/Path(args.input).name;shutil.copyfile(root/args.input,incoming)
   if (code/'work').exists():raise RuntimeError('Package unexpectedly contains runtime state')
   process=subprocess.run([sys.executable,'-m','bridge_agent','run',str(incoming),'--output','result','--work','work/clean','--concurrency','4'],cwd=code,env=env,capture_output=True,text=True)
   (root/'artifacts/clean_package_run.log').write_text(process.stdout+process.stderr)
   if process.returncode:
    audit=root/'artifacts'/('clean_failure_'+str(time.time_ns()));audit.mkdir()
    for task in (code/'work/clean').iterdir():
     if task.is_dir():shutil.copytree(task,audit/task.name)
    save_json(audit/'failure.json',{'archive_sha256':digest(archive),'code_hash':manifest['code_hash'],'contract_hash':manifest['contract_hash'],'stdout':process.stdout,'stderr':process.stderr})
    raise RuntimeError('Clean package real model run failed; diagnostics preserved in '+str(audit))
   m=json.loads(process.stdout.strip().splitlines()[-1]);dest=Path(m['output']);document=Document(dest)
   if [len(t.columns) for t in document.tables]!=[3,4,7] or len(document.tables[0].rows)!=17:raise RuntimeError('Output contract mismatch')
   if m['cache_hits'] or not m['model_requests']:raise RuntimeError('Expected fresh real model calls')
   output=root/'result/交付样例'/dest.name;output.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(dest,output)
   task=code/'work/clean'/m['job_id'];audit=root/'artifacts/clean_task';audit.mkdir(exist_ok=True)
   for name in ['manifest.json','findings.json','analysis.json','report.json','metrics.json','agent_trace.json','status.json']:
    shutil.copyfile(task/name,audit/name)
   parsed=list((code/'work/cache').glob('*/parsed.json'));result.update(metrics=m,output=str(output),output_sha256=digest(output),parsed_backend=json.loads(parsed[0].read_text())['backend'],empty_runtime_state_before_run=True,labels_present=False,training_directories_present=False)
  result['validation_elapsed_seconds']=round(time.monotonic()-start,3);name='clean_package_static.json' if args.static_only else 'clean_package.json';save_json(root/'artifacts'/name,result);print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
