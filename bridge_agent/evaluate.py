"""Transparent local comparisons; these are not the organizer's score."""
import re
from difflib import SequenceMatcher
from pathlib import Path
from .audit import label_data,FIELDS
from .documents import parse

def canonical(text):
    return re.sub(r'[\s，,。；;：:、（）()\[\]“”"·]','',str(text)).replace('×','x').replace('＃','#')

def evaluate(generated,label):
    cache=Path(__file__).resolve().parent.parent/'work/cache'
    a=label_data(parse(generated,cache));b=label_data(parse(label,cache))
    fixed=[]
    for field in FIELDS[:12]:
        expected=b['summary'].get(field,'');actual=a['summary'].get(field,'');ok=canonical(expected)==canonical(actual)
        fixed.append({'field':field,'expected':expected,'actual':actual,'exact':ok})
    candidates=[]
    for i,r in enumerate(a['diseases']):
        for j,t in enumerate(b['diseases']):
            if len(r)<7 or len(t)<7:continue
            desc=SequenceMatcher(None,canonical(r[3]),canonical(t[3]),autojunk=False).ratio()
            # A location mismatch is visible in the item-level comparison below.
            candidates.append((desc,i,j))
    used_a=set();used_b=set();matched=[]
    for score,i,j in sorted(candidates,reverse=True):
        if score<0.6:break
        if i in used_a or j in used_b:continue
        used_a.add(i);used_b.add(j);r=a['diseases'][i];t=b['diseases'][j]
        nums=lambda s:re.findall(r'\d+(?:\.\d+)?',s)
        matched.append({'generated_index':i+1,'label_index':j+1,'description_similarity':round(score,4),'generated':r,'label':t,'history_exact':canonical(r[4])==canonical(t[4]),'numeric_tokens_equal':nums(r[3])==nums(t[3])})
    tp=len(matched);precision=tp/max(1,len(a['diseases']));recall=tp/max(1,len(b['diseases']))
    return {'generated':str(generated),'label':str(label),'basic_exact_count':sum(x['exact'] for x in fixed),'basic_total':12,'basic_fields':fixed,'structure':{'field_order':list(a['summary'])==FIELDS,'recommendation_columns':4,'disease_columns':7,'actual_fields':len(a['summary'])},'disease':{'generated':len(a['diseases']),'label':len(b['diseases']),'matched':tp,'precision':round(precision,4),'recall':round(recall,4),'f1':round(2*precision*recall/(precision+recall),4) if precision+recall else 0,'numeric_equal_on_matches':sum(x['numeric_tokens_equal'] for x in matched),'history_equal_on_matches':sum(x['history_exact'] for x in matched)},'recommendations':{'generated':len(a['recommendations']),'label':len(b['recommendations'])},'matches':matched,'unmatched_generated':[r for i,r in enumerate(a['diseases']) if i not in used_a],'unmatched_label':[r for j,r in enumerate(b['diseases']) if j not in used_b],'limits':'Greedy description similarity >=0.6; sensitive to split/merged observations. Numeric equality compares token lists. No official scorer supplied; this is not an official score.'}
