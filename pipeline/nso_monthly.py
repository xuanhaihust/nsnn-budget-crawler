#!/usr/bin/env python3
"""Monthly NSNN revenue/expenditure per province, from the statistics offices.

CKNS - the source the rest of this repo crawls - cannot answer a monthly question. Its own
`GetPeriodType` lists exactly four periods (Quí I, 6 tháng, 9 tháng, Năm) and none is a month,
and it carries local budgets only, so there is no national total in it either.

The statistics offices publish what CKNS does not. Each province has a site at
`thongke<tinh>.nso.gov.vn` with a monthly "Tình hình kinh tế - xã hội" report whose section
*4. Thu, chi ngân sách Nhà nước* gives the figure FOR THE MONTH and the year-to-date total:

    Tổng thu ngân sách Nhà nước tháng 8/2026 ước đạt 185,89 tỷ đồng; lũy kế tổng thu
    ngân sách Nhà nước 8 tháng năm 2026 đạt 1.370,39 tỷ đồng, giảm 10,72% ...

The same rules as the rest of the project apply, and they matter more here because the numbers
sit in prose rather than in a table:

  - Never infer a value. A sentence that does not parse cleanly goes to `pending` with its
    raw text; it never becomes a guess and never becomes 0.
  - A missing figure stays missing. Absent is not zero.
  - Never correct the source. An implausible published number is reported as published.
  - The unit is read from the sentence, never assumed - "tỷ đồng" is usual but not universal,
    and a figure whose unit cannot be read is left unconverted.
"""
import re, unicodedata, urllib.request, urllib.error, json, time, pathlib

UA = {'User-Agent': 'Mozilla/5.0'}
#: Four listings, because a month is NOT always filed under the monthly one: the quarter-end
#: months (3, 6, 9, 12) are filed under the quarterly or annual listing, so walking only
#: "...-thang/" silently loses a third of the year. Điện Biên 2025: months 3, 6, 9 and 12 were
#: all missing until these were added. "thong-tin-kinh-te-xa-hoi" is the union listing and
#: usually carries everything, but the others are walked too because a site can differ.
LIST_PATHS = ['/thong-tin-kinh-te-xa-hoi/',
              '/bao-cao-tinh-hinh-kinh-te-xa-hoi-thang/',
              '/bao-cao-tinh-hinh-kinh-te-xa-hoi-quy/',
              '/bao-cao-tinh-hinh-kinh-te-xa-hoi-nam/']
#: Article slugs. The prose report is "tinh-hinh-kinh-te-xa-hoi"; "mot-so-chi-tieu" is the
#: indicator table and "infographic" is a picture - neither carries the budget paragraph.
#: Any href whose slug contains "tinh-hinh-kinh-te-xa-hoi", under any directory. Deliberately
#: loose: the same CMS files the report under different slugs per province - Điện Biên uses
#: "tinh-hinh-kinh-te-xa-hoi-...", Cao Bằng "bao-cao-tinh-hinh-kinh-te-xa-hoi-..." - and
#: anchoring on the directory or on the start of the slug silently returned zero articles for
#: the second. "infographic" and "mot-so-chi-tieu" are excluded: a picture and a table of
#: indicators, neither carrying the budget paragraph.
ART = re.compile(r'href="(/[^"]*?tinh-hinh-kinh-te-xa-hoi[^"]*?\.html)"')
SKIP_SLUG = re.compile(r'infographic|mot-so-chi-tieu|/rss/', re.I)

#: Scale words, matched on the word before "đồng" exactly as pipeline/parse_xml does. A suffix
#: test would read "triệu đồng" as plain đồng; that bug has already been paid for once here.
#: COMPOUND scales come first and must be tried first. The national report writes
#: "2.023,8 nghìn tỷ đồng" - that is nghìn x tỷ = 1e12, not 1e9. Matching the last scale word
#: before "đồng" reads it as "tỷ đồng" and lands 1000x low, which is the same shape as the
#: "triệu đồng read as đồng" bug already paid for in parse_xml.
SCALE = {'nghìn tỷ': 1e12, 'ngàn tỷ': 1e12, 'nghìn tỉ': 1e12,
         'tỷ': 1e9, 'tỉ': 1e9, 'triệu': 1e6, 'nghìn': 1e3, 'ngàn': 1e3, 'đồng': 1.0}
_SC = '|'.join(sorted(SCALE, key=len, reverse=True))


def slug(name):
    s = name.replace('TP ', '').replace('Thành phố ', '')
    s = unicodedata.normalize('NFD', s.replace('đ', 'd').replace('Đ', 'D'))
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z]', '', s.lower())


def get(url, tries=3):
    last = None
    for a in range(tries):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45)
            return r.read().decode('utf-8', 'replace'), r.geturl()
        except Exception as e:
            last = e
            time.sleep(2 * (a + 1))
    raise last


def text_of(html_src):
    """Article HTML -> flat lines, tags and scripts removed."""
    import html as H
    t = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html_src, flags=re.S | re.I)
    t = re.sub(r'<[^>]+>', '\n', t)
    return [l.strip() for l in H.unescape(t).split('\n') if l.strip()]


def num(s):
    """Vietnamese number -> float. '1.370,39' is 1370.39; '.' groups, ',' is the decimal.

    Returns None rather than a guess when the string is not unambiguously a number, so the
    caller records it as pending instead of inventing a value.
    """
    s = s.strip()
    if not re.fullmatch(r'\d{1,3}(\.\d{3})*(,\d+)?|\d+(,\d+)?', s):
        return None
    return float(s.replace('.', '').replace(',', '.'))


def unit_factor(tail):
    """Read the scale from the words right after the number. None when it cannot be read.

    Longest scale first, so "nghìn tỷ đồng" resolves to 1e12 rather than to the "tỷ" inside it.
    """
    m = re.match(r'\s*(' + _SC + r')?\s*đồng', tail, re.I)
    if not m:
        return None, ''
    word = re.sub(r'\s+', ' ', (m.group(1) or 'đồng').lower())
    return SCALE.get(word), re.sub(r'\s+', ' ', m.group(0).strip())


#: Months are written as a WORD as often as a digit - "tháng Hai năm 2025", "tháng Sáu ước
#: đạt". The trap: "tháng Năm" is MAY, while "năm 2025" is the YEAR, and they differ only by
#: capitalisation, so this map is matched case-SENSITIVELY. Folding case here would read the
#: December report's "Tổng thu ... năm 2025" as a May figure.
MONTH_WORD = {'Một': 1, 'Hai': 2, 'Ba': 3, 'Tư': 4, 'Bốn': 4, 'Năm': 5, 'Sáu': 6, 'Bảy': 7,
              'Bẩy': 7, 'Tám': 8, 'Chín': 9, 'Mười': 10, 'Mười một': 11, 'Mười hai': 12}
#: Same words as ordinals in the cumulative clause: "lũy kế hai tháng đầu năm 2025".
CUM_WORD = {'một': 1, 'hai': 2, 'ba': 3, 'tư': 4, 'bốn': 4, 'năm': 5, 'sáu': 6, 'bảy': 7,
            'tám': 8, 'chín': 9, 'mười': 10, 'mười một': 11, 'mười hai': 12}
#: Two-word months are written with either capitalisation - "tháng Mười hai" and "tháng Mười
#: Hai" both occur - so the SECOND word is matched case-insensitively while the first stays
#: case-sensitive, which is what keeps "Năm" (May) apart from "năm" (year). Matching the whole
#: token case-sensitively made "Mười Hai" fall back to "Mười" and file December as month 10.
def _mw_alt(w):
    a, _, b = w.partition(' ')
    return a + r'\s+[' + b[0].upper() + b[0].lower() + ']' + b[1:] if b else a
_MW = '|'.join(_mw_alt(w) for w in sorted(MONTH_WORD, key=len, reverse=True))
_CW = '|'.join(sorted(CUM_WORD, key=len, reverse=True))

#: "tháng 8/2026", "tháng 8 năm 2026", "tháng Hai năm 2025", "tháng Sáu" (year omitted).
MONTH = r'tháng\s*(\d{1,2}|' + _MW + r')\s*(?:/\s*(\d{4})|năm\s*(\d{4}))?'
NUMU = r'([\d.,]+)\s*((?:' + _SC + r')?\s*đồng)'
RE_MONTH_VAL = re.compile(r'Tổng\s+(thu|chi)\s+ngân\s+sách[^.;]*?' + MONTH +
                          r'[^.;]*?đạt\s+' + NUMU)
#: "lũy kế 8 tháng", "lũy kế ... hai tháng đầu năm 2025"
RE_CUM_VAL = re.compile(r'[Ll]ũy\s*kế[^.;]*?(\d{1,2}|' + _CW + r')\s*tháng[^.;]*?đạt\s+' + NUMU)
#: The year, when the month phrase omits it, comes from elsewhere in the same sentence.
RE_YEAR = re.compile(r'năm\s*(\d{4})')
#: Spending split the owner asked about by name: recurrent vs development investment.
RE_PART = re.compile(r'[Cc]hi\s+(đầu\s+tư\s+phát\s+triển|thường\s+xuyên)\s*(?:ước\s+)?đạt\s+' + NUMU)


def month_num(tok):
    """'8', 'Hai', 'Mười Hai' -> 8 / 2 / 12.

    The first letter must be upper case - that is the only thing distinguishing "tháng Năm"
    (May) from "năm" (year) - but the rest is compared case-insensitively so both spellings of
    the two-word months resolve.
    """
    if tok.isdigit():
        return int(tok)
    tok = re.sub(r'\s+', ' ', tok.strip())
    if not tok[:1].isupper():
        return None
    return next((v for k, v in MONTH_WORD.items() if k.lower() == tok.lower()), None)


def cum_num(tok):
    return int(tok) if tok.isdigit() else CUM_WORD.get(tok.lower())


def parse_article(lines, url):
    """Pull the budget figures out of one monthly report.

    Returns (rows, pending). Every sentence that names a total but does not yield a clean
    (number, unit) pair lands in pending with its text - never silently dropped, never guessed.
    """
    rows, pending = [], []
    # Section 4 is the budget block; fall back to scanning every line if the heading is worded
    # differently, because a missed heading must not silently produce zero rows.
    idx = next((i for i, l in enumerate(lines)
                if re.search(r'Thu,?\s*chi\s+ngân\s+sách', l, re.I)), None)
    block = lines[idx:idx + 20] if idx is not None else lines

    for line in block:
        for m in RE_MONTH_VAL.finditer(line):
            side, motok, yr1, yr2, raw, unit = m.groups()
            mo = month_num(motok)
            # The year is often stated only in the cumulative half of the same sentence
            # ("tháng Sáu ước đạt ...; lũy kế 6 tháng đầu năm 2026"), so fall back to it.
            ytok = yr1 or yr2 or (RE_YEAR.search(line).group(1) if RE_YEAR.search(line) else None)
            yr = int(ytok) if ytok else None
            v, f = num(raw), unit_factor(unit)[0]
            if mo is None or yr is None:
                pending.append({'url': url, 'reason': 'không xác định được tháng/năm',
                                'text': line[:300]})
                continue
            if v is None or f is None:
                pending.append({'url': url, 'reason': 'số hoặc đơn vị không đọc được',
                                'text': line[:300]})
                continue
            rows.append({'mat': 'thu' if side.lower() == 'thu' else 'chi', 'ky': 'tháng',
                         'thang': mo, 'nam': yr, 'chi_tieu': 'tổng',
                         'gia_tri_goc': raw, 'dvt_nguon': unit.strip(),
                         'vnd': v * f, 'nguon': url})
            # the cumulative figure lives in the same sentence, after "lũy kế"
            tail = line[m.end():]
            c = RE_CUM_VAL.search(tail)
            if c:
                nmtok, craw, cunit = c.groups()
                nm = cum_num(nmtok)
                cv, cf = num(craw), unit_factor(cunit)[0]
                if nm is not None and cv is not None and cf is not None:
                    rows.append({'mat': 'thu' if side.lower() == 'thu' else 'chi',
                                 'ky': f'luỹ kế {nm} tháng', 'thang': nm,
                                 'nam': yr, 'chi_tieu': 'tổng', 'gia_tri_goc': craw,
                                 'dvt_nguon': cunit.strip(), 'vnd': cv * cf, 'nguon': url})
                else:
                    pending.append({'url': url, 'reason': 'luỹ kế không đọc được',
                                    'text': line[:300]})
        for m in RE_PART.finditer(line):
            what, raw, unit = m.groups()
            v, f = num(raw), unit_factor(unit)[0]
            if v is None or f is None:
                continue
            ym = RE_YEAR.search(line)
            yr = int(ym.group(1)) if ym else None
            rows.append({'mat': 'chi', 'ky': 'luỹ kế', 'thang': None, 'nam': yr,
                         'chi_tieu': re.sub(r'\s+', ' ', what.lower()), 'gia_tri_goc': raw,
                         'dvt_nguon': unit.strip(), 'vnd': v * f, 'nguon': url})
    return rows, pending


def list_articles(host, max_pages=12):
    """Every prose report on one province site, across all four listings.

    Returns absolute URLs under /thong-tin-kinh-te-xa-hoi/, which is where the other three
    redirect to anyway (a request to the ...-thang/ path answers 301).
    """
    seen = set()
    for base in LIST_PATHS:
        for p in range(1, max_pages + 1):
            path = base if p == 1 else f'{base}page-{p}/'
            try:
                body, _ = get(f'https://{host}{path}', tries=2)
            except Exception:
                break
            found = {h for h in ART.findall(body) if not SKIP_SLUG.search(h)}
            if not (found - seen):
                break
            seen |= found
    return [f'https://{host}{h}' for h in sorted(seen)]


def province(name, host=None, years=(2025, 2026)):
    """Crawl one province. Returns (rows, pending, stats)."""
    host = host or f'thongke{slug(name)}.nso.gov.vn'
    arts = list_articles(host)
    rows, pending, read = [], [], 0
    for u in arts:
        # Match the year ANYWHERE in the slug, not as "nam-YYYY": the province name can sit
        # after the year (…-6-thang-dau-nam-2025-tinh-dien-bien-616.html) and some slugs omit
        # the province entirely (…-thang-3-quy-i-nam-2026-624.html). The authoritative year is
        # the one in the sentence, which parse_article reads; this only avoids fetching old
        # articles.
        if not re.search(r'(?<!\d)(%s)(?!\d)' % '|'.join(str(y) for y in years), u):
            continue
        try:
            body, real = get(u)
        except Exception as e:
            pending.append({'url': u, 'reason': f'tải lỗi: {type(e).__name__}', 'text': ''})
            continue
        r, pd = parse_article(text_of(body), real)
        if not r:
            pending.append({'url': real, 'reason': 'không bóc được số nào', 'text': ''})
        for x in r:
            x['tinh'] = name
        rows += r
        pending += pd
        read += 1
    return rows, pending, {'tinh': name, 'host': host, 'bai_liet_ke': len(arts),
                           'bai_doc': read, 'dong': len(rows), 'pending': len(pending)}


def write_csv(rows, path):
    import csv
    with open(path, 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh)
        w.writerow(['pham_vi', 'mat', 'ky', 'nam', 'thang', 'chi_tieu', 'gia_tri_goc',
                    'dvt_nguon', 'vnd', 'nghin_ty', 'nguon'])
        for r in sorted(rows, key=lambda r: (r['nam'], r['thang'] or 0, r['ky'], r['mat'])):
            w.writerow([r['tinh'], r['mat'], r['ky'], r['nam'], r['thang'], r['chi_tieu'],
                        r['gia_tri_goc'], r['dvt_nguon'], int(r['vnd']),
                        round(r['vnd'] / 1e12, 4), r['nguon']])
    return len(rows)


NAT_LIST = 'https://www.nso.gov.vn/bao-cao-tinh-hinh-kinh-te-xa-hoi-hang-thang/'
NAT_ART = re.compile(r'(https://www\.nso\.gov\.vn/[a-z-]+/\d{4}/\d{2}/'
                     r'bao-cao-tinh-hinh-kinh-te-xa-hoi[^"\']*?)/?["\']')
#: The month figure and the cumulative one, as the national report words them:
#:   Tổng thu ngân sách Nhà nước tháng Tám ước đạt 150,4 nghìn tỷ đồng.
#:   Lũy kế tổng thu ngân sách Nhà nước tám tháng năm 2026 ước đạt 2.023,8 nghìn tỷ đồng
#: Two source quirks, both attested, both of which silently dropped a month:
#:   - "tháng" is sometimes missing: "Tổng chi ngân sách Nhà nước Bảy ước đạt 164,9 ..."
#:     (national report for July 2025). It is optional only before a capitalised month WORD;
#:     making it optional before a digit would match unrelated numbers.
#:   - a hedging word sits between "đạt" and the figure: "ước đạt gần 215,0 nghìn tỷ đồng"
#:     (May 2026). The hedge is kept in `gia_tri_goc` so the reader sees the source said
#:     "gần", and the number is recorded exactly as published.
HEDGE = r'(?:gần|khoảng|hơn|trên|xấp\s*xỉ|ước\s+tính)?\s*'
NAT_MONTH = re.compile(r'Tổng\s+(thu|chi)\s+ngân\s+sách\s+Nhà\s+nước\s+(?:tháng\s+)?('
                       + _MW + r')[^.]*?đạt\s+' + HEDGE + NUMU +
                       r'|Tổng\s+(?:thu|chi)\s+ngân\s+sách\s+Nhà\s+nước\s+tháng\s+'
                       r'(\d{1,2})[^.]*?đạt\s+' + HEDGE + NUMU)
NAT_CUM = re.compile(r'Lũy\s+kế\s+tổng\s+(thu|chi)\s+ngân\s+sách\s+Nhà\s+nước\s+('
                     + _CW + r'|\d{1,2})\s+tháng\s+năm\s+(\d{4})[^.]*?đạt\s+' + NUMU)
#: Anchor for reading one side's whole sentence: it opens with "Tổng thu/chi ngân sách Nhà
#: nước" and carries BOTH figures, the month and the cumulative one.
NAT_SIDE = re.compile(r'Tổng\s+(thu|chi)\s+ngân\s+sách\s+Nhà\s+nước')
#: Within one side's segment. "tháng" is sometimes missing before a WORD month ("... Nhà nước
#: Bảy ước đạt 164,9", July 2025); never optional before a digit, which would match anything.
NAT_MONTH_IN = re.compile(r'^\s*(?:tháng\s*)?(' + _MW + r'|/?\s*\d{1,2}\s*/\s*\d{4}|\d{1,2})'
                          r'[^.;]*?đạt\s+' + HEDGE + NUMU)
#: The cumulative clause. Most reports write "lũy kế ... hai tháng ĐẦU năm 2026"; matching only
#: the minority wording without "đầu" left the cumulative series with 4 points out of 20.
NAT_CUM_IN = re.compile(r'[Ll]ũy\s+kế[^.]*?(' + _CW + r'|\d{1,2})\s+tháng(?:\s+đầu)?'
                        r'(?:\s+năm\s+(\d{4}))?[^.]*?đạt\s+' + HEDGE + NUMU)
#: The December report states the YEAR total, with no month count: "Lũy kế tổng thu ngân sách
#: Nhà nước năm 2025 ước đạt 2.650,1 nghìn tỷ đồng". Without this the year stayed unknown and
#: fell back to the URL path - and that report is published in January, so December 2025 was
#: filed as 2026.
NAT_YEAR = re.compile(r'[Ll]ũy\s+kế[^.]{0,60}?năm\s+(\d{4})[^.]*?đạt\s+' + NUMU)


def national_article(url):
    """One national monthly report -> rows.

    The page breaks a sentence across tags ("<b>Tổng thu ngân sách Nhà nước</b> tháng Tám ước
    đạt 150,4 nghìn tỷ đồng."), so the whole document is flattened to ONE string before
    matching; splitting on tags first loses every month figure while leaving the cumulative
    ones intact, which fails silently and looks like the month is simply not published.
    """
    body, real = get(url)
    flat = re.sub(r'\s+', ' ', ' '.join(text_of(body)))
    # The report's own year always wins over the URL: a December report is published the
    # following January, so the path year is one too high for every December figure.
    ym = NAT_YEAR.search(flat)
    yr = int(ym.group(1)) if ym else None
    if yr is None:
        m = re.search(r'/(\d{4})/\d{2}/', real)
        yr = int(m.group(1)) if m else None
    if yr is None:
        return []

    # Read each side as ONE sentence rather than with two independent patterns: the report
    # states the month figure and the cumulative one together, separated by a semicolon, and
    # only the opening words say which side they belong to.
    rows, anchors = [], list(NAT_SIDE.finditer(flat))
    for k, a in enumerate(anchors):
        side = a.group(1).lower()
        stop = anchors[k + 1].start() if k + 1 < len(anchors) else a.end() + 400
        seg = flat[a.end():stop]
        mm = NAT_MONTH_IN.search(seg)
        if mm:
            # "01/2026" -> "01"; a word month keeps its single internal space, because
            # stripping all whitespace turns "Mười Hai" into "MườiHai" and the lookup misses.
            tok = mm.group(1).strip().lstrip('/').split('/')[0].strip()
            mo, v, f = month_num(tok), num(mm.group(2)), unit_factor(mm.group(3))[0]
            if None not in (mo, v, f):
                rows.append({'tinh': 'CẢ NƯỚC', 'mat': side, 'ky': 'tháng', 'thang': mo,
                             'nam': yr, 'chi_tieu': 'tổng', 'gia_tri_goc': mm.group(2),
                             'dvt_nguon': mm.group(3), 'vnd': v * f, 'nguon': real})
        cm = NAT_CUM_IN.search(seg)
        if cm:
            n, v, f = cum_num(cm.group(1)), num(cm.group(3)), unit_factor(cm.group(4))[0]
            if None not in (n, v, f):
                rows.append({'tinh': 'CẢ NƯỚC', 'mat': side, 'ky': f'luỹ kế {n} tháng',
                             'thang': n, 'nam': int(cm.group(2)) if cm.group(2) else yr,
                             'chi_tieu': 'tổng', 'gia_tri_goc': cm.group(3),
                             'dvt_nguon': cm.group(4), 'vnd': v * f, 'nguon': real})
    return rows


def national(years=(2025, 2026), max_pages=8):
    """Every national monthly report for these years."""
    urls = set()
    for p in range(1, max_pages + 1):
        u = NAT_LIST if p == 1 else f'{NAT_LIST}?paged={p}'
        try:
            body, _ = get(u, tries=2)
        except Exception:
            break
        found = {h for h in NAT_ART.findall(body)}
        if not (found - urls):
            break
        urls |= found
    rows = []
    for u in sorted(urls):
        if not re.search(r'(?<!\d)(%s)(?!\d)' % '|'.join(str(y) for y in years), u):
            continue
        try:
            rows += national_article(u)
        except Exception:
            continue
    return rows


if __name__ == '__main__':
    import sys
    if sys.argv[1:2] == ['--national']:
        rows = national()
        # A SECOND monthly series, differenced from the cumulative points. It exists because
        # the first one does not add up: the month figure is an early estimate published that
        # month and never revised, while the cumulative figure is re-estimated, so summing the
        # published months falls ~8-9% short of the published cumulative on the revenue side
        # (-60 at 2 months, -130 at 6, -176 at 8 for 2026). Spending is additive (-0.0% at 8
        # months). Neither series is corrected; both are published side by side so the reader
        # picks the basis. Differences are only emitted where BOTH endpoints exist.
        import collections
        cum = collections.defaultdict(dict)
        for r in rows:
            if r['ky'].startswith('luỹ kế'):
                cum[(r['nam'], r['thang'])][r['mat']] = r
        for (y, mo), sides in sorted(cum.items()):
            for mat, cur in sides.items():
                prev = cum.get((y, mo - 1), {}).get(mat) if mo > 1 else None
                base = 0.0 if mo == 1 else (prev['vnd'] if prev else None)
                if base is None:
                    continue
                rows.append({'tinh': 'CẢ NƯỚC', 'mat': mat, 'ky': 'tháng (suy từ luỹ kế)',
                             'thang': mo, 'nam': y, 'chi_tieu': 'tổng',
                             'gia_tri_goc': '', 'dvt_nguon': cur['dvt_nguon'],
                             'vnd': cur['vnd'] - base, 'nguon': cur['nguon']})
        n = write_csv(rows, 'output/nsnn_ca_nuoc_theo_thang.csv')
        import collections
        d = collections.defaultdict(dict)
        for r in rows:
            if r['ky'] == 'tháng':
                d[(r['nam'], r['thang'])][r['mat']] = r['vnd'] / 1e12
        print(f"{n} dòng -> output/nsnn_ca_nuoc_theo_thang.csv\n")
        # QA: the source's own monthly figures need not add up to its own cumulative figure.
        # Each month is an estimate published that month and never revised, while the
        # cumulative one is re-estimated, so they drift. Reported, never reconciled by force:
        # 2026 months 1 and 2 are both published as 163.0 while the 2-month cumulative is
        # 311.0, so the published parts exceed the published whole by 15.0.
        cum = {}
        for r in rows:
            if r['ky'].startswith('luỹ kế'):
                cum[(r['nam'], r['thang'], r['mat'])] = r['vnd'] / 1e12
        print("  KIỂM TRA nguồn tự khớp (cộng các tháng vs luỹ kế nguồn công bố):")
        for (y, n_, mat), c in sorted(cum.items()):
            parts = [d[(y, mm)].get(mat) for mm in range(1, n_ + 1) if (y, mm) in d]
            if len(parts) != n_ or any(x is None for x in parts):
                continue
            ssum = sum(parts)
            print(f"    {y} {n_:>2}T {mat}: cộng tháng {ssum:>8,.1f}  luỹ kế {c:>8,.1f}"
                  f"  lệch {ssum - c:>+7,.1f}")
        print(f"  {'tháng':<10}{'THU':>9}{'CHI':>9}{'THU-CHI':>10}")
        for k in sorted(d):
            t, c = d[k].get('thu'), d[k].get('chi')
            bal = f"{t - c:>10,.1f}" if None not in (t, c) else f"{'—':>10}"
            print(f"  {k[0]}-{k[1]:02d}   {t if t is not None else '—':>9}"
                  f"{c if c is not None else '—':>9}{bal}")
        raise SystemExit
    for nm in (sys.argv[1:] or ['Điện Biên']):
        rows, pending, st = province(nm)
        print(json.dumps(st, ensure_ascii=False))
        for r in sorted(rows, key=lambda r: (r['nam'], r['thang'] or 0, r['mat'], r['ky']))[:12]:
            print(f"   {r['mat']:<4}{r['ky']:<16}{r['nam']}-{str(r['thang'] or '?'):<3}"
                  f"{r['chi_tieu']:<22}{r['gia_tri_goc']:>12} {r['dvt_nguon']:<10}"
                  f"{r['vnd']/1e9:>12,.2f} tỷ")
        for p in pending[:5]:
            print(f"   PENDING {p['reason']}: {p['text'][:90]}")


# --------------------------------------------------------------- national, all of Vietnam
#: The national monthly report. WordPress, "?paged=N" pagination, articles under /bai-top/.
