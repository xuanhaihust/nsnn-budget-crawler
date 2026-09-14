"""Parse TT343 XML attachments into the 16-column long-format schema."""
import re, json, pathlib, unicodedata, xml.etree.ElementTree as ET

META = ('title','type','year','periodType','code','circular','department',
        'curencyunit','filename','header')

# The only spellings of "đồng" this pipeline accepts. An explicit allowlist, not a fuzzy
# match: stripping diacritics collapses đồng, dồng, đổng, dòng, đóng and động onto one string,
# and "dòng" means LINE. Reading a row count as a currency is exactly the kind of guess the
# data rules forbid, so an unlisted spelling gets no conversion and shows up in `./nsnn
# --units` instead. All three below are attested in the crawled corpus.
CURRENCY = {'đồng', 'dồng', 'đổng'}

# Scale words, including the abbreviations and misspellings actually observed. Every entry is
# either standard Vietnamese or attested in the corpus; nothing here is invented.
SCALE = {'đồng': 1, 'nghìn': 1_000, 'ngàn': 1_000,
         'triệu': 1_000_000, 'tiệu': 1_000_000, 'tr': 1_000_000,   # tiệu, tr: attested
         'tỷ': 1_000_000_000, 'tỉ': 1_000_000_000}
UNIT_VND = {'đồng': 1, 'nghìn đồng': 1_000, 'triệu đồng': 1_000_000, 'tỷ đồng': 1_000_000_000}
POW10 = {1, 1_000, 1_000_000, 1_000_000_000}

# One spelling per factor, so the ĐVT column reads the same everywhere. The source spelling is
# never lost: when it differs, build.py keeps it in Ghi chú.
CANON = {1: 'đồng', 1_000: 'nghìn đồng',
         1_000_000: 'triệu đồng', 1_000_000_000: 'tỷ đồng'}


def clean_unit(u):
    """Trim decoration and normalise to NFC.

    Some sources write Vietnamese decomposed (NFD): "ê" as e + U+0323, "ồ" as ô + U+0300.
    Such a string looks identical but never equals the NFC literals in SCALE, so units like
    "Triệu đồng" silently resolved to no factor at all and lost their VND conversion.
    A trailing ")" is only dropped when it is not closing a "(" that is still open, so
    "% (phần trăm)" does not decay into "% (phần trăm".
    """
    u = unicodedata.normalize('NFC', (u or '').strip())
    u = re.sub(r'^[\s\[]+', '', u)
    u = re.sub(r'[\s.:;,/\]]+$', '', u)
    while u.endswith(')') and u.count('(') < u.count(')'):
        u = re.sub(r'[\s.:;,/\])]+$', '', u[:-1] + ' ').strip()
    return re.sub(r'\s+', ' ', u).strip()


def unit_factor(u):
    """VND multiplier for a unit string, or None when it is unknown or ambiguous.

    Matched by WORD, not by suffix. A suffix test treats every string ending in "đồng" as
    plain đồng, so "Tiệu đồng" (a misspelling of "Triệu đồng") silently resolved to factor 1
    and made those values a million times too small - the exact failure CLAUDE.md records as
    having shipped once. The last word must be a listed spelling of đồng, and the word before
    it decides the scale:

      "đồng"            -> 1            "triệu đồng"     -> 1_000_000
      "nghìn đồng"      -> 1_000        "1.000 đồng"     -> 1_000   (a stated multiplier)
      "tỷ đồng"         -> 1e9          "1.000.000 đồng" -> 1_000_000
      "Tr đồng"         -> 1_000_000    "Triệu dồng"     -> 1_000_000
      "Tiệu đồng"       -> 1_000_000    "Triệu đổng"     -> 1_000_000
      "dòng"            -> None         "UBND tỉnh Cao Bằng" -> None

    Those six currency spellings were 660 rows that carried a real figure and no conversion
    until they were listed here. Anything still unlisted returns None, so Quy đổi VND is left
    empty rather than guessed - and `./nsnn --units` lists what was left out. A string naming
    both a percentage and a currency is ambiguous for the same reason and also returns None.
    """
    u = clean_unit(u).lower()
    if '%' in u:
        return None
    words = u.split()
    if not words or words[-1] not in CURRENCY:
        return None
    if len(words) == 1:
        return 1
    scale = words[-2]
    if scale in SCALE:
        return SCALE[scale] if scale not in CURRENCY else 1
    digits = scale.replace('.', '').replace(',', '')
    if digits.isdigit() and int(digits) in POW10:
        return int(digits)
    return None


# Percentages arrive written four ways. None of them converts, so unifying them changes no
# figure - but leaving them split means a query that groups by unit still sees one unit as
# four. Only forms that unambiguously mean "percent" are listed; a string merely containing a
# % sign is not enough, because "% so với dự toán" names a comparison, not a unit.
PERCENT = {'%', '% (phần trăm)', '%(phần trăm)', 'phần trăm (%)', 'phần trăm', '(%)',
           '% (phan tram)', 'percent', '%.'}


def canon_unit(u):
    """One spelling per unit, so the ĐVT column is consistent across all 34 provinces.

    A known currency becomes its canonical form ("Tr đồng", "1.000.000 đồng" and "Triệu dồng"
    all become "triệu đồng"). Everything else - percentages, counts, blanks, and any currency
    spelling not listed - is returned cleaned but otherwise untouched, because rewriting a
    string we could not classify would be a guess.
    """
    u = clean_unit(u)
    f = unit_factor(u)
    if f is not None:
        return CANON[f]
    return '%' if u.lower() in PERCENT else u


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

    src_unit = clean_unit(g('curencyunit') or (rec.get('CurrencyUnit') or ''))
    unit = canon_unit(src_unit)            # one spelling per unit across all 34 provinces
    factor = unit_factor(unit)
    unit_note = f"; ĐVT nguồn: {src_unit}" if src_unit != unit else ''
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
                'Ghi chú': f"{g('circular') or rec.get('ReportCircular_Name','')}; XML TT343; "
                           f"report {rec.get('ID')}{unit_note}",
            }
