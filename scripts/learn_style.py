"""Learn writing granularity, never input-specific answers; exclude the development matrix."""
import json,re,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bridge_agent.audit import label_data
from bridge_agent.documents import parse,save_json
from validate import SAMPLES
root=Path(__file__).resolve().parents[1];excluded={str((root/label).resolve()) for _,_,label in SAMPLES};profiles=[]
for p in sorted((root/'赛题一_训练集标签').rglob('*')):
 if not p.is_file() or p.name.startswith('~$') or str(p.resolve()) in excluded:continue
 data=label_data(parse(p,root/'work/cache'));rows=[r for r in data['diseases'] if len(r)>=7];name=data['summary'].get('桥梁名称','')
 if not rows or not name:continue
 counts=sum(bool(re.search(r'总面积|总长|共计|（如|等）',r[3])) for r in rows);year=re.search(r'20\d{2}',p.parent.name)
 mode='exhaustive' if len(rows)>200 else 'aggregate' if counts/len(rows)>.15 else 'representative'
 profiles.append({'bridge':name,'year':int(year[0]) if year else 0,'style':mode,'label_rows':len(rows),'aggregate_fraction':round(counts/len(rows),3),'label':str(p.relative_to(root))})
save_json(root/'assets/style_profiles.json',{'method':'Nearest bridge identity and closest earlier report year; a writing-style prior only. More than 200 label rows => exhaustive; aggregate phrasing in >15% of rows => aggregate; otherwise representative. Development matrix labels excluded. No report facts, scores or disease answers stored.','excluded_labels':[label for _,_,label in SAMPLES],'profiles':profiles})
print(json.dumps({'profiles':len(profiles),'excluded':len(excluded),'styles':{s:sum(p['style']==s for p in profiles) for s in ['exhaustive','aggregate','representative']}},ensure_ascii=False))
