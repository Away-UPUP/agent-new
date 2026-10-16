"""Run real held-out sources from fresh state, then evaluate without feeding labels to generation."""
import argparse,json,sys,time,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bridge_agent.pipeline import process
from bridge_agent.evaluate import evaluate
from bridge_agent.documents import save_json

SAMPLES=[
 ('short_history','赛题一_训练集/2017年/51-机场路立交桥.docx','赛题一_训练集标签/2017年/51-机场路立交桥-含有对比年度的信息提取报告.docx'),
 ('legacy_no_history','赛题一_训练集/2014年/59   人行天桥K31检测报告.doc','赛题一_训练集标签/2014年/59   人行天桥K31检测报告-无对比年度的信息提取报告.docx'),
 ('ordinary_tables','赛题一_训练集/2017年/60-新桥中桥.docx','赛题一_训练集标签/2017年/60-新桥中桥-含有对比年度的信息提取报告.docx'),
 ('long_468_pages','赛题一_训练集/2018年/2、马桑溪大桥.docx','赛题一_训练集标签/2018年/2、马桑溪大桥-含有对比年度的信息提取报告.docx'),
 ('long_300_pages','赛题一_训练集/2018年/4、高家花园.docx','赛题一_训练集标签/2018年/4、高家花园-含有对比年度的信息提取报告.docx'),
]

def main():
 os.environ.setdefault('INSPECTION_FRESH_RUN','1')
 p=argparse.ArgumentParser();p.add_argument('--samples',nargs='*',default=[s[0] for s in SAMPLES]);p.add_argument('--name',default='acceptance');p.add_argument('--concurrency',type=int,default=4);args=p.parse_args();root=Path(__file__).resolve().parents[1];results=[]
 for name,source,label in SAMPLES:
  if name not in args.samples:continue
  print('START',name,flush=True)
  try:
   metrics=process(root/source,root,root/'result'/args.name,root/'work'/args.name,args.concurrency)
   item={'sample':name,'status':'succeeded','metrics':metrics}
   if (root/label).exists():
    comparison=evaluate(metrics['output'],root/label);save_json(root/'artifacts'/f'{args.name}_{name}_evaluation.json',comparison);item['evaluation']={k:comparison[k] for k in ['basic_exact_count','basic_total','disease','recommendations','limits']}
   else:item['evaluation']={'error':'Expected label filename absent; generation did not use labels.'}
  except Exception as e:item={'sample':name,'status':'failed','error':str(e)}
  results.append(item);save_json(root/'artifacts'/f'{args.name}.json',results);print(json.dumps(item,ensure_ascii=False),flush=True)
 if any(r['status']!='succeeded' for r in results):sys.exit(1)
if __name__=='__main__':main()
