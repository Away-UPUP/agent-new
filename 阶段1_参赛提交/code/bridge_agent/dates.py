"""Date evidence policy learned from the supplied report/label pairs."""
import re
from datetime import date


def normalize_date(value):
    digits = dict(zip('零〇○一二三四五六七八九', '000123456789'))
    def number(s):
        if s.isdigit():
            return str(int(s))
        if '十' in s:
            a, b = s.split('十', 1)
            return str((int(digits.get(a, '1')) if a else 1)*10 + (int(digits[b]) if b else 0))
        return ''.join(digits.get(c, c) for c in s)
    pattern = r'([\d零〇○一二三四五六七八九]{4})[年./-]([\d一二三四五六七八九十]{1,3})(?:[月./-]([\d一二三四五六七八九十]{1,3})日?)?月?'
    def replace(m):
        return number(m[1])+'年'+number(m[2])+'月'+(number(m[3])+'日' if m[3] else '')
    return re.sub(pattern, replace, value)


def date_end(text):
    """Resolve an exact date or range endpoint, inheriting omitted year/month."""
    text = normalize_date(re.sub(r'\s+', '', text))
    full = list(re.finditer(r'(\d{4})年(\d{1,2})月(?:(\d{1,2})日)?', text))
    if not full:
        return ''
    match = full[0]
    year, month = int(match[1]), int(match[2])
    day = int(match[3]) if match[3] else None
    tail = text[match.end():]
    if re.match(r'^[～~至—–-]+\d{4}年', tail):
        return date_end(re.sub(r'^[～~至—–-]+', '', tail))
    short = re.match(r'^[～~至—–-]+(?:(\d{1,2})[月./-])?(\d{1,2})日?', tail)
    if short:
        end_month = int(short[1]) if short[1] else month
        if end_month < month:
            year += 1
        month, day = end_month, int(short[2])
    date(year, month, day or 1)
    return f'{year}年{month}月'+(f'{day}日' if day else '')


DATE_RULE = '优先明确报告日期/批准日期，其次封面落款，再次发出/签发/编制日期；均缺失才按训练标签取本次检验/检测日期区间最后一天。旧版报告封面年月优先于内页发出日期，不补造日。省略的结束年份/月从区间起点继承；不能采用历年检测或标准发布日期。'


def date_policy(blocks):
    candidates = {'report': [], 'cover': [], 'issued': [], 'inspection_end': []}
    for i, block in enumerate(blocks):
        text = re.sub(r'\s+', '', block['text'])
        marker = re.search(r'(?:报告(?:批准|签发|出具|发出|编制)?日期|签发日期|出具日期)[：:（(]*', text)
        basis = ('issued' if re.search(r'发出|签发|出具|编制',marker[0]) else 'report') if marker else None
        quote = text[marker.end():] if marker else text
        if not basis and i < 25 and len(text) < 45 and re.fullmatch(r'[（(]?(?:\d{4}|[二两][〇○零一二三四五六七八九]{3})[年./-].*?[）)]?', text):
            basis = 'cover'
        if not basis and not re.search(r'历史|历年|上次|上一次|往年', block.get('section', '')):
            marker = re.search(r'(?:检[验测查](?:日期|时间)|检验起止日期)[：:（(]*', text)
            if marker and not re.search(r'历年|上次|上一次|往年', text[:marker.start()]):
                basis, quote = 'inspection_end', text[marker.end():]
        if not basis:
            continue
        # Limit to this date cell/clause; unrelated later dates are not evidence.
        start = re.search(r'(?:\d{4}|[二两][〇○零一二三四五六七八九]{3})[年./-]', quote)
        if not start:
            continue
        quote = re.split(r'[|；;。]', quote[start.start():], maxsplit=1)[0]
        try:
            value = date_end(quote)
        except ValueError:
            continue
        if value:
            candidates[basis].append({'source': block['id'], 'value': value, 'basis': basis})
    for basis in ['report', 'cover', 'issued', 'inspection_end']:
        if candidates[basis]:
            return {'rule': DATE_RULE, 'basis': basis, 'candidates': candidates[basis]}
    return {'rule': DATE_RULE, 'basis': 'unknown', 'candidates': []}


def validate_date(value, sources, policy):
    expected = policy['candidates']
    if not expected:
        if policy['basis'] != 'unknown':
            raise ValueError('No eligible report-date source in this batch; omit the date here. The date is handled in its original source batch.')
        return
    supported = [c for c in expected if c['source'] in sources and c['value'] == date_end(value)]
    if not supported:
        raise ValueError('Report date must follow document date policy: '+str(policy))
