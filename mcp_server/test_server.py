"""Checks for the invariants this server exists to hold.

    .venv/bin/python mcp_server/test_server.py

Needs data/nsnn.db (dashboard/db.py restore). No test framework: the project has none, and a
script that exits non-zero is enough. Every assertion here corresponds to a rule in CLAUDE.md
or to a defect found while building this.
"""
import pathlib, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import search, sqlguard, tools, warehouse as w

PASS = FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


def section(t):
    print(f"\n{t}")


# --- Vietnamese text -------------------------------------------------------------------------
section('folding (đ is U+0111 and has no canonical decomposition)')
check('đ folds to d', w.fold('Chi đầu tư') == 'chi dau tu', w.fold('Chi đầu tư'))
check('Đ folds to d', w.fold('Đà Nẵng') == 'da nang', w.fold('Đà Nẵng'))
check('tone marks stripped', w.fold('tiền sử dụng đất') == 'tien su dung dat')
check('NFD input folds the same as NFC',
      w.fold('Triệu đồng') == w.fold('Triệu đồng'))

# --- the outline parser that replaces dim_indicator.depth ------------------------------------
section('outline levels (depth is 1 for 87% of rows and cannot be used)')
for label, lvl in [('A TỔNG THU CÂN ĐỐI NSNN', w.SECTION), ('Đ TỔNG MỨC VAY CỦA NSĐP', w.SECTION),
                   ('I Thu nội địa', w.ROMAN), ('II Chi thường xuyên', w.ROMAN),
                   ('VIII Mục khác', w.ROMAN), ('6 Thuế bảo vệ môi trường', w.ARABIC),
                   ('1.1 Chi giáo dục', w.ARABIC + 1), ('- Thuế BVMT', w.DASH),
                   ('a Dự án chuyển tiếp', w.LOWER)]:
    check(f'outline({label[:24]!r})', w.outline(label)[1] == lvl, w.outline(label))
check('unmarked label has no level', w.outline('Thu nội địa')[1] is None)
check('a marker must be followed by whitespace',            # else it eats real words
      w.outline('Thực hiện dự án')[1] is None, w.outline('Thực hiện dự án'))

# --- money -----------------------------------------------------------------------------------
section('money (never scientific, never separators, blank stays blank)')
check('blank stays blank, never 0', w.money(None) == '', repr(w.money(None)))
check('a real zero is 0', w.money(0) == '0', repr(w.money(0)))
check('no scientific notation', 'e' not in w.money(352919320000000.0), w.money(352919320000000.0))
check('fixed point keeps the fraction', w.money(352919320000000.0) == '352919.32')
check('no thousands separator', ',' not in w.money(146068000000000.0))
check('absurd magnitudes are flagged', w.money(5.32e21).endswith('!scale?'))
check('a percentage row gets no money', w.confidence(1e9, 'SO SÁNH (%)', '%') == 'not_currency')

# --- the SQL guard ---------------------------------------------------------------------------
section('sql guard (mode=ro is not a sandbox)')
ATTACKS = ['DELETE FROM fact_row', 'UPDATE fact_row SET value=0', 'DROP TABLE fact_row',
           "ATTACH DATABASE '/tmp/evil.db' AS e", 'PRAGMA writable_schema=ON',
           "SELECT load_extension('/tmp/x.so')", 'SELECT 1; DROP TABLE fact_row',
           'SELECT randomblob(1000000000)', 'SELECT * FROM dbstat',
           'SELECT count(*) FROM "dbstat"', 'SELECT count(*) FROM main.dbstat',
           "VACUUM INTO '/tmp/copy.db'", 'SELECT 1 /* c */ ; delete from fact_row',
           'CREATE TEMP TABLE t AS SELECT 1', "SELECT writefile('/tmp/x','y')"]
for sql in ATTACKS:
    try:
        sqlguard.run(sql)
        check(f'blocked: {sql[:42]}', False, 'IT RAN')
    except sqlguard.Denied:
        check(f'blocked: {sql[:42]}', True)
    except Exception as exc:
        check(f'blocked: {sql[:42]}', True, f'({type(exc).__name__})')

cols, rows, trunc, ms = sqlguard.run(
    'WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<5) SELECT count(*) FROM c')
check('a legitimate recursive CTE still works', rows and rows[0][0] == 5, rows)
cols, rows, trunc, ms = sqlguard.run('SELECT * FROM v_fact', max_rows=200)
check('a 3.8M-row select is streamed and capped', len(rows) == 200 and trunc)
_, rows, trunc, _ = sqlguard.run('SELECT * FROM fact_row', max_rows=99999)
check('max_rows is clamped to the hard ceiling', len(rows) == sqlguard.MAX_ROWS)
t0 = time.perf_counter()
try:
    sqlguard.run('SELECT count(*) FROM fact_row a, fact_row b', timeout_s=2.0)
    check('a runaway join hits the deadline', False, 'it completed')
except sqlguard.Denied:
    check('a runaway join hits the deadline', time.perf_counter() - t0 < 4)

# --- province resolution ---------------------------------------------------------------------
section('province names (no fuzzy guessing)')
check('exact', tools.resolve_province('Bắc Ninh') == 'Bắc Ninh')
check('undiacriticised', tools.resolve_province('bac ninh') == 'Bắc Ninh')
check('alias Hanoi', tools.resolve_province('Hanoi') == 'Hà Nội')
check('alias TPHCM', tools.resolve_province('TPHCM') == 'TP Hồ Chí Minh')
check('alias Thua Thien Hue', tools.resolve_province('thua thien hue') == 'Huế')
try:
    tools.resolve_province('Bắc Giang')
    check('a pre-merger name is refused, not guessed', False, 'it resolved')
except ValueError as exc:
    check('a pre-merger name is refused, not guessed', 'An Giang' not in str(exc).split('names:')[0])
    check('the refusal lists all 34 names', str(exc).count('·') == 33, str(exc).count('·'))

# --- the double-count trap and the breakdown that avoids it ------------------------------------
section('breakdown reconciles instead of summing')
con = w.connect()
naive = con.execute("""SELECT SUM(vnd) FROM v_fact WHERE province='Nghệ An' AND year=2020
                       AND form_code='B63' AND series='QUYẾT TOÁN/TỔNG THU NSNN'""").fetchone()[0]
parent, kids, scanned = tools.children('Nghệ An', 2020, 'B63', 'QUYẾT TOÁN/TỔNG THU NSNN',
                                       'I Thu nội địa')
kidsum = sum(k['vnd'] for k in kids if k['vnd'] is not None)
check('the naive sum really does overstate', naive > parent['vnd'] * 2,
      f"{naive/parent['vnd']:.2f}x")
check('direct children reconcile to the published parent',
      abs(parent['vnd'] - kidsum) < parent['vnd'] * 0.001,
      f"residual {(parent['vnd']-kidsum)/1e6:,.0f} triệu")
check('sub-rows are excluded from the sum', len(kids) < scanned, f'{len(kids)} of {scanned}')

section('cells are keyed on the raw label, so nothing is ambiguous')
for form in ('B46', 'B49', 'B50', 'B63', 'B64', 'B65'):
    n = con.execute("""SELECT COUNT(*) FROM (SELECT province, year, indicator_raw, series,
                       COUNT(*) c FROM v_fact WHERE form_code=? AND vnd IS NOT NULL
                       GROUP BY province, year, indicator_raw, series HAVING c>1)""",
                    (form,)).fetchone()[0]
    check(f'{form} has no ambiguous cells on indicator_raw', n == 0, f'{n} ambiguous')

# --- search ------------------------------------------------------------------------------------
section('search')
check('diacritics optional',
      [h['indicator'] for h in search.indicators('giao duc', min_provinces=20, limit=3)] ==
      [h['indicator'] for h in search.indicators('giáo dục', min_provinces=20, limit=3)])
inv = search.indicators('dau tu', min_provinces=30, limit=5)
check('"dau tu" finds đầu tư, not dầu thô', any('đầu tư' in h['indicator'] for h in inv),
      [h['indicator'][:30] for h in inv])
for bad in ['chi "giáo', 'a""b', '"', 'NEAR(a b)', '*', 'chi - giáo']:
    try:
        search.indicators(bad, limit=2)
        check(f'no FTS injection: {bad!r}', True)
    except Exception as exc:
        check(f'no FTS injection: {bad!r}', False, f'{type(exc).__name__}: {exc}')

# --- tool surface --------------------------------------------------------------------------------
section('tools return text and report what they cannot do')
check('describe_corpus warns about summing', 'NEVER SUM ROWS' in tools.describe_corpus())
cmp_out = tools.compare_provinces('B46', 'B TỔNG CHI NSĐP', 'DỰ TOÁN', 2021)
check('a comparison always prints coverage', 'coverage:' in cmp_out)
check('a source defect is flagged, not hidden', 'MAGNITUDE?' in cmp_out)
check('pending reports are listed with a reason',
      'PENDING_PDF_DOC' in tools.list_data_quality(block='pending', limit=3))
try:
    tools.read_timeseries('Nghệ An', 'B63', 'I Thu nội địa')
    check('an ambiguous series is refused, not guessed', False, 'it picked one')
except ValueError as exc:
    check('an ambiguous series is refused, not guessed', 'naming one is required' in str(exc))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
