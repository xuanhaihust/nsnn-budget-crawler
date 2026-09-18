#!/usr/bin/env python3
"""Load the national monthly series into the warehouse as its OWN table.

    .venv/bin/python dashboard/load_national.py          # load, then verify
    .venv/bin/python dashboard/load_national.py --check   # verify only

Deliberately NOT merged into `fact_row`, even though it is budget data in VND and the
temptation is obvious. Three reasons, any one of which is enough:

1. `fact_row` holds 34 provinces' LOCAL budgets and nothing else. CLAUDE.md's rule is that
   the corpus has no national total and that summing provinces does not make one. Putting a
   CẢ NƯỚC row into the same table hands every existing query that does not filter on
   province a national total sitting beside the provincial rows it would be summed with -
   the exact double count the whole warehouse is built to prevent.
2. `dim_period.kind` takes exactly four values, none of them a month, and `describe_corpus`
   and the analysis notes both state that as a fact about the table. A month row would make
   that documentation quietly false.
3. This series has three parallel bases for the same month, one of which (`tháng (suy từ
   luỹ kế)`) this project computed rather than read. `fact_row` has no column for that
   distinction, so it would be lost exactly where it matters most.

Separate table, separate tool, no join path that lets the two be added together by accident.
"""
import csv, pathlib, sqlite3, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / 'data' / 'nsnn.db'
CSV = ROOT / 'output' / 'nsnn_ca_nuoc_theo_thang.csv'
TABLE = 'fact_national_monthly'

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id             INTEGER PRIMARY KEY,
    year           INTEGER NOT NULL,
    month          INTEGER NOT NULL,       -- for a cumulative row, the month it runs TO
    side           TEXT    NOT NULL,       -- 'thu' | 'chi'
    basis          TEXT    NOT NULL,       -- see BASIS below
    published_text TEXT,                   -- the figure as printed; empty when derived
    unit_source    TEXT,                   -- the unit as printed, e.g. 'nghìn tỷ đồng'
    vnd            INTEGER NOT NULL,
    source_url     TEXT    NOT NULL,
    UNIQUE (year, month, side, basis)
);
CREATE INDEX IF NOT EXISTS ix_natmon ON {TABLE} (year, month, side, basis);
"""

#: The three bases, kept apart because adding them together is meaningless. The CSV writes the
#: cumulative one as "luỹ kế 8 tháng"; the count is normalised away here because it is already
#: the `month` column, and leaving it in the basis made every N its own basis value - eleven of
#: them - so a caller had to know N before it could ask for the series at all.
BASIS = {
    'tháng': 'as the source published it that month',
    'tháng (suy từ luỹ kế)': 'successive difference of the cumulative series - computed here',
    'luỹ kế': 'year to date through `month`, as published',
}


def norm_basis(ky):
    return 'luỹ kế' if ky.startswith('luỹ kế') else ky


def load():
    if not CSV.exists():
        sys.exit(f"{CSV} not found - run pipeline/nso_monthly.py --national first")
    rows = list(csv.DictReader(CSV.open(encoding='utf-8-sig')))
    con = sqlite3.connect(DB)
    con.executescript(DDL)
    con.execute(f"DELETE FROM {TABLE}")          # idempotent: a reload replaces, never appends
    n = 0
    for r in rows:
        if r['pham_vi'] != 'CẢ NƯỚC' or not r['vnd']:
            continue
        con.execute(
            f"""INSERT OR REPLACE INTO {TABLE}
                (year, month, side, basis, published_text, unit_source, vnd, source_url)
                VALUES (?,?,?,?,?,?,?,?)""",
            (int(r['nam']), int(r['thang']), r['mat'], norm_basis(r['ky']),
             r['gia_tri_goc'] or None,
             r['dvt_nguon'] or None, int(r['vnd']), r['nguon']))
        n += 1
    con.commit()
    return con, n


def check(con):
    """Prove the load, and re-state the one defect a reader must know about.

    Not a smoke test: it re-derives the additivity gap from the table itself, so if a future
    reload changes the numbers the discrepancy is recomputed rather than quoted from memory.
    """
    q = lambda s, *a: con.execute(s, a).fetchall()
    tot = q(f"SELECT COUNT(*) FROM {TABLE}")[0][0]
    print(f"{TABLE}: {tot} rows")
    for basis, n, y0, y1 in q(
            f"""SELECT basis, COUNT(*), MIN(year || '-' || substr('0'||month,-2)),
                       MAX(year || '-' || substr('0'||month,-2))
                FROM {TABLE} GROUP BY basis ORDER BY basis"""):
        print(f"  {basis:<24} {n:>3}  {y0} .. {y1}")

    print("\n  published months vs the source's OWN cumulative (nghìn tỷ):")
    bad = 0
    for y, m, side, cum in q(
            f"""SELECT year, month, side, vnd FROM {TABLE}
                WHERE basis = 'luỹ kế' ORDER BY side, year, month"""):
        parts = q(f"""SELECT COUNT(*), SUM(vnd) FROM {TABLE}
                      WHERE basis = 'tháng' AND side = ? AND year = ? AND month <= ?""",
                  side, y, m)[0]
        if parts[0] != m:                      # only compare a complete run of months
            continue
        gap = (parts[1] - cum) / 1e12
        flag = '  <-- thu lệch lớn' if side == 'thu' and abs(gap) > 50 else ''
        bad += abs(gap) > 50
        print(f"    {y} {m:>2}T {side}: cộng {parts[1]/1e12:>8,.1f}  luỹ kế {cum/1e12:>8,.1f}"
              f"  lệch {gap:>+7,.1f}{flag}")
    print(f"\n  {bad} mốc lệch quá 50 nghìn tỷ - all on the revenue side, and NOT corrected:")
    print("  the month figure is an early estimate never revised, the cumulative is re-estimated.")
    assert tot > 0, 'no rows loaded'
    assert not q(f"SELECT 1 FROM {TABLE} WHERE vnd IS NULL OR vnd <= 0"), 'bad vnd'
    assert not q(f"SELECT 1 FROM {TABLE} WHERE side NOT IN ('thu','chi')"), 'bad side'
    print("\n  OK")


if __name__ == '__main__':
    if '--check' in sys.argv:
        check(sqlite3.connect(f'file:{DB}?mode=ro', uri=True))
    else:
        c, n = load()
        print(f"loaded {n} rows into {DB.name}\n")
        check(c)
