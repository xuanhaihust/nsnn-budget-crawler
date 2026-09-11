"""Parse TT343 XML attachments into the 16-column long-format schema."""
import re, json, pathlib, xml.etree.ElementTree as ET

META = ('title','type','year','periodType','code','circular','department',
        'curencyunit','filename','header')

UNIT_VND = {'đồng':1, 'nghìn đồng':1_000, 'triệu đồng':1_000_000, 'tỷ đồng':1_000_000_000}

def clean_unit(u):
    u = re.sub(r'[\s.:;,]+$', '', (u or '').strip())
    u = re.sub(r'\s+', ' ', u)
    return u

def unit_factor(u):
    """Longest unit name wins: 'triệu đồng' must not match the 'đồng' suffix."""
    u = clean_unit(u).lower()
    for k in sorted(UNIT_VND, key=len, reverse=True):
        if u == k or u.startswith(k + ' ') or u.endswith(' ' + k): return UNIT_VND[k]
    return None

NUM = re.compile(r'^-?[\d., ]+$')
def norm_num(raw):
    """Return float or None. Never guess: blanks and '-' stay unconverted."""
    s = (raw or '').strip()
    if s in ('', '-', '–', '—'): return None
    if not NUM.match(s): return None
    s = s.replace(' ', '')
    if ',' in s and '.' in s:                 # 1.234.567,89  -> vi grouping
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:
        s = s.replace(',', '.') if s.count(',') == 1 and len(s.split(',')[-1]) <= 3 and len(s.split(',')[0]) <= 3 else s.replace(',', '')
    try: return float(s)
    except ValueError: return None

def load(path: pathlib.Path):
    raw = path.read_bytes()
    for enc in ('utf-8', 'utf-16', 'latin-1'):
        try: txt = raw.decode(enc); break
        except UnicodeDecodeError: continue
    txt = re.sub(r'encoding="[^"]+"', 'encoding="utf-8"', txt, count=1)
    return ET.fromstring(txt)

def rows_from(path, rec, province):
    """rec = matching catalog record. Yields 16-column dicts."""
    r = load(path)
    g = lambda t: (r.findtext(t) or '').strip()
    header = {c.tag: (c.text or '').strip() for c in (r.find('header') or [])}
    content = r.find('content')
    if content is None: return

    unit = clean_unit(g('curencyunit') or (rec.get('CurrencyUnit') or ''))
    factor = unit_factor(unit)
    src = next((a['Url'] for a in rec.get('Attachments') or []
                if a['FileName'].lower().endswith('.xml')), '')
    form_code = g('code') or (rec.get('ReportFileName') or '')
    label_cols = [k for k in ('f1','f2') if k in header]
    value_cols = [k for k in header if k not in label_cols]

    for row in content:
        cells = {c.tag: (c.text or '').strip() for c in row}
        stt = cells.get('f1', '')
        chi_tieu = cells.get('f2', '')
        if not chi_tieu: continue
        for vc in value_cols:
            raw = cells.get(vc, '')
            if raw == '':                      # blank stays blank, never 0
                std, vnd = '', ''
            else:
                n = norm_num(raw)
                std = '' if n is None else n
                vnd = n * factor if (n is not None and factor) else ''
            yield {
                'Tỉnh/TP hiện hành': province,
                'Tỉnh/TP theo nguồn': g('department') or rec.get('Deparment_Name',''),
                'Số QĐ/Văn bản': rec.get('ReportFileName',''),
                'Ngày tài liệu': rec.get('ApprovedDateText') or rec.get('PublishDateText',''),
                'Kỳ dữ liệu': f"{g('periodType') or rec.get('PeriodType_Name','')} {g('year') or rec.get('YearTextMonthOrPeriod','')}".strip(),
                'Cơ quan': rec.get('Deparment_Name',''),
                'Phạm vi': rec.get('ReportType_Name',''),
                'Nội dung/Bảng': f"{g('title') or rec.get('Title','')} [{form_code}]",
                'Chỉ tiêu': f"{stt} {chi_tieu}".strip(),
                'Loại số liệu': header.get(vc, vc),
                'Giá trị gốc': raw,
                'Giá trị chuẩn hóa': std,
                'ĐVT': unit,
                'Quy đổi VND': vnd,
                'Nguồn': src,
                'Ghi chú': f"{g('circular') or rec.get('ReportCircular_Name','')}; XML TT343; report {rec.get('ID')}",
            }
