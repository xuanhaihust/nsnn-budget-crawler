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
SCALE = {'tỷ': 1e9, 'tỉ': 1e9, 'triệu': 1e6, 'nghìn': 1e3, 'ngàn': 1e3, 'đồng': 1.0}


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
    """Read the scale from the words right after the number. None when it cannot be read."""
    m = re.match(r'\s*(tỷ|tỉ|triệu|nghìn|ngàn)?\s*đồng', tail, re.I)
    if not m:
        return None, ''
    word = (m.group(1) or 'đồng').lower()
    return SCALE.get(word), m.group(0).strip()


#: Months are written as a WORD as often as a digit - "tháng Hai năm 2025", "tháng Sáu ước
#: đạt". The trap: "tháng Năm" is MAY, while "năm 2025" is the YEAR, and they differ only by
#: capitalisation, so this map is matched case-SENSITIVELY. Folding case here would read the
#: December report's "Tổng thu ... năm 2025" as a May figure.
MONTH_WORD = {'Một': 1, 'Hai': 2, 'Ba': 3, 'Tư': 4, 'Bốn': 4, 'Năm': 5, 'Sáu': 6, 'Bảy': 7,
              'Bẩy': 7, 'Tám': 8, 'Chín': 9, 'Mười': 10, 'Mười một': 11, 'Mười hai': 12}
#: Same words as ordinals in the cumulative clause: "lũy kế hai tháng đầu năm 2025".
CUM_WORD = {'một': 1, 'hai': 2, 'ba': 3, 'tư': 4, 'bốn': 4, 'năm': 5, 'sáu': 6, 'bảy': 7,
            'tám': 8, 'chín': 9, 'mười': 10, 'mười một': 11, 'mười hai': 12}
_MW = '|'.join(sorted(MONTH_WORD, key=len, reverse=True))
_CW = '|'.join(sorted(CUM_WORD, key=len, reverse=True))

#: "tháng 8/2026", "tháng 8 năm 2026", "tháng Hai năm 2025", "tháng Sáu" (year omitted).
MONTH = r'tháng\s*(\d{1,2}|' + _MW + r')\s*(?:/\s*(\d{4})|năm\s*(\d{4}))?'
NUMU = r'([\d.,]+)\s*((?:tỷ|tỉ|triệu|nghìn|ngàn)?\s*đồng)'
RE_MONTH_VAL = re.compile(r'Tổng\s+(thu|chi)\s+ngân\s+sách[^.;]*?' + MONTH +
                          r'[^.;]*?đạt\s+' + NUMU)
#: "lũy kế 8 tháng", "lũy kế ... hai tháng đầu năm 2025"
RE_CUM_VAL = re.compile(r'[Ll]ũy\s*kế[^.;]*?(\d{1,2}|' + _CW + r')\s*tháng[^.;]*?đạt\s+' + NUMU)
#: The year, when the month phrase omits it, comes from elsewhere in the same sentence.
RE_YEAR = re.compile(r'năm\s*(\d{4})')
#: Spending split the owner asked about by name: recurrent vs development investment.
RE_PART = re.compile(r'[Cc]hi\s+(đầu\s+tư\s+phát\s+triển|thường\s+xuyên)\s*(?:ước\s+)?đạt\s+' + NUMU)


def month_num(tok):
    """'8' or 'Hai' -> 8 / 2. Case-sensitive on the word form; see MONTH_WORD."""
    return int(tok) if tok.isdigit() else MONTH_WORD.get(tok)


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


if __name__ == '__main__':
    import sys
    for nm in (sys.argv[1:] or ['Điện Biên']):
        rows, pending, st = province(nm)
        print(json.dumps(st, ensure_ascii=False))
        for r in sorted(rows, key=lambda r: (r['nam'], r['thang'] or 0, r['mat'], r['ky']))[:12]:
            print(f"   {r['mat']:<4}{r['ky']:<16}{r['nam']}-{str(r['thang'] or '?'):<3}"
                  f"{r['chi_tieu']:<22}{r['gia_tri_goc']:>12} {r['dvt_nguon']:<10}"
                  f"{r['vnd']/1e9:>12,.2f} tỷ")
        for p in pending[:5]:
            print(f"   PENDING {p['reason']}: {p['text'][:90]}")
