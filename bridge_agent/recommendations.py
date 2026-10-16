"""Preserve every action in explicit recommendation chapters, with model categorization."""
import json,re
from .model import function,obj,STR

CLASSIFY=function('classify_actions','Classify every supplied original recommendation; never rewrite its text.',obj({'actions':{'type':'array','items':obj({'id':STR,'part':STR,'category':{'type':'string','enum':['立即处置','尽快维修','预防性养护','非建议']},'reason':STR})}}))

def action_candidates(doc):
    candidates=[];seen=set()
    for b in doc['blocks']:
        if b.get('kind')!='paragraph' or not re.search(r'(?:养护|维修|处置|处理|维护)建议',b['section']):continue
        text=b['text']
        if len(text)<12 or re.match(r'^(?:表\s*\d|图\s*\d|照片)',text) or not re.search(r'建议|应|加强|进行|更换|修复|修补|参照|定期|严禁|及时|控制|清理',text):continue
        # Some DOC exports put the whole numbered list into a single paragraph.
        parts=re.split(r'(?=[（(]\d+[）)])',text) if len(re.findall(r'[（(]\d+[）)]',text))>1 else [text]
        for part in parts:
            if len(part.strip())<12:continue
            canonical=re.sub(r'\s|^[（(]?\d+[）).、．]','',part)
            if canonical in seen:continue
            seen.add(canonical);candidates.append({'id':f'C{len(candidates)+1:04d}','source':b['id'],'section':b['section'],'description':part.strip()})
    return candidates

def preserve_actions(model,doc,findings):
    candidates=action_candidates(doc)
    if not candidates:return
    result=[]
    for start in range(0,len(candidates),16):
        group=candidates[start:start+16];messages=[{'role':'system','content':'逐条核对原报告明确养护/处置建议章节。每个ID必须返回一次，给出部位及三种建议类别；仅标题、现状/评估描述、目录项可标非建议并解释。法规要求的保养小修、荷载控制和严禁超载、兜底按规范处理、监测巡查均是有效建议，不能省略。立即/紧急为立即处置，及时修复为尽快维修，控制荷载/巡查/保养/定期观测为预防性养护。不要改写原文，只调用classify_actions。'}, {'role':'user','content':json.dumps(group,ensure_ascii=False)}]
        for attempt in range(3):
            msg=model.call(messages,[CLASSIFY],{'type':'function','function':{'name':'classify_actions'}},max_tokens=2500)
            try:
                actions=json.loads(msg['tool_calls'][0]['function']['arguments'])['actions'];index={a['id']:a for a in actions}
                if len(actions)!=len(group) or set(index)!={c['id'] for c in group}:raise ValueError('Return each original action ID exactly once')
                accepted=[]
                for c in group:
                    a=index[c['id']]
                    if a['category'] not in ['立即处置','尽快维修','预防性养护','非建议']:raise ValueError('Invalid action category')
                    if a['category']=='非建议':
                        if re.match(r'^[（(]\d+[）)]',c['description']):raise ValueError('A numbered action in the recommendation chapter cannot be excluded: '+c['id'])
                        if not a.get('reason'):raise ValueError('Excluded action needs a reason')
                        continue
                    accepted.append({**c,'part':a['part'] or '桥梁','category':a['category'],'canonical':True})
                result.extend(accepted)
                break
            except (ValueError,KeyError,TypeError) as e:
                if attempt==2:raise
                messages.append(msg)
                if not msg.get('tool_calls'):messages.append({'role':'user','content':'必须调用classify_actions，不允许只文字确认。'})
                for call in msg.get('tool_calls',[]):messages.append({'role':'tool','tool_call_id':call['id'],'content':str(e)})
    if result:
        findings['additional_recommendations']=findings['recommendations'];findings['recommendations']=result;findings['recommendation_mode']='explicit_recommendation_chapter'
