from __future__ import annotations
import hashlib, json, re, subprocess, tempfile, time, shutil, os, signal
from pathlib import Path
from zipfile import ZipFile
from lxml import etree, html

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
W = '{'+NS['w']+'}'
PARSER_VERSION = 5

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()

def save_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)

def read_json(path): return json.loads(Path(path).read_text(encoding='utf-8'))

def sniff(path):
    with Path(path).open('rb') as f: head = f.read(512)
    if head.startswith(b'PK'): return 'docx'
    if head.startswith(b'\xd0\xcf\x11\xe0'): return 'doc'
    if b'html' in head.lower() or b'<table' in head.lower() or b'<!doctype' in head.lower(): return 'html'
    raise ValueError('Unsupported document content: '+Path(path).name)

def converted(path, cache):
    path=Path(path); cache=Path(cache); target=cache/digest(path)/'converted.docx'
    if target.exists(): return target
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bridge-lo-') as td:
        td=Path(td); incoming=td/'input.doc'; incoming.write_bytes(path.read_bytes())
        p=subprocess.Popen(['libreoffice','-env:UserInstallation='+ (td/'profile').as_uri(), '--headless','--convert-to','docx','--outdir',str(td),str(incoming)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        try:p.communicate(timeout=480)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid,signal.SIGKILL);p.communicate()
            raise RuntimeError('LibreOffice DOC conversion exceeded 480 seconds: '+path.name)
        result=td/'input.docx'
        if p.returncode or not result.exists(): raise RuntimeError('LibreOffice DOC conversion failed: '+path.name)
        staging=target.with_suffix('.tmp.docx');shutil.copyfile(result,staging);staging.replace(target)
    return target

def txt(el):
    return ''.join(('\n' if n.tag==W+'br' else '\t' if n.tag==W+'tab' else n.text or '') for n in el.iter() if n.tag in {W+'t',W+'br',W+'tab'}).strip()

def docbook(path):
    bundled=Path(__file__).resolve().parent.parent/'tools/antiword/usr'
    binary=shutil.which('antiword') or str(bundled/'bin/antiword')
    if not Path(binary).exists():return None
    env=dict(os.environ)
    if (bundled/'share/antiword').exists():env['ANTIWORDHOME']=str(bundled/'share/antiword')
    p=subprocess.run([binary,'-x','db',str(path)],capture_output=True,timeout=90,env=env)
    if p.returncode:
        p=subprocess.run([binary,'-t','-w','0','-m','UTF-8.txt',str(path)],capture_output=True,timeout=90,env=env)
        if p.returncode:raise RuntimeError('antiword text export failed')
        return p.stdout.decode('utf-8')
    return etree.fromstring(p.stdout,etree.XMLParser(resolve_entities=False,no_network=True))

def parse(path, cache):
    path=Path(path); sha=digest(path); cache=Path(cache); output=cache/sha/'parsed.json'
    if output.exists():
        data=read_json(output)
        if data.get('parser_version')==PARSER_VERSION:
            data['filename']=path.name
            return data
    start=time.monotonic(); fmt=sniff(path); blocks=[]; tables=[]; section=''; images=0; pages=None; paragraphs=0
    def add(text, **meta):
        if text.strip(): blocks.append({'id':f'B{len(blocks)+1:06d}', 'text':text.strip(), 'section':section, **meta})
    cached_conversion=cache/sha/'converted.docx'
    book=docbook(path) if fmt=='doc' and not cached_conversion.exists() else None
    if isinstance(book,str):
        images=None;pending=[]
        def flush_rows():
            if not pending:return
            ti=len(tables)+1;rows=[]
            for cells in pending:
                if rows and len(cells)==len(rows[-1]) and not any(cells[:-1]) and cells[-1]:rows[-1][-1]+=' '+cells[-1]
                else:rows.append(cells)
            tables.append(rows)
            for ri,cells in enumerate(rows,1):add(' | '.join(cells),kind='row',table=ti,row=ri,cells=cells,header=rows[0])
            pending.clear()
        for line in book.splitlines():
            s=line.strip()
            if s.startswith('|') and s.endswith('|'):pending.append([c.strip() for c in s.split('|')[1:-1]])
            else:
                flush_rows()
                if s and s!='[pic]':
                    paragraphs+=1
                    if len(s)<90 and re.match(r'^\d+(?:\.\d+)*\s',s):section=s
                    add(s,kind='paragraph',paragraph=paragraphs)
        flush_rows()
    elif book is not None:
        images=None
        def walk_book(nodes):
            nonlocal paragraphs,section
            for el in nodes:
                if el.tag=='informaltable':
                    ti=len(tables)+1;rows=[];tables.append(rows)
                    for ri,row in enumerate(el.findall('.//row'),1):
                        if next(row.iterancestors('informaltable'),None) is not el:continue
                        if row.findall('.//informaltable'):
                            for cell in row.findall('entry'):walk_book(list(cell))
                            continue
                        cells=[' '.join(''.join(c.itertext()).split()) for c in row.findall('entry')];rows.append(cells)
                        add(' | '.join(cells),kind='row',table=ti,row=ri,cells=cells,header=rows[0])
                elif el.tag in ['para','title']:
                    if el.findall('.//informaltable'):
                        buffer=el.text or ''
                        for child in el:
                            if child.tag=='informaltable':
                                add(' '.join(buffer.split()),kind='paragraph',paragraph=paragraphs);buffer='';walk_book([child])
                            else:buffer+=''.join(child.itertext())
                            buffer+=child.tail or ''
                        add(' '.join(buffer.split()),kind='paragraph',paragraph=paragraphs)
                        continue
                    s=' '.join(''.join(el.itertext()).split());paragraphs+=1
                    if el.tag=='title' and s:section=s
                    add(s,kind='paragraph',paragraph=paragraphs)
                elif el.tag!='bookinfo':walk_book(list(el))
        walk_book(book.findall('chapter'))
    elif fmt=='html':
        raw=path.read_bytes(); tree=html.fromstring(raw)
        for el in tree.xpath('//p[not(ancestor::table)] | //table'):
            if el.tag=='table':
                ti=len(tables)+1; rows=[]
                for ri,row in enumerate(el.xpath('.//tr'),1):
                    cells=[''.join(c.itertext()).strip() for c in row.xpath('./th|./td')];rows.append(cells)
                    add(' | '.join(cells),kind='row',table=ti,row=ri,cells=cells,header=rows[0])
                tables.append(rows)
            else:
                s=''.join(el.itertext()).strip(); paragraphs+=1
                if len(s)<70:section=s
                add(s,kind='paragraph',paragraph=paragraphs)
    else:
        actual=converted(path,cache) if fmt=='doc' else path
        with ZipFile(actual) as z:
            root=etree.fromstring(z.read('word/document.xml'))
            images=len([n for n in z.namelist() if n.startswith('word/media/')])
            if 'docProps/app.xml' in z.namelist():
                pr=etree.fromstring(z.read('docProps/app.xml')); v=pr.find('{*}Pages')
                if v is not None and (v.text or '').isdigit(): pages=int(v.text)
            body=root.find('w:body',NS)
            def walk(nodes,bi=0):
              nonlocal paragraphs,section
              for el in nodes:
                if el.tag==W+'p':
                    s=txt(el);paragraphs+=1
                    style=el.find('w:pPr/w:pStyle',NS)
                    if s and len(s)<90 and (re.match(r'^(?:第[一二三四五六七八九十\d]+[章节]|\d+(?:\.\d+){0,3}[、.\s]|[一二三四五六七八九十]+[、．])',s) or style is not None and 'heading' in style.get(W+'val','').lower()):section=s
                    add(s,kind='paragraph',paragraph=paragraphs,body_index=bi)
                elif el.tag==W+'tbl':
                    ti=len(tables)+1; rows=[];tables.append(rows);previous=[]
                    for ri,row in enumerate(el.findall('w:tr',NS),1):
                        if row.findall('.//w:tbl',NS):
                            for cell in row.findall('w:tc',NS):walk(list(cell),bi)
                            continue
                        cells=[]; spans=[]
                        for ci,c in enumerate(row.findall('w:tc',NS)):
                            s=txt(c);merge=c.find('w:tcPr/w:vMerge',NS)
                            if merge is not None and merge.get(W+'val')!='restart' and not s and ci<len(previous):s=previous[ci]
                            cells.append(s); span=c.find('w:tcPr/w:gridSpan',NS);spans.append(int(span.get(W+'val')) if span is not None else 1)
                        rows.append(cells);previous=cells
                        add(' | '.join(cells),kind='row',table=ti,row=ri,cells=cells,spans=spans,header=rows[0],body_index=bi)
                elif el.tag in [W+'sdt',W+'sdtContent',W+'customXml']:
                    walk(list(el),bi)
            for bi,el in enumerate(body):walk([el],bi)
            # Headers and text boxes may contain the actual report date.
            for name in z.namelist():
                if re.match(r'word/(header|footer)\d+\.xml$',name):
                    for el in etree.fromstring(z.read(name)).findall('.//w:p',NS):add(txt(el),kind='header_footer',part=name)
    # A caption carries the main/approach bridge and left/right scope across chunk boundaries.
    contexts={}
    for i,b in enumerate(blocks):
        if b.get('kind')=='row':
            tid=b['table']
            if tid not in contexts:
                before=[x['text'] for x in blocks[max(0,i-5):i] if x.get('kind')=='paragraph']
                captions=[t for t in before if re.match(r'^表\s*[\d一二三四五六七八九十]',t)]
                contexts[tid]=captions[-1] if captions else b['section']
            b['table_context']=contexts[tid]
    if not blocks: raise ValueError('No readable text; OCR required: '+path.name)
    data={'parser_version':PARSER_VERSION,'sha256':sha,'filename':path.name,'format':fmt,'backend':'antiword-text-tables' if isinstance(book,str) else 'antiword-docbook' if book is not None else 'html' if fmt=='html' else 'ooxml','pages_metadata':pages,'images':images,'paragraphs':paragraphs,'table_count':len(tables),'chars':sum(len(b['text']) for b in blocks),'blocks':blocks,'tables':tables,'parse_seconds':round(time.monotonic()-start,3),'locator':'paragraph/table/row in parsed export; page metadata is not a precise citation'}
    save_json(output,data);return data

def search(blocks, query, limit=12, max_chars=18000):
    terms=[t for t in re.split(r'[\s,，、;；]+',query) if t]
    ranked=sorted(enumerate(blocks), key=lambda x: -sum((3 if term in x[1]['section'] else 0)+x[1]['text'].count(term) for term in terms))
    selected=[];used=0
    for _,b in ranked:
        if not any(t in b['text'] or t in b['section'] for t in terms):continue
        if used+len(b['text'])>max_chars:continue
        selected.append(b);used+=len(b['text'])
        if len(selected)>=limit:break
    return sorted(selected,key=lambda b:b['id'])

def compact(block):
    out={'id':block['id'],'section':block['section']}
    if block.get('kind')=='row':out.update(cells=block['cells'],header=block['header'],table=block['table'],row=block['row'],table_context=block.get('table_context',''))
    else:out['text']=block['text']
    return out
