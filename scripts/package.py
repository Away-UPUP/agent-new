"""Build a portable, secret-free code/design/result archive with a deterministic manifest."""
import argparse,gzip,hashlib,io,json,sys,tarfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bridge_agent.documents import digest,save_json
from bridge_agent.pipeline import code_hash,contract_hash

def build(root):
 root=Path(root);entries=[]
 for folder in ['bridge_agent','assets','tests','scripts','tools/antiword']:
  entries.extend((p,'code/'+str(p.relative_to(root))) for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
 for name in ['requirements.txt','.env.example','README.md','THIRD_PARTY.md','run_batch.sh','stop_batch.sh']:
  entries.append((root/name,'code/'+name))
 for name in ['DESIGN.md','开发文档.md','VALIDATION.md','PERFORMANCE.md']:
  entries.append((root/name,'design/'+name))
 for pattern in ['audit_summary.json','label_patterns.json','training_pairs.json','test_inventory.json','acceptance_final*.json','performance*.json','upload_test.json','final_upload_test.json','clean_package.json','clean_package_evaluation.json','clean_package_failed*.json','unit_tests.log','release_v*.json','delivery_*.json','final_delivery*.json','final_performance*.json','clean_package_tests.log','server_restart_check.json','environment.json','structural_and_ledger_checks.json','sample_provenance.json','script_selfchecks.log','clean_package_run_failed_final_attempt1.log']:
  entries.extend((p,'design/validation/'+p.name) for p in (root/'artifacts').glob(pattern))
 entries.extend((p,'result/'+p.name) for p in (root/'result/交付样例').glob('*.docx'))
 if not entries or not list((root/'result/交付样例').glob('*.docx')):raise RuntimeError('No verified example reports; refusing empty result package')
 manifest={'code_hash':code_hash(),'contract_hash':contract_hash(root),'files':{target:digest(p) for p,target in sorted(entries,key=lambda e:e[1])},'excluded':['API credentials','raw training and test inputs','labels other than authorized short few-shot examples','runtime caches']}
 payload=json.dumps(manifest,ensure_ascii=False,indent=2).encode();archive=root/'agent-new-delivery.tar.gz'
 with archive.open('wb') as f:
  with gzip.GzipFile(fileobj=f,filename='',mode='wb',mtime=0) as gz:
   with tarfile.open(fileobj=gz,mode='w') as tar:
    for p,target in sorted(entries,key=lambda e:e[1]):
     info=tar.gettarinfo(str(p),target);info.mtime=0;info.uid=info.gid=0;info.uname=info.gname=''
     with p.open('rb') as src:tar.addfile(info,src)
    info=tarfile.TarInfo('design/package_manifest.json');info.size=len(payload);tar.addfile(info,io.BytesIO(payload))
 with tarfile.open(archive) as tar:
  names=tar.getnames()
  if any(Path(n).name=='.env' or n.startswith('/') or '..' in Path(n).parts for n in names):raise RuntimeError('Unsafe or secret archive member')
  for target,sha in manifest['files'].items():
   if hashlib.sha256(tar.extractfile(target).read()).hexdigest()!=sha:raise RuntimeError('Archive content verification failed: '+target)
 result={'archive':str(archive.resolve()),'sha256':digest(archive),'bytes':archive.stat().st_size,'files':len(names),'code_hash':manifest['code_hash'],'contract_hash':manifest['contract_hash']};save_json(root/'artifacts/package.json',result);print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':build(Path(__file__).resolve().parents[1])
