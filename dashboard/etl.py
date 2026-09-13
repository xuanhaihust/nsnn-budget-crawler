"""Build a normalised SQLite warehouse from the 34 province workbooks.

The Data sheets are one long fact table with heavy repetition: 34 provinces share
20 periods, 3 scopes and a few thousand indicator labels across 3.8M rows. Storing
those as text 3.8M times is most of the file, so every repeated string becomes a
row in a dim_* table and the fact table carries integer ids. Nothing is aggregated
here - fact_row is the full corpus, one row per source row, so any later number can
be traced back to its report and its stored source file.

    .venv/bin/python dashboard/etl.py            # rebuild data/nsnn.db from work/
"""
import sys, pathlib, sqlite3, re, unicodedata, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'pipeline'))
import build as pbuild                                    # noqa: E402
import harvest, run as prun                               # noqa: E402
from parse_xml import canon_unit, unit_factor             # noqa: E402

# work/<slug>/ is ASCII-folded, so the directory name is not a province name:
# work/Ho_Chi_Minh is "TP Hồ Chí Minh". provinces34.txt holds the current official
# spellings; ALIAS covers the two the portal files under an older name.
SLUG_TO_NAME = {harvest.slug(prun.ALIAS.get(n, n)): n for n in prun.provinces34()}

DB = ROOT / 'data' / 'nsnn.db'

SCHEMA = """
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
CREATE TABLE dim_province (id INTEGER PRIMARY KEY, name TEXT UNIQUE, slug TEXT);
CREATE TABLE dim_period   (id INTEGER PRIMARY KEY, label TEXT UNIQUE, year INTEGER, kind TEXT);
CREATE TABLE dim_scope    (id INTEGER PRIMARY KEY, label TEXT UNIQUE);
CREATE TABLE dim_table    (id INTEGER PRIMARY KEY, label TEXT UNIQUE, form_code TEXT);
CREATE TABLE dim_indicator(id INTEGER PRIMARY KEY, label TEXT UNIQUE, clean TEXT, depth INTEGER);
CREATE TABLE dim_series   (id INTEGER PRIMARY KEY, label TEXT UNIQUE);
CREATE TABLE dim_unit     (id INTEGER PRIMARY KEY, label TEXT UNIQUE, factor INTEGER,
                           canon TEXT);
CREATE TABLE dim_raw      (id INTEGER PRIMARY KEY, label TEXT UNIQUE);
CREATE TABLE dim_report   (id INTEGER PRIMARY KEY, report_id INTEGER, province_id INTEGER,
                           title TEXT, doc_no TEXT, doc_date TEXT, method TEXT,
                           reason TEXT, formats TEXT, rows INTEGER, source_url TEXT);
CREATE TABLE fact_row (
  province_id INTEGER, period_id INTEGER, scope_id INTEGER, table_id INTEGER,
  indicator_id INTEGER, series_id INTEGER, unit_id INTEGER, report_id INTEGER,
  raw_id INTEGER, value REAL);
"""

# Quy đổi VND is not stored: it is exactly value * dim_unit.factor, and factor is 0
# precisely where build.py leaves the conversion blank. Deriving it in a view keeps the
# rule in one place and takes 30 MB of duplicated floats out of the file.
VIEW = """
CREATE VIEW v_fact AS
SELECT f.rowid AS id, p.name AS province, pe.label AS period, pe.year, pe.kind,
       s.label AS scope, t.label AS table_label, t.form_code,
       i.label AS indicator_raw, i.clean AS indicator, i.depth,
       se.label AS series, u.canon AS unit, u.label AS unit_source,
       rw.label AS raw, f.value,
       CASE WHEN u.factor > 0 AND f.value IS NOT NULL THEN f.value * u.factor END AS vnd,
       f.report_id
FROM fact_row f
JOIN dim_province  p  ON p.id  = f.province_id
JOIN dim_period    pe ON pe.id = f.period_id
JOIN dim_scope     s  ON s.id  = f.scope_id
JOIN dim_table     t  ON t.id  = f.table_id
JOIN dim_indicator i  ON i.id  = f.indicator_id
JOIN dim_series    se ON se.id = f.series_id
JOIN dim_unit      u  ON u.id  = f.unit_id
JOIN dim_raw       rw ON rw.id = f.raw_id;
"""

INDEX = """
CREATE INDEX ix_fact_prov   ON fact_row(province_id);
CREATE INDEX ix_fact_period ON fact_row(period_id);
CREATE INDEX ix_fact_ind    ON fact_row(indicator_id);
CREATE INDEX ix_fact_pps    ON fact_row(province_id, period_id, scope_id);
CREATE INDEX ix_report_prov ON dim_report(province_id);
"""

RE_YEAR = re.compile(r'(19|20)\d{2}')
# Indicator labels arrive with their outline marker glued on: "I Thu nội địa",
# "2.0 Thực hiện dự án", "- Thuế GTGT", "a Dự án chuyển tiếp".
# The marker must be FOLLOWED BY WHITESPACE, or the pattern eats real words one letter
# at a time: "Thực hiện dự án" -> "ực hiện dự án", "Sở Nội vụ" -> "ở Nội vụ".
RE_MARK = re.compile(r'^\s*(?:[-*+•]+|\(?[0-9]+(?:[.][0-9]+)*[.)]?|[IVXLC]{1,5}[.)]?|[A-Za-z][.)]?)(?=\s)\s+')


def period_parts(label):
    m = RE_YEAR.search(label or '')
    year = int(m.group()) if m else None
    low = (label or '').lower()
    kind = ('Năm' if low.startswith('năm') else
            '6 tháng' if '6 tháng' in low else
            '9 tháng' if '9 tháng' in low else
            'Quý' if 'quý' in low else 'Khác')
    return year, kind


def indicator_parts(label):
    """Split "II 1 Chi giáo dục" into its outline depth and its bare label."""
    s = unicodedata.normalize('NFC', (label or '').strip())
    clean, depth = s, 0
    while True:
        stripped = RE_MARK.sub('', clean, count=1)
        if stripped == clean or not stripped:
            break
        clean, depth = stripped, depth + 1
    return clean.strip() or s, depth


class Dim:
    """Intern a repeated string once and hand back its integer id."""

    def __init__(self, cur, table, extra_cols=()):
        self.cur, self.table, self.extra, self.seen = cur, table, extra_cols, {}
        self.col = 'name' if table == 'dim_province' else 'label'

    def id(self, value, *extra):
        if value in self.seen:
            return self.seen[value]
        n = len(self.seen) + 1
        cols = ','.join(('id', self.col) + self.extra)
        self.cur.execute(f"INSERT INTO {self.table} ({cols}) VALUES ({','.join('?' * (2 + len(extra)))})",
                         (n, value, *extra))
        self.seen[value] = n
        return n


def main():
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.executescript(SCHEMA)

    d_prov = Dim(cur, 'dim_province', ('slug',))
    d_per = Dim(cur, 'dim_period', ('year', 'kind'))
    d_scope = Dim(cur, 'dim_scope')
    d_tab = Dim(cur, 'dim_table', ('form_code',))
    d_ind = Dim(cur, 'dim_indicator', ('clean', 'depth'))
    d_ser = Dim(cur, 'dim_series')
    d_unit = Dim(cur, 'dim_unit', ('factor', 'canon'))
    d_raw = Dim(cur, 'dim_raw')

    rep_pk = 0
    batch, total, t0 = [], 0, time.time()
    for wd in sorted((ROOT / 'work').iterdir()):
        if not wd.is_dir():
            continue
        province = SLUG_TO_NAME.get(wd.name, wd.name.replace('_', ' '))
        final, status, _ = pbuild.build(province, wd)
        pid = d_prov.id(province, wd.name)

        for rid, v in status.items():
            rep_pk += 1
            cur.execute("INSERT INTO dim_report VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (rep_pk, rid, pid, v['title'], '', str(v['year']), v['method'],
                         v['err'], ','.join(v['exts']), v['rows'], ''))

        for r in final:
            label = str(r['Nội dung/Bảng'] or '')
            m = re.search(r'\[([^\]]*)\]\s*$', label)
            per = str(r['Kỳ dữ liệu'] or '')
            yr, kind = period_parts(per)
            ind = str(r['Chỉ tiêu'] or '')
            clean, depth = indicator_parts(ind)
            unit = str(r['ĐVT'] or '')
            val = r['Giá trị chuẩn hóa']
            batch.append((
                pid, d_per.id(per, yr, kind), d_scope.id(str(r['Phạm vi'] or '')),
                d_tab.id(label, m.group(1) if m else ''),
                d_ind.id(ind, clean, depth), d_ser.id(str(r['Loại số liệu'] or '')),
                d_unit.id(unit, unit_factor(unit) or 0, canon_unit(unit)),
                int(re.search(r'report (\d+)', str(r['Ghi chú'] or '')).group(1))
                if re.search(r'report (\d+)', str(r['Ghi chú'] or '')) else 0,
                d_raw.id(str(r['Giá trị gốc'] or '')),
                float(val) if val not in (None, '') else None))
            if len(batch) >= 50_000:
                cur.executemany("INSERT INTO fact_row VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
                total += len(batch); batch = []
        print(f"  {wd.name:<18} {len(final):>9,} rows", flush=True)

    if batch:
        cur.executemany("INSERT INTO fact_row VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
        total += len(batch)
    cur.executescript(VIEW)
    cur.executescript(INDEX)
    con.commit()
    cur.execute("VACUUM")
    con.close()
    print(f"\n{total:,} fact rows -> {DB} ({DB.stat().st_size/1e6:.0f} MB) in {time.time()-t0:.0f}s")


if __name__ == '__main__':
    main()
