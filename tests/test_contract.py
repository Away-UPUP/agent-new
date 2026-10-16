import json,tempfile,unittest
from pathlib import Path
from docx import Document
from bridge_agent.documents import parse,search,save_json
from bridge_agent.extraction import validate_findings,chunks,source_text
from bridge_agent.audit import label_data,FIELDS
from bridge_agent.render import render,materialize
from bridge_agent.render import normalize_date
from bridge_agent.agent import fact_candidates
from bridge_agent.extraction import extract_all,FINDINGS
from unittest.mock import patch
from bridge_agent.model import Model

class ContractTests(unittest.TestCase):
    def test_grouped_summary_rejects_invented_totals_and_keeps_all_members(self):
        from bridge_agent.selection import materialize_group
        group=[{'id':f'D{i}','source':f'B{i}','part':'箱梁','kind':'裂缝','description':f'位置{i}裂缝W=0.1mm','table_context':'表1 左幅主桥箱梁内'} for i in range(1,5)]
        spec={'part':'左幅主桥箱梁','kind':'裂缝','summary_source':'B9','summary_quote':'存在4条裂缝','example_ids':['D1','D2']}
        got=materialize_group(spec,group,{'B9':{'text':'左幅主桥存在4条裂缝'}})
        self.assertEqual(got['member_count'],4);self.assertEqual(got['member_ids'],['D1','D2','D3','D4']);self.assertIn('B4',got['sources']);self.assertIn('存在4条裂缝',got['description']);self.assertIn('左幅',got['part']);self.assertIn('主桥',got['part'])
        spec['part']='右幅主桥箱梁'
        with self.assertRaises(ValueError):materialize_group(spec,group,{'B9':{'text':'左幅主桥存在4条裂缝'}})
        spec['part']='箱梁'
        spec['summary_quote']='存在5条裂缝'
        with self.assertRaises(ValueError):materialize_group(spec,group,{'B9':{'text':'左幅主桥存在4条裂缝'}})
    def test_small_structured_ledger_survives_model_omission(self):
        from bridge_agent.extraction import structured_candidates
        b={'id':'B000001','kind':'row','row':2,'table':1,'section':'检测','table_context':'左幅主桥','header':['病害位置','病害类型','病害描述'],'cells':['箱梁','裂缝','L=1m,W=0.1mm'],'text':'箱梁|裂缝|L=1m,W=0.1mm'}
        doc={'filename':'x.docx','tables':[[b['header'],b['cells']]],'blocks':[b]}
        def no_diseases(*args):return {k:[] for k in FINDINGS['properties']}
        with tempfile.TemporaryDirectory() as td,patch('bridge_agent.extraction.extract_batch',side_effect=no_diseases):
            got=extract_all(None,doc,Path(td));self.assertEqual(len(got['diseases']),1);self.assertEqual(got['diseases'][0]['table_context'],'左幅主桥')
    def test_main_bridge_overall_score_is_not_superstructure_score(self):
        from bridge_agent.extraction import validate_metric_scope
        with self.assertRaises(ValueError):validate_metric_scope('上部结构评分','该桥主桥总体技术状况评定为83.2，等级为二类')
        validate_metric_scope('总体评分','该桥主桥总体技术状况评定为83.2，等级为二类')
        validate_metric_scope('上部结构评分','该桥主桥上部结构评分为83.2')
    def test_unknown_history_can_omit_empty_quote_without_crashing(self):
        from bridge_agent.history import match_history
        class FakeModel:
            def call(self,*args,**kwargs):
                return {'tool_calls':[{'function':{'arguments':json.dumps({'matches':[{'id':'D1','new':'无法判断','previous':'无','development':'无','sources':[]}]})}}]}
        doc={'blocks':[{'id':'B1','section':'历年对比','kind':'paragraph','text':'历史未提供详细位置'}]};findings={'history_notes':[],'diseases':[{'id':'D1','source':'B2','part':'1#墩','kind':'裂缝','description':'裂缝'}]}
        with tempfile.TemporaryDirectory() as td:
            got=match_history(FakeModel(),doc,findings,Path(td));self.assertEqual(got['matched'],0);self.assertEqual(findings['diseases'][0]['history_quote'],'')
    def test_rating_table_does_not_duplicate_detailed_diseases(self):
        bs=[{'id':'B000001','kind':'row','row':2,'table':1,'section':'检测','header':['病害位置','病害类型','病害描述'],'cells':['桥墩','涂层剥落','墩身涂层剥落'],'text':'墩身涂层剥落'}, {'id':'B000002','kind':'row','row':2,'table':2,'section':'养护建议','header':['部位','损坏类型','扣分值'],'cells':['墩身','涂层剥落','12'],'text':'墩身|涂层剥落|12'}]
        doc={'filename':'x.docx','tables':[[bs[0]['header'],bs[0]['cells']],[bs[1]['header'],bs[1]['cells']]],'blocks':bs}
        def fake(*args):
            data={k:[] for k in FINDINGS['properties']};data['diseases']=[{'source':'B000002','part':'墩身','kind':'涂层剥落','description':'涂层剥落','history_sources':[]}];return data
        with tempfile.TemporaryDirectory() as td,patch('bridge_agent.extraction.extract_batch',side_effect=fake):
            got=extract_all(None,doc,Path(td));self.assertEqual(len(got['diseases']),1);self.assertEqual(got['diseases'][0]['source'],'B000001')
    def test_caption_scope_persists_across_chunk_boundaries(self):
        from bridge_agent.extraction import pack_blocks
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'scope.docx';d=Document();d.add_paragraph('表6.1-2 左幅主桥箱梁外检查结果');t=d.add_table(rows=2,cols=2);t.cell(0,0).text='位置';t.cell(0,1).text='病害';t.cell(1,0).text='第9跨';t.cell(1,1).text='露筋';d.save(p)
            doc=parse(p,root/'cache');last=doc['blocks'][-1];self.assertIn('左幅主桥',last['table_context']);self.assertIn('左幅主桥',json.dumps(pack_blocks([last]),ensure_ascii=False))
    def test_main_bridge_overview_preserves_approach_facts_and_precision(self):
        basic={k:{'value':'无','sources':[]} for k in FIELDS[:12]};basic['总体评分']['value']='主桥70.80；引桥74.00';basic['上一次总体评分']['value']='主桥74；引桥68.6';basic['总体等级']['value']='主桥二类；引桥二类'
        report={'basic':basic,'has_history':True,'recommendation_ids':[],'trend':{'text':'趋势'},'overall':{'text':'结论'},'risks':{'text':'风险'},'conclusion':[],'causes':[],'safety':[]};findings={'recommendations':[],'diseases':[]}
        result=materialize(report,findings);self.assertEqual(result['summary']['总体评分'],'70.80');self.assertEqual(result['summary']['上一次总体评分'],'74.00');self.assertEqual(result['summary']['总体等级'],'二类');self.assertIn('引桥',basic['总体评分']['value'])
        basic['上一次总体评分']['value']='主桥74.234；引桥68.6';self.assertEqual(materialize(report,findings)['summary']['上一次总体评分'],'74.234')
    def test_upstream_failure_splits_only_the_failed_source_batch(self):
        def fake_extract(model,blocks,path,identity):
            if len(blocks)>1:raise RuntimeError('Model HTTP 502: upstream failure')
            return {k:[] for k in FINDINGS['properties']}
        doc={'filename':'x.docx','tables':[],'blocks':[{'id':'B000001','text':'第一段','section':''},{'id':'B000002','text':'第二段','section':''}]}
        with tempfile.TemporaryDirectory() as td,patch('bridge_agent.extraction.extract_batch',side_effect=fake_extract) as mocked:
            result=extract_all(None,doc,Path(td));self.assertEqual(result['coverage']['processed_blocks'],2);self.assertEqual(mocked.call_count,3);self.assertEqual(json.loads((Path(td)/'0000.json').read_text())['split_depth'],1)
    def test_shared_response_requires_identical_request_and_service(self):
        class Response:
            status_code=200
            def json(self):return {'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','tool_calls':[{'id':'call1','type':'function','function':{'name':'done','arguments':'{}'}}]}}],'usage':{'prompt_tokens':5,'completion_tokens':2}}
        class Client:
            def __init__(self,forbidden=False):self.forbidden=forbidden
            def post(self,*a,**k):
                if self.forbidden:raise AssertionError('Unexpected network call')
                return Response()
        with tempfile.TemporaryDirectory() as td,patch.dict('os.environ',{'INSPECTION_FRESH_RUN':'0'}):
            root=Path(td);env=root/'.env';env.write_text('INSPECTION_API_BASE_URL=https://model.invalid/v1\nINSPECTION_API_KEY=dummy\nINSPECTION_MODEL=test-model\n')
            a=Model(root,root/'work/first');a.client.close();a.client=Client();messages=[{'role':'user','content':'source A'}];result=a.call(messages,[])
            b=Model(root,root/'work/second');b.client.close();b.client=Client(True);self.assertEqual(b.call(messages,[]),result);self.assertTrue(b.records[-1]['cache'])
            env.write_text(env.read_text().replace('model.invalid','other.invalid'));c=Model(root,root/'work/third');c.client.close();c.client=Client();c.call(messages,[]);self.assertFalse(c.records[-1]['cache'])
    def test_nested_table_is_not_flattened_into_parent_row(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'nested.docx';d=Document();outer=d.add_table(rows=1,cols=1);cell=outer.cell(0,0);cell.text='前文';inner=cell.add_table(rows=2,cols=2);inner.cell(0,0).text='部位';inner.cell(0,1).text='病害描述';inner.cell(1,0).text='3#墩';inner.cell(1,1).text='裂缝W=0.12mm';cell.add_paragraph('后文');d.save(p)
            parsed=parse(p,root/'cache');texts=[b['text'] for b in parsed['blocks']]
            self.assertEqual(parsed['table_count'],2);self.assertEqual(sum('0.12mm' in t for t in texts),1);self.assertLess(texts.index('前文'),next(i for i,t in enumerate(texts) if '0.12mm' in t));self.assertEqual(texts[-1],'后文')
    def test_date_and_consensus_normalization(self):
        self.assertEqual(normalize_date('二〇一八年十二月三十一日'),'2018年12月31日');self.assertEqual(normalize_date('2014/7/25'),'2014年7月25日')
        rows=[{'field':'总体评分','value':v,'source':s,'quote':'原文'} for v,s in [('70.80','B1'),('70.8','B2'),('74','B3'),('70.8','B1')]]
        got=fact_candidates(rows);self.assertEqual(len(got),2);self.assertEqual(got[0]['sources'],['B1','B2'])
    def test_parser_order_table_and_unicode(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'fake.doc';d=Document();d.add_paragraph('1、桥梁概况');t=d.add_table(rows=2,cols=2);t.cell(0,0).text='位置';t.cell(0,1).text='描述';t.cell(1,0).text='3#墩';t.cell(1,1).text='裂缝W=0.12mm';d.add_paragraph('结束');d.save(p)
            parsed=parse(p,root/'cache');self.assertEqual(parsed['format'],'docx');self.assertEqual([b['kind'] for b in parsed['blocks']],['paragraph','row','row','paragraph']);self.assertIn('0.12mm',search(parsed['blocks'],'裂缝')[0]['text']);self.assertEqual(parsed['blocks'][2]['cells'],['3#墩','裂缝W=0.12mm'])
            again=parse(p,root/'cache');self.assertEqual(parsed['sha256'],again['sha256'])
            d.add_paragraph('新增信息');d.save(p);self.assertNotEqual(parsed['sha256'],parse(p,root/'cache')['sha256'])
    def test_source_columns_and_fabricated_quote(self):
        b={'id':'B000001','kind':'row','cells':['3#墩','裂缝W=0.12mm'],'text':'3#墩 | 裂缝W=0.12mm','section':''}
        self.assertEqual(source_text({'source':b['id'],'cols':[1]}, {b['id']:b}),'裂缝W=0.12mm')
        with self.assertRaises(ValueError):source_text({'source':b['id'],'cols':[],'quote':'裂缝W=1.2mm'}, {b['id']:b})
        with self.assertRaises(ValueError):source_text({'source':b['id'],'cols':[5]}, {b['id']:b})
    def test_numeric_fact_and_quote_restore(self):
        data={k:[] for k in ['facts','diseases','recommendations','causes','safety','history_notes','no_disease_sources','excluded_diseases']};b={'id':'B1','text':'整体BCI=89.96，为B级','section':''}
        data['facts']=[{'field':'总体评分','value':'89.96','source':'B1','quote':'改写的句子'}]
        got=validate_findings(data,[b]);self.assertTrue(got['facts'][0]['quote_reconstructed']);self.assertIn(got['facts'][0]['quote'],b['text'])
        data['facts'][0].update(value='99.99',quote=b['text'])
        with self.assertRaises(ValueError):validate_findings(data,[b])
    def test_character_budget_full_coverage(self):
        bs=[{'id':f'B{i}','text':'裂缝'*100,'section':'章节'} for i in range(30)];gs=chunks(bs,1000)
        self.assertEqual([b['id'] for g in gs for b in g],[b['id'] for b in bs]);self.assertGreater(len(gs),1)
        with self.assertRaises(ValueError):chunks([{'id':'B1','text':'x'*10000,'section':''}],500)
    def test_html_label_without_header(self):
        d={'tables':[[['桥梁名称','某桥','全称'],['总体评分','90','评分']],[['序号','位置','病害种类','病害情况','是否新增','上一次定检状态','发展程度'],['1','3#墩','裂缝','W=0.1mm','否','无','无']]]}
        result=label_data(d);self.assertEqual(result['summary']['桥梁名称'],'某桥');self.assertEqual(len(result['diseases']),1)
    def test_docx_full_contract_readback(self):
        root=Path(__file__).resolve().parent.parent
        basic={k:{'value':'无','sources':[]} for k in FIELDS[:12]};basic['桥梁名称']['value']='测试桥'
        claim=lambda s:{'text':s,'sources':['B1'],'inferred':False}
        report={'basic':basic,'has_history':False,'trend':claim('未提供历史资料'),'overall':claim('裂缝需处置'),'risks':claim('3#墩裂缝'),'conclusion':[claim('经综合评定，无评分。'),claim('无历史资料。'),claim('目前存在裂缝。')],'recommendation_ids':['R1'],'causes':[claim('原文未说明')],'safety':[claim('可能影响耐久性')]}
        findings={'recommendations':[{'id':'R1','category':'尽快维修','description':'封闭裂缝','part':'3#墩'}],'diseases':[{'part':'3#墩','kind':'裂缝','description':'裂缝W=0.12mm','source':'B1'}]}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'out.docx';data=render(p,report,findings,root);doc=Document(p)
            self.assertEqual([len(t.columns) for t in doc.tables],[3,4,7]);self.assertEqual(len(doc.tables[0].rows),17);self.assertEqual(data['diseases'][0][-3:],['否','无','无']);self.assertIn('1条尽快维修',data['summary']['建议'])
if __name__=='__main__':unittest.main()
