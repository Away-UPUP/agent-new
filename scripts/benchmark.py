"""Same input and release, fresh model-result directories for concurrency 1/2/4."""
import argparse,json,sys,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bridge_agent.pipeline import process
from bridge_agent.documents import save_json
from bridge_agent.evaluate import evaluate

def main():
 os.environ['INSPECTION_FRESH_RUN']='1'
 p=argparse.ArgumentParser();p.add_argument('--name',default='performance');args=p.parse_args();root=Path(__file__).resolve().parents[1];source=root/'赛题一_训练集/2017年/51-机场路立交桥.docx';label=root/'赛题一_训练集标签/2017年/51-机场路立交桥-含有对比年度的信息提取报告.docx';results=[]
 for concurrency in [1,2,4]:
  name=args.name+'_c'+str(concurrency);work=root/'work'/name
  if list(work.glob('*/findings.json')):raise RuntimeError('Benchmark needs a new --name; existing model results found')
  print('START concurrency',concurrency,flush=True)
  try:
   metrics=process(source,root,root/'result'/name,work,concurrency)
   e=evaluate(metrics['output'],label);save_json(root/'artifacts'/(name+'_evaluation.json'),e)
   result={'concurrency':concurrency,'status':'succeeded','fresh_model_state':metrics['cache_hits']==0,'metrics':metrics,'evaluation':{k:e[k] for k in ['basic_exact_count','disease','recommendations']}}
  except Exception as e:result={'concurrency':concurrency,'status':'failed','error':str(e)}
  results.append(result);save_json(root/'artifacts'/(args.name+'.json'),results);print(json.dumps(result,ensure_ascii=False),flush=True)
 if any(x['status']!='succeeded' for x in results):sys.exit(1)
if __name__=='__main__':main()
