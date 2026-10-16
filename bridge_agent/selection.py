"""Keep the complete evidence ledger; choose representative rows for large labels."""
import json,re
from collections import defaultdict
from .documents import save_json
from .model import function,obj,STR,STRS

SELECT=function('select_diseases','Select representative disease IDs for the final report, preserving severe/rare findings across every component/type.',obj({'ids':STRS,'reason':STR}))

def selection(model,doc,findings,path):
    all_rows=findings['diseases']
    if len(all_rows)<=80:
        result={'ids':[d['id'] for d in all_rows],'reason':'Small ledger: preserve all independently identified current observations.','total_candidates':len(all_rows)};save_json(path,result);return result
    idx={b['id']:b for b in doc['blocks']};groups=defaultdict(list)
    for d in all_rows:
        b=idx[d['source']];groups[(b.get('table',b.get('section','')),d['kind'])].append(d)
    summaries=[];visible=set()
    def severity(d):
        text=d['description'];return sum(5 for t in ['严重','贯通','脱空','断裂','冲刷','倾斜','坍塌','超限','露筋','开裂'] if t in text)+len(re.findall(r'\d+(?:\.\d+)?(?:mm|㎜)',text))
    for (component,kind),rows in groups.items():
        selected=[];seen=set()
        for d in sorted(rows,key=lambda d:-severity(d))[:3]+rows[:2]+rows[-1:]:
            if d['id'] not in seen:seen.add(d['id']);visible.add(d['id']);selected.append({k:d.get(k) for k in ['id','part','kind','description','source']})
        summaries.append({'table_or_section':component,'kind':kind,'count':len(rows),'examples':selected})
    request={'all_observations':len(all_rows),'groups':summaries,'major_risks':findings['safety'][:12],'history_notes':findings['history_notes'][:8]}
    if len(json.dumps(request,ensure_ascii=False))>45000:raise ValueError('Selection overview too large; refine component groups')
    messages=[{'role':'system','content':'你是参赛报告的病害表编审。完整原始观测台账已保存，现根据训练标签的代表性汇总写法组织最终病害表。长报告不用把数百个同类套筒划痕/照片重复项全部展开；每个构件-病害类型组至少选择1处，常见重复类型通常选2-3处最典型观测，所有严重/危险/特别尺寸/独立新增类型必须保留。相同构件与类型的多个位置可保留多行。覆盖主桥/引桥、上部/下部/桥面系，不把不同构件的位置合并。只从给定示例ID选择，通常总行数30-80，可为覆盖更多类型突破80；不编造新观测。仅调用select_diseases，reason概括选取准则。'}, {'role':'user','content':json.dumps(request,ensure_ascii=False)}]
    msg=model.call(messages,[SELECT],{'type':'function','function':{'name':'select_diseases'}},max_tokens=2500)
    result=json.loads(msg['tool_calls'][0]['function']['arguments']);ids=result['ids']
    if not ids or len(ids)!=len(set(ids)) or any(i not in visible for i in ids):raise ValueError('Invalid disease selection IDs')
    chosen=set(ids);missing=[str(key) for key,rows in groups.items() if not any(d['id'] in chosen for d in rows)]
    if missing:raise ValueError('Selection omitted disease groups: '+','.join(missing[:10]))
    result.update(total_candidates=len(all_rows),selected=len(ids),groups=len(groups));save_json(path,result);return result
