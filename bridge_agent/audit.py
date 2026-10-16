"""Inventory and learn the stable contract from authorized labels, never at inference."""
import argparse, concurrent.futures, re, shutil
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from .documents import parse, save_json, sniff

FIELDS=['桥梁名称','报告日期','总体评分','总体等级','上部结构评分','上部结构等级','下部结构评分','下部结构等级','桥面系评分','桥面系等级','上一次总体评分','上一次总体等级','病害发展趋势与具体说明','总体结论','主要风险点','建议']
REC_HEADERS=['序号','建议类别','建议内容','病害部位']
DISEASE_HEADERS=['序号','病害部位','病害类型','病害描述','是否新增','上一次定检状态','发展程度']

def label_data(doc):
    summary={}; descriptions={}; recs=[];diseases=[]
    for rows in doc['tables']:
        if not rows:continue
        h=rows[0]
        if h[0]=='字段' or h[0]=='桥梁名称':
            for row in (rows[1:] if h[0]=='字段' else rows):
                if len(row)>=2:summary[row[0]]=row[1];descriptions[row[0]]=row[2] if len(row)>2 else ''
        elif h[:2]==REC_HEADERS[:2]:recs.extend(rows[1:])
        elif len(h)>=6 and any('是否新增' in c for c in h):
            for row in rows[1:]:
                diseases.append(row if len(h)==7 else ['']+row)
    return {'summary':summary,'descriptions':descriptions,'recommendations':recs,'diseases':diseases}

def norm(s):
    s=re.split(r'(?:无对比|有对比|含有?对比)',s)[0]
    return re.sub(r'[\W_]|docx?|报告|桥梁检测评估|定期检测|检查|信息提取','',s.lower())

def audit(root):
    root=Path(root);cache=root/'work/cache';out=root/'artifacts';out.mkdir(exist_ok=True)
    labels=sorted(p for p in (root/'赛题一_训练集标签').rglob('*') if p.is_file());sources=sorted(p for p in (root/'赛题一_训练集').rglob('*') if p.is_file())
    field_orders=Counter();headers=Counter();titles=Counter();errors=[];labels_data={};examples=[];style_template=None
    for p in labels:
        try:
            d=parse(p,cache);ld=label_data(d);labels_data[p]=ld
            field_orders[tuple(ld['summary'])]+=1
            for rows in d['tables']:
                if rows:headers[tuple(rows[0])]+=1
            for b in d['blocks']:
                if b['kind']=='paragraph' and len(b['text'])<25:titles[b['text']]+=1
            if len(examples)<3 and len(ld['diseases'])>2 and (len(examples)==0 or ('上一次总体评分' in ld['summary'] and ld['summary']['上一次总体评分']!='无')):
                examples.append({'label':str(p.relative_to(root)), 'summary_excerpt':{k:ld['summary'].get(k,'无') for k in ['病害发展趋势与具体说明','主要风险点']},'recommendations':ld['recommendations'][:2],'diseases':ld['diseases'][:3]})
            if style_template is None and len(ld['summary'])==16 and sniff(p)=='docx':style_template=p
        except Exception as e:errors.append({'file':str(p),'error':str(e)})
    print('labels parsed',len(labels_data),flush=True)
    save_json(out/'label_patterns.json',{'labels':len(labels),'readable':len(labels_data),'field_orders':[{'fields':list(k),'count':v} for k,v in field_orders.most_common()],'headers':[{'cells':list(k),'count':v} for k,v in headers.most_common()],'titles':titles.most_common(30),'errors':errors})
    desc=next((ld['descriptions'] for ld in labels_data.values() if len(ld['summary'])==16),{})
    save_json(root/'assets/label_spec.json',{'fields':FIELDS,'descriptions':desc,'summary_headers':['字段','示例','说明'],'recommendation_headers':REC_HEADERS,'disease_headers':DISEASE_HEADERS,'headings':['1、简要信息','2、详细信息','（1）详细结论','（2）建议明细','（3）病害列表','病害成因：','处置建议：','安全影响：'],'missing':'无','no_history_disease':['否','无','无'],'recommendation_categories':['立即处置','尽快维修','预防性养护'],'provenance':'Derived from all readable supplied training labels; optional cover titles/date stamps omitted.'})
    save_json(root/'assets/few_shot.json',examples)
    # Keep only formatting in the template: delete every sample text and sample row.
    if style_template:
        from docx import Document
        doc=Document(style_template)
        for child in list(doc.element.body):
            if not child.tag.endswith('}sectPr'):doc.element.body.remove(child)
        doc.save(root/'assets/layout.docx')
    def inspect(p):
        entry={'source':str(p.relative_to(root)),'format':sniff(p)}
        try:
            d=parse(p,cache);entry.update(readable=True,pages=d['pages_metadata'],paragraphs=d['paragraphs'],tables=d['table_count'],chars=d['chars'],images=d['images'],sha256=d['sha256'])
            candidates=[l for l in labels_data if l.parent.name==p.parent.name]
            ranked=sorted(candidates,key=lambda l:SequenceMatcher(None,norm(p.name),norm(l.name)).ratio(),reverse=True)
            if ranked:
                label=ranked[0];ld=labels_data[label];name=ld['summary'].get('桥梁名称','');alltext=''.join(b['text'] for b in d['blocks']);clean=lambda x:re.sub(r'\s','',x)
                name_seen=clean(name) in clean(alltext) if name else False
                score=SequenceMatcher(None,norm(p.name),norm(label.name)).ratio()
                entry.update(label=str(label.relative_to(root)),filename_similarity=round(score,4),label_bridge=name,identity_in_source=name_seen,pair_reliable=score>=0.85 and name_seen)
        except Exception as e:entry.update(readable=False,error=str(e))
        return entry
    entries=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for i,item in enumerate(pool.map(inspect,sources),1):
            entries.append(item)
            if i%20==0:print('sources',i,'/',len(sources),flush=True);save_json(out/'training_pairs.json',entries)
    save_json(out/'training_pairs.json',entries)
    tests=[]
    for p in sorted((root/'初赛测试集').iterdir()):
        if p.is_file():
            try:
                d=parse(p,cache);tests.append({'source':str(p.relative_to(root)),'chars':d['chars'],'pages':d['pages_metadata'],'tables':d['table_count'],'format':d['format']})
            except Exception as e:tests.append({'source':str(p.relative_to(root)),'error':str(e)})
    save_json(out/'test_inventory.json',tests)
    summary={'training':len(sources),'labels':len(labels),'label_read_errors':errors,'source_read_errors':[e for e in entries if not e['readable']],'reliable_pairs':sum(e.get('pair_reliable',False) for e in entries),'test':len(tests),'duplicate_label_assignments':[k for k,v in Counter(e.get('label') for e in entries).items() if v>1]}
    save_json(out/'audit_summary.json',summary);print(summary,flush=True)

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--root',default='.');audit(a.parse_args().root)
