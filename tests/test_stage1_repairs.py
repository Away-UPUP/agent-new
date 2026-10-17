import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from docx import Document

from bridge_agent.dates import date_end, date_policy, validate_date
from bridge_agent.documents import parse
from bridge_agent.extraction import FINDINGS, extract_batch, validate_findings
from bridge_agent.selection import selection


def empty_findings():
    return {k: [] for k in FINDINGS['properties']}


class Stage1RepairTests(unittest.TestCase):
    def test_training_inspection_range_endpoints_and_report_priority(self):
        for raw, expected in [('2012/3/24～6/12', '2012年6月12日'),
                              ('2013/4/18～9/23', '2013年9月23日'),
                              ('2014/7/28～7/29', '2014年7月29日'),
                              ('2020年12月30日～2021年1月3日', '2021年1月3日'),
                              ('2020/12/30～1/3', '2021年1月3日'),
                              ('2021年6月8日至10日', '2021年6月10日'),
                              ('二〇一八年十二月三十一日', '2018年12月31日')]:
            with self.subTest(raw=raw):
                b={'id':'B1','text':'检验日期（Sampling date） | '+raw+' | 抽样数量 | 见报告','section':''}
                policy=date_policy([b]);self.assertEqual(policy['basis'],'inspection_end')
                self.assertEqual(policy['candidates'][0]['value'],expected)
                data=empty_findings();data['facts']=[{'field':'报告日期','value':expected,'source':'B1','quote':raw}]
                self.assertEqual(validate_findings(data,[b],policy)['facts'][0]['value'],expected)
        inspection={'id':'B1','text':'检验日期 | 2021.01.03','section':''}
        report={'id':'B2','text':'报 告 日 期 ： 2021年06月08日','section':''}
        cover={'id':'B3','text':'二〇二一年五月','section':''}
        policy=date_policy([inspection,report,cover]);self.assertEqual(policy['basis'],'report')
        validate_date('2021/6/8',['B2'],policy)
        with self.assertRaises(ValueError):validate_date('2021.01.03',['B1'],policy)
        self.assertEqual(date_policy([inspection,cover])['candidates'][0]['value'],'2021年5月')
        historical={**inspection,'section':'历年检测对比'}
        self.assertEqual(date_policy([historical])['candidates'],[])
        issued={'id':'B4','text':'报 告 发 出 日 期：2012年11月12日 审核：某某 公司 二〇一二年十一月','section':''}
        policy=date_policy([{'id':'B5','text':'二〇一二年十一月','section':''},issued])
        self.assertEqual(policy['basis'],'cover');self.assertEqual(policy['candidates'][0]['value'],'2012年11月')
        self.assertEqual(date_policy([issued])['candidates'][0]['value'],'2012年11月12日')
        self.assertEqual(date_end('2012年11月12日 公司 二〇一二年十一月'),'2012年11月12日')

    def test_identical_dotted_date_is_supported_but_invented_date_is_not(self):
        b={'id':'B1','text':'2021.01.03','section':''}
        data=empty_findings();data['facts']=[{'field':'报告日期','value':'2021.01.03','source':'B1','quote':b['text']}]
        self.assertEqual(validate_findings(data,[b])['facts'][0]['value'],'2021年1月3日')
        data['facts'][0]['value']='2021年3月1日'
        with self.assertRaises(ValueError):validate_findings(data,[b])
        with self.assertRaises(ValueError):date_end('2021/2/30')

    def test_only_local_date_candidates_are_exposed_in_each_source_batch(self):
        from bridge_agent.extraction import extract_all
        blocks=[{'id':'B1','text':'报告日期：2021年6月8日','section':''},
                {'id':'B2','text':'检验日期：2021年1月3日','section':''}]
        policies=[]
        def fake(model,bs,path,identity):
            policy=identity['date_policy'];policies.append(policy)
            self.assertTrue(all(c['source'] in {b['id'] for b in bs} for c in policy['candidates']))
            return empty_findings()
        with tempfile.TemporaryDirectory() as td, patch('bridge_agent.extraction.extract_batch',side_effect=fake), patch('bridge_agent.recommendations.preserve_actions'):
            extract_all(None,{'filename':'test.doc','blocks':blocks,'tables':[]},Path(td),concurrency=1,budget=150)
        self.assertEqual(len(policies),2)
        self.assertEqual(policies[0]['candidates'][0]['source'],'B1')
        self.assertEqual(policies[1]['candidates'],[])
        with self.assertRaisesRegex(ValueError,'omit the date'):
            validate_date('2021年1月3日',['B2'],policies[1])

    def test_final_submission_uses_the_same_inspection_fallback(self):
        from bridge_agent.agent import validate_report
        from bridge_agent.audit import FIELDS
        blocks=[{'id':'B1','text':'测试桥','section':''},
                {'id':'B2','text':'检验日期 | 2012/3/24～6/12','section':''}]
        basic={f:{'value':'无','sources':[]} for f in FIELDS[:12]}
        basic['桥梁名称']={'value':'测试桥','sources':['B1']}
        basic['报告日期']={'value':'2012.06.12','sources':['B2']}
        claim={'text':'资料未提供，无法判断。','sources':[],'inferred':False}
        report={'basic':basic,'has_history':False,'trend':claim,'overall':claim,'risks':claim,
                'conclusion':[claim]*3,'causes':[claim],'safety':[claim],'recommendation_ids':[]}
        findings=empty_findings();findings.update(no_disease_sources=['B1'],coverage={'processed_blocks':2,'total_blocks':2})
        self.assertEqual(validate_report(report,{'blocks':blocks},findings,{'B1','B2'}),report)
        basic['报告日期']['value']='2012年3月24日'
        with self.assertRaises(ValueError):validate_report(report,{'blocks':blocks},findings,{'B1','B2'})

    def test_repair_sees_all_errors_and_exact_alternative_sources(self):
        blocks=[{'id':'B1','text':'桥台局部渗水、泛碱。','section':''},
                {'id':'B2','text':'距2#伸缩缝15m处，桥面铺装中央坑槽','section':''},
                {'id':'B3','text':'1#伸缩缝堵塞','section':''}]
        data=empty_findings()
        def disease(sid,quote):
            return {'source':sid,'part':'桥台','kind':'泛碱','cols':[],'quote':quote,
                    'previous':'','new':'','development':'','history_sources':[]}
        data['diseases']=[disease('B1','桥台局部泛碱'),disease('B3',blocks[1]['text'])]
        with self.assertRaises(ValueError) as exc:validate_findings(data,blocks)
        issues=json.loads(str(exc.exception))['validation_errors']
        self.assertEqual(len(issues),2)
        self.assertEqual(issues[0]['actual_source']['text'],blocks[0]['text'])
        self.assertEqual(issues[1]['exact_quote_sources'],['B2'])
        class FakeModel:
            def __init__(self):self.calls=0
            def call(self,messages,*args,**kwargs):
                self.calls+=1
                if self.calls==2:
                    feedback=json.loads(messages[-1]['content'])
                    assert 'diseases[0]' in feedback['error'] and 'diseases[1]' in feedback['error']
                    data['diseases'][0]['quote']='泛碱';data['diseases'][1]['source']='B2'
                return {'role':'assistant','tool_calls':[{'id':'c','function':{'name':'save_findings','arguments':json.dumps(data)}}]}
        with tempfile.TemporaryDirectory() as td:
            model=FakeModel();result=extract_batch(model,blocks,Path(td)/'batch.json',{})
            self.assertEqual(model.calls,2);self.assertEqual(len(result['diseases']),2)
            self.assertEqual(result['diseases'][1]['description'],blocks[1]['text'])

    def test_antiword_failure_converts_without_changing_original(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'bad.doc';original=b'\xd0\xcf\x11\xe0'+b'\0'*508;src.write_bytes(original)
            converted=root/'recovered.docx';doc=Document();doc.add_paragraph('恢复的报告正文');doc.save(converted)
            with (patch('bridge_agent.documents.docbook',side_effect=RuntimeError('antiword text export failed: broken OLE')),
                  patch('bridge_agent.documents.converted',return_value=converted) as convert):
                result=parse(src,root/'cache')
            self.assertGreaterEqual(convert.call_count,1)
            self.assertEqual(result['blocks'][0]['text'],'恢复的报告正文')
            self.assertIn('broken OLE',result['parse_warnings'][0]);self.assertEqual(src.read_bytes(),original)

    def test_group_truncation_splits_and_extra_ids_receive_specific_feedback(self):
        doc={'filename':'unknown.docx','blocks':[{'id':f'B{i:06d}','text':'桥墩裂缝','section':'检查','kind':'row','table':i} for i in range(1,8)]}
        rows=[{'id':f'D{i}_{j}','source':f'B{i:06d}','part':'桥墩','kind':'裂缝','description':f'{i}号墩位置{j}裂缝'} for i in range(1,8) for j in range(12)]
        class FakeModel:
            concurrency=1
            def __init__(self):self.calls=0;self.truncated=False;self.extra=False;self.specific=False
            def call(self,messages,tools,*args,**kwargs):
                self.calls+=1;payload=json.loads(messages[1]['content']);groups=payload['groups']
                schema=tools[0]['function']['parameters']['properties']['groups']
                assert schema['minItems']==schema['maxItems']==len(groups)
                if len(groups)==6 and not self.truncated:
                    self.truncated=True;raise ValueError('Model output incomplete: length')
                specs=[{'id':g['id'],'part':'桥墩','kind':'裂缝','summary_source':'','summary_quote':'','example_ids':[g['examples'][0]['id']]} for g in groups]
                if not self.extra:
                    self.extra=True;specs.append({**specs[0],'id':'G9999'})
                elif len(messages)>2:
                    feedback=json.loads(messages[-1]['content']);self.specific=feedback['extra']==['G9999']
                return {'role':'assistant','tool_calls':[{'id':'c','function':{'name':'organize_groups','arguments':json.dumps({'groups':specs})}}]}
        model=FakeModel()
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'selection.json';findings={'facts':[],'diseases':rows}
            result=selection(model,doc,findings,path)
            self.assertTrue(model.truncated and model.specific)
            self.assertEqual(result['groups'],7);self.assertEqual(len(result['rows']),7)
            self.assertEqual(sum(g['member_count'] for g in result['organized_groups']),84)
            calls=model.calls;self.assertEqual(selection(model,doc,findings,path),result)
            self.assertEqual(model.calls,calls)

    def test_truncated_model_response_is_saved_but_never_reused_as_success(self):
        from bridge_agent.model import Model
        class Client:
            def post(self,*args,**kwargs):
                class Response:
                    status_code=200
                    def json(self):return {'choices':[{'finish_reason':'length','message':{'role':'assistant','content':'truncated'}}],'usage':{'completion_tokens':4500}}
                return Response()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'.env').write_text('INSPECTION_API_BASE_URL=https://model.invalid/v1\nINSPECTION_API_KEY=dummy\nINSPECTION_MODEL=test\n')
            model=Model(root,root/'work');model.client.close();model.client=Client()
            with self.assertRaisesRegex(ValueError,'incomplete'):model.call([],[],max_tokens=4500)
            self.assertEqual(len(list((root/'work/incomplete_calls').glob('*.json'))),1)
            self.assertEqual(list((root/'work/calls').glob('*.json')),[])


if __name__=='__main__':unittest.main()
