"""Parse TT343 xls/xlsx attachments (they are xlsx zips despite the .xls name)."""
import re, zipfile, pathlib, codecs, xml.etree.ElementTree as ET

_u16 = codecs.utf_16_le_decode
def _tolerant(b, errors='strict', final=True):
    try: return _u16(b, errors, final)
    except UnicodeDecodeError: return _u16(b, 'replace', final)
codecs.utf_16_le_decode = _tolerant
from parse_xml import norm_num, unit_factor, clean_unit

N = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'

ZIP_MAGIC = b'PK\x03\x04'

def is_xlsx_zip(path: pathlib.Path):
    """Sniff the magic bytes, do not use zipfile.is_zipfile.

    is_zipfile scans the whole file for an end-of-central-directory record, so a real
    BIFF .xls that embeds an OOXML drawing part (drs/connectorxml.xml and friends) is
    reported as a zip. The xlsx branch then finds no xl/worksheets/sheet*.xml, yields
    no sheets, and the report is silently dropped with no exception to record. Seen on
    TT343 forms B41/B45 across several provinces and years.
    """
    with open(path, 'rb') as fh:
        return fh.read(4) == ZIP_MAGIC

def sheet_rows(path: pathlib.Path):
    if not is_xlsx_zip(path):                 # true legacy BIFF .xls
        import xlrd
        bk = xlrd.open_workbook(str(path), formatting_info=False)
        for sh in bk.sheets():
            grid = [[('' if c is None else str(c).strip()) for c in sh.row_values(i)]
                    for i in range(sh.nrows)]
            yield sh.name, grid
        return
    z = zipfile.ZipFile(path)
    shared = []
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall(N + 'si'):
            shared.append(''.join(t.text or '' for t in si.iter(N + 't')))
    names = [n for n in z.namelist() if re.match(r'xl/worksheets/sheet\d+\.xml$', n)]
    for nm in sorted(names):
        grid = []
        for row in ET.fromstring(z.read(nm)).iter(N + 'row'):
            cells = {}
            for c in row:
                ref = c.get('r') or ''
                col = re.match(r'([A-Z]+)', ref)
                idx = 0
                for ch in (col.group(1) if col else 'A'):
                    idx = idx * 26 + (ord(ch) - 64)
                v = c.find(N + 'v'); s = ''
                if v is not None:
                    s = shared[int(v.text)] if c.get('t') == 's' else (v.text or '')
                elif c.find(N + 'is') is not None:
                    s = ''.join(t.text or '' for t in c.iter(N + 't'))
                cells[idx - 1] = s.strip()
            width = max(cells) + 1 if cells else 0
            grid.append([cells.get(i, '') for i in range(width)])
        yield nm, grid

RE_UNIT = re.compile(r'Đơn vị(?:\s*tính)?\s*[::]\s*(.+)', re.I)
RE_FORM = re.compile(r'Biểu\s*số\s*([0-9A-Za-z/\-]+)', re.I)
RE_QD   = re.compile(r'(Quyết định|Nghị quyết)\s*số[::]?\s*([^\s,;)]+)', re.I)
HDR_KEY = ('stt', 'nội dung', 'noi dung', 'chỉ tiêu', 'chi tieu')
# a header cell is an index column (STT/TT/Số TT) or a label column (Nội dung/Chỉ tiêu/Danh mục/Tên ...)
RE_IDX = re.compile(r'^(số\s*)?(stt|tt)\.?$', re.I)
RE_LBL = re.compile(r'(nội dung|noi dung|chỉ tiêu|chi tieu|danh mục|danh muc|tên\s|ten\s|đơn vị sử dụng|khoản mục)', re.I)

def is_header_row(r):
    cells = [(c or '').strip() for c in r]
    if sum(1 for c in cells if c) < 3: return False
    return any(RE_IDX.match(c) for c in cells[:3]) or any(RE_LBL.search(c) for c in cells[:4])
RULER   = re.compile(r'^[A-Za-z]$|^\(?\d{1,2}\)?$')

def rows_from(path, rec, province):
    src = next((a['Url'] for a in rec.get('Attachments') or []
                if a['FileName'].lower().endswith(('.xls', '.xlsx'))), '')
    for _, grid in sheet_rows(path):
        flat = [' '.join(r) for r in grid]
        unit = form = qd = ''
        for line in flat[:14] + flat[14:60]:
            if not unit and (m := RE_UNIT.search(line)): unit = m.group(1).strip()
            if not form and (m := RE_FORM.search(line)): form = m.group(1).strip()
            if not qd   and (m := RE_QD.search(line)):   qd = m.group(2).strip()
        hdr_i = next((i for i, r in enumerate(grid[:20]) if is_header_row(r)), None)
        if hdr_i is None: continue
        # data starts at the first row with a label AND a number
        def is_data(r):
            return len(r) > 1 and (r[1] or '').strip() and any(
                norm_num(c) is not None for c in r[2:])
        data_i = next((i for i in range(hdr_i + 1, min(hdr_i + 8, len(grid)))
                       if is_data(grid[i])), hdr_i + 1)
        width = max((len(r) for r in grid[hdr_i:data_i] or [[]]), default=0)
        parts = []
        for r in grid[hdr_i:data_i]:
            row, last = [], ''
            for i in range(width):                 # merged cells: fill right
                c = (r[i] if i < len(r) else '').strip()
                if c: last = c
                row.append(last)
            parts.append(row)
        header = [' / '.join(dict.fromkeys(x for x in col if x)).strip()
                  for col in zip(*parts)] if parts else []
        unit = clean_unit(unit)
        factor = unit_factor(unit)
        for r in grid[data_i:]:
            if len(r) < 2: continue
            stt, label = (r[0] or '').strip(), (r[1] or '').strip()
            if not label or label.lower() in HDR_KEY or RE_LBL.search(label) and len(label) < 18: continue
            if RULER.match(label) and RULER.match(stt or 'A'): continue
            for ci in range(2, len(r)):
                raw = (r[ci] or '').strip()
                col = (header[ci] if ci < len(header) else '').strip() or f'col{ci+1}'
                if not raw:
                    std = vnd = ''
                else:
                    n = norm_num(raw)
                    std = '' if n is None else n
                    vnd = n * factor if (n is not None and factor) else ''
                if raw == '' : continue          # spreadsheets are sparse: skip empty cells
                yield {
                    'Tỉnh/TP hiện hành': province,
                    'Tỉnh/TP theo nguồn': rec.get('Deparment_Name', ''),
                    'Số QĐ/Văn bản': qd or rec.get('ReportFileName', ''),
                    'Ngày tài liệu': rec.get('ApprovedDateText') or rec.get('PublishDateText', ''),
                    'Kỳ dữ liệu': f"{rec.get('PeriodType_Name','')} {rec.get('YearTextMonthOrPeriod','')}".strip(),
                    'Cơ quan': rec.get('Deparment_Name', ''),
                    'Phạm vi': rec.get('ReportType_Name', ''),
                    'Nội dung/Bảng': f"{rec.get('Title','')} [{form or rec.get('ReportFileName','')}]",
                    'Chỉ tiêu': f"{stt} {label}".strip(),
                    'Loại số liệu': col,
                    'Giá trị gốc': raw,
                    'Giá trị chuẩn hóa': std,
                    'ĐVT': unit,
                    'Quy đổi VND': vnd,
                    'Nguồn': src,
                    'Ghi chú': f"{rec.get('ReportCircular_Name','')}; XLSX TT343; report {rec.get('ID')}",
                }
