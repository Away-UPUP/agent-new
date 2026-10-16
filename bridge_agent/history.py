"""Bounded matching against explicit historical/comparison evidence."""
import concurrent.futures,json,re
from collections import Counter
from .documents import compact,read_json,save_json
from .model import function,obj,STR,STRS

MATCH=function('save_comparisons','Compare each listed disease only with the supplied explicit historical evidence.',obj({'matches':{'type':'array','items':obj({'id':STR,'new':{'type':'string','enum':['是','否','无法判断']},'previous':STR,'development':STR,'sources':STRS,'quote':STR})}}))

def historical_pool(doc, findings):
    ids={s for note in findings['history_notes'] for s in note['sources']}
    table_ids=set()
    for b in doc['blocks']:
        # Require explicit comparison wording, not just a year in a cover/header.
        if re.search(r'对比|比较|上[一]?次|历年|历次|往年',b['section']+' '+str(b.get('header',[]))):
            ids.add(b['id'])
            if 'table' in b:table_ids.add(b['table'])
    return [b for b in doc['blocks'] if b['id'] in ids or b.get('table') in table_ids]

def match_history(model, doc, findings, directory, concurrency=4):
    directory.mkdir(exist_ok=True,parents=True);pool=historical_pool(doc,findings)
    pending=[d for d in findings['diseases'] if not d.get('history_sources')]
    if not pool:
        return {'status':'no_explicit_comparison_evidence','matched':0,'pending':len(pending),'pool_blocks':0}
    def run(pair):
        groupno,group=pair;dest=directory/f'{groupno:04d}.json'
        if dest.exists():return read_json(dest)
        # Rank by component, location tokens and disease type; keep multiple candidates.
        chosen={}
        for d in group:
            terms=[d['part'],d['kind']]+re.findall(r'\d+[#＃号]|[\u4e00-\u9fff]{2,4}',d['part'])
            rank=sorted(pool,key=lambda b:sum(t in b['text'] for t in terms),reverse=True)
            for b in rank[:6]:chosen[b['id']]=b
        evidence=[];chars=0
        for b in chosen.values():
            n=len(json.dumps(compact(b),ensure_ascii=False))
            if chars+n<=16000:evidence.append(b);chars+=n
        evidence_ids={b['id'] for b in evidence}
        messages=[{'role':'system','content':'你是桥梁病害历史核对智能体。只根据明确的检测年份/当前与上次对比表及同一部位、构件编号判断；不同墩号/跨号/左右幅不可合并。资料缺失或未检索到匹配项不等于新增或修复。new=是需原文明示本次新增或同一位置上次未发现；new=否需匹配到同一病害。无充分依据填无法判断，previous=无，development=无，sources=[]，quote=空。不能将建议维修当已修复。development无明确量化/发展描述则无。每个id均需返回。quote逐字摘录历史证据的关键句，避免只引用“对比表”标题。调用save_comparisons。'}, {'role':'user','content':json.dumps({'current':[{k:d.get(k,'') for k in ['id','part','kind','description','source']} for d in group],'historical_candidates':[compact(b) for b in evidence]},ensure_ascii=False)}]
        for attempt in range(2):
            msg=model.call(messages,[MATCH],{'type':'function','function':{'name':'save_comparisons'}},max_tokens=4500)
            try:
                call=msg['tool_calls'][0];data=json.loads(call['function']['arguments']);matches=data['matches']
                if Counter(x['id'] for x in matches)!=Counter(d['id'] for d in group):raise ValueError('Each disease ID required exactly once')
                index={b['id']:b for b in evidence}
                for m in matches:
                    if m['new'] not in ['是','否','无法判断']:raise ValueError('Invalid status')
                    if any(s not in evidence_ids for s in m['sources']):raise ValueError('Invalid comparison source')
                    if m['new'] in ['是','否']:
                        if not m['sources']:raise ValueError('Comparison without an original historical source')
                        if not m['quote'] or not any(re.sub(r'\s','',m['quote']) in re.sub(r'\s','',index[s]['text']) for s in m['sources']):
                            # Restore the actual evidence, retaining no paraphrase as a verbatim quote.
                            m['quote']=index[m['sources'][0]]['text'];m['quote_reconstructed']=True
                save_json(dest,matches);return matches
            except (ValueError,KeyError,TypeError) as e:
                if attempt:raise
                messages.append(msg)
                for c in msg.get('tool_calls',[]):messages.append({'role':'tool','tool_call_id':c['id'],'content':str(e)})
        raise RuntimeError('History matching exhausted')
    groups=[pending[i:i+16] for i in range(0,len(pending),16)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:results=list(executor.map(run,enumerate(groups)))
    disease_index={d['id']:d for d in findings['diseases']}
    for result in results:
        for m in result:
            d=disease_index[m['id']]
            d.update(new=m['new'],previous=m['previous'],development=m['development'],history_sources=m['sources'],history_quote=m['quote'])
    return {'status':'compared','matched':sum(bool(d.get('history_sources')) for d in findings['diseases']),'pending':len(pending),'pool_blocks':len(pool)}
