"""Organize large ledgers without dropping component/type groups or inventing totals."""
import concurrent.futures,json,re
from collections import defaultdict,Counter
from .documents import save_json
from .model import function,obj,STR,STRS

SUMMARY=function('organize_groups','Name each provided disease group and optionally quote an exact current summary span from the source.',obj({'groups':{'type':'array','items':obj({'id':STR,'part':STR,'kind':STR,'summary_source':STR,'summary_quote':STR,'example_ids':STRS})}}))

def family(kind):
    if re.search(r'裂|开裂',kind):return '裂缝'
    if re.search(r'露筋|破损|坑|剥落',kind):return '破损、露筋' if not re.search(r'涂层|涂装|油漆|防水|止水',kind) else kind
    if re.search(r'渗|泛碱|漏水',kind):return '渗水、泛碱'
    return kind

def group_rows(rows,doc):
    index={b['id']:b for b in doc['blocks']};groups=defaultdict(list)
    for d in rows:
        b=index[d['source']]
        # Separate tables preserve left/right and main/approach scopes from captions.
        component=('table',b['table']) if b.get('table') else ('paragraph',d['part'])
        groups[(component,family(d['kind']))].append(d)
    return list(groups.values())

def materialize_group(spec,group,evidence):
    index={d['id']:d for d in group};ids=spec['example_ids']
    if not ids or len(ids)>3 or len(ids)!=len(set(ids)) or any(i not in index for i in ids):raise ValueError('Choose 1-3 original examples from this group')
    quote=spec['summary_quote'];sid=spec['summary_source']
    if bool(quote)!=bool(sid):raise ValueError('Summary source and verbatim quote required together')
    if quote and (sid not in evidence or re.sub(r'\s','',quote) not in re.sub(r'\s','',evidence[sid]['text'])):raise ValueError('Summary quote absent from original source')
    part=spec['part'];context=group[0].get('table_context','')
    for pair in [('左幅','右幅'),('主桥','引桥')]:
        present=[term for term in pair if term in context]
        if len(present)==1:
            expected=present[0];opposite=pair[1-pair.index(expected)]
            if opposite in part:raise ValueError('Component scope contradicts original table caption: '+context)
            if expected not in part:part=expected+part
    selected=[index[i] for i in ids]
    descriptions=list(dict.fromkeys(d['description'] for d in selected))
    if len(group)==1:description=group[0]['description']
    elif quote:description=quote+'（如：'+'；'.join(descriptions)+'）'
    else:description=f'共记录{len(group)}项{spec["kind"]}观测（如：'+'；'.join(descriptions)+'等）'
    sources=list(dict.fromkeys([d['source'] for d in group]+([sid] if sid else [])))
    return {**group[0],'part':part,'kind':spec['kind'],'description':description,'sources':sources,'member_ids':[d['id'] for d in group],'member_count':len(group),'summary_source':sid,'summary_quote':quote,'previous':'','new':'','development':'','history_sources':[]}

def selection(model,doc,findings,path):
    rows=findings['diseases']
    if len(rows)<=80:
        result={'ids':[d['id'] for d in rows],'reason':'Small ledger: retain every verified current observation.','total_candidates':len(rows)};save_json(path,result);return result
    grouped=group_rows(rows,doc);index={b['id']:b for b in doc['blocks']}
    # Current summaries supply exact physical counts; computed fallback counts only ledger entries.
    summaries=[b for b in doc['blocks'] if b.get('kind')!='row' and re.search(r'存在|共计|共发现|发现.*处|总面积|总长',b['text']) and re.search(r'裂|蚀|漏|渗|碱|缺|脱|损',b['text']) and not re.search(r'上一次|上次|历年|对比|比较|往年',b['section']) and len(b['text'])<1200]
    # Local summaries, rather than the large front-page synopsis, keep each call bounded.
    def run(start):
        groups=grouped[start:start+12];items=[];chosen={}
        for n,group in enumerate(groups,start):
            first=index[group[0]['source']];at=int(first['id'][1:])
            nearby=sorted(summaries,key=lambda b:abs(int(b['id'][1:])-at))[:8]
            for b in nearby:chosen[b['id']]=b
            examples=group[:3]+sorted(group,key=lambda d:len(d['description']),reverse=True)[:2]+group[-1:]
            unique={d['id']:{k:(d[k][:400] if k in ['description','part'] else d[k]) for k in ['id','part','kind','description','source']} for d in examples}
            items.append({'id':f'G{n:04d}','table_context':first.get('table_context',first['section'])[:400],'kind_counts':dict(Counter(d['kind'] for d in group)),'ledger_count':len(group),'examples':list(unique.values())})
        bounded=[];used=0
        for b in chosen.values():
            if used+len(b['text'])>12000:continue
            bounded.append(b);used+=len(b['text'])
        chosen={b['id']:b for b in bounded}
        evidence={b['id']:b for b in chosen.values()}
        messages=[{'role':'system','content':'你是桥梁分析报告病害表编审。完整台账已保存，现在逐组整理汇总，禁止丢弃任一给定组。按表题明确左幅/右幅、主桥/引桥、箱梁内/外，part用宏观部位，不让示例中的第几跨代替整个分组。kind用标签的简洁病名。同组所有观测均保留关联，example_ids选本组最先出现的1-3项典型位置，优先保留最早项目及严重例外，不能使用其他组ID。若原文摘要中有精确对应同侧同构件同类型的数量，可summary_source和summary_quote逐字摘取该连续片段；不要把其他构件的计数混入，不要改写原文，不要引用历史数量。没有准确对应摘要就两个字段都空。程序恢复原文例子和计数，不要自行计算或编造总长度/面积。每个组ID必须返回一次，只调用organize_groups。'}, {'role':'user','content':json.dumps({'groups':items,'current_summary_sources':[{'id':b['id'],'section':b['section'],'text':b['text']} for b in chosen.values()]},ensure_ascii=False)}]
        for attempt in range(2):
            msg=model.call(messages,[SUMMARY],{'type':'function','function':{'name':'organize_groups'}},max_tokens=4500)
            try:
                specs=json.loads(msg['tool_calls'][0]['function']['arguments'])['groups'];by_id={s['id']:s for s in specs}
                if len(specs)!=len(items) or set(by_id)!={x['id'] for x in items}:raise ValueError('Every group must be returned exactly once')
                return [materialize_group(by_id[x['id']],g,evidence) for x,g in zip(items,groups)]
            except (ValueError,KeyError,TypeError) as e:
                if attempt:raise
                messages.append(msg)
                for c in msg.get('tool_calls',[]):messages.append({'role':'tool','tool_call_id':c['id'],'content':str(e)})
        raise RuntimeError('Group organization failed')
    with concurrent.futures.ThreadPoolExecutor(max_workers=model.concurrency) as executor:parts=list(executor.map(run,range(0,len(grouped),12)))
    final=[d for part in parts for d in part]
    for i,d in enumerate(final,1):d['id']=f'A{i:05d}'
    result={'ids':[d['id'] for d in final],'rows':final,'reason':'Every source-table/component and normalized disease-type group retained; exact source summary plus original examples, complete ledger references.','total_candidates':len(rows),'selected':len(final),'groups':len(grouped)}
    save_json(path,result);return result
