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
    messages=[{'role':'system','content':'你是参赛报告的病害表编审。完整原始观测台账已保存，现根据训练标签的代表性汇总写法组织最终病害表。长报告不用把数百个同类套筒划痕/照片重复项全部展开。按少数宏观构件（主梁、横隔板、拉索、主塔、桥墩桥台、支座、桥面及附属设施）归并病害类型，每个原始表格至少保留一个代表项，同一宏观构件内的近义/组合类型共享代表项，不必每个细分类组单列。常见类型通常选最先出现的2处典型原文观测，裂缝组最多4处；另补严重/危险/特别尺寸的少量例外。宏观构件内的近义、组合类型通过同一代表观测覆盖，避免为细分病名各新增一行。原文顺序是重要的选择依据。相同构件与类型的多个位置可保留多行。覆盖主桥/引桥、上部/下部/桥面系，不把不同构件的位置描述合并。只从给定示例ID选择，最终通常30-50行，尽量不超过60行；不要为了覆盖细分术语堆积到上百行。附属机电故障仅选1-2个代表项。不编造新观测。仅调用select_diseases，reason概括选取准则。'}, {'role':'user','content':json.dumps(request,ensure_ascii=False)}]
    msg=model.call(messages,[SELECT],{'type':'function','function':{'name':'select_diseases'}},max_tokens=2500)
    result=json.loads(msg['tool_calls'][0]['function']['arguments']);ids=result['ids']
    if not ids or len(ids)!=len(set(ids)) or any(i not in visible for i in ids):raise ValueError('Invalid disease selection IDs')
    chosen=set(ids);by_component=defaultdict(list)
    for (component,kind),rows in groups.items():by_component[component].extend(rows)
    missing=[str(key) for key,rows in by_component.items() if not any(d['id'] in chosen for d in rows)]
    for repair in range(2):
        if not missing:break
        supplement=[g for g in summaries if str(g['table_or_section']) in missing]
        # Preserve accepted choices and ask only about the omitted component(s).
        repair_messages=[{'role':'system','content':'补充已完成病害表的遗漏构件。只从提供的示例ID中为每个构件至少选择一个代表项，调用select_diseases；无需重写或重新选择此前已接受的内容。'}, {'role':'user','content':json.dumps({'missing_components':supplement},ensure_ascii=False)}]
        fixed=model.call(repair_messages,[SELECT],{'type':'function','function':{'name':'select_diseases'}},max_tokens=1200)
        extra=json.loads(fixed['tool_calls'][0]['function']['arguments'])
        allowed={d['id'] for g in supplement for d in g['examples']}
        if not extra.get('ids') or any(i not in allowed for i in extra['ids']):raise ValueError('Invalid supplemental selection')
        for sid in extra['ids']:
            if sid not in chosen:ids.append(sid);chosen.add(sid)
        result['reason']+='；补充：'+extra.get('reason','')
        missing=[str(key) for key,rows in by_component.items() if not any(d['id'] in chosen for d in rows)]
    if missing:raise ValueError('Selection omitted disease groups: '+','.join(missing[:10]))
    result.update(total_candidates=len(all_rows),selected=len(ids),groups=len(groups));save_json(path,result);return result
