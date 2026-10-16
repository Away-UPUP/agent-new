import json,tempfile,unittest
from pathlib import Path
from docx import Document
from bridge_agent.documents import parse,search,save_json
from bridge_agent.extraction import validate_findings,chunks,source_text
from bridge_agent.audit import label_data,FIELDS
from bridge_agent.render import render

class ContractTests(unittest.TestCase):
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
        data={k:[] for k in ['facts','diseases','recommendations','causes','safety','history_notes','no_disease_sources']};b={'id':'B1','text':'整体BCI=89.96，为B级','section':''}
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
