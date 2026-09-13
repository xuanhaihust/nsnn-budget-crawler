"""Bring data/nsnn.db in line with the unit rules in pipeline/parse_xml.py.

    .venv/bin/python dashboard/migrate_units.py --check    # report, change nothing
    .venv/bin/python dashboard/migrate_units.py --apply    # rewrite dim_unit and v_fact

Why this exists as a migration rather than an ETL re-run: the warehouse is built from `work/`,
which is several GB of cached downloads and is not in a fresh checkout. Recomputing the unit
dimension does not need any of that - `dim_unit.factor` is a pure function of the label, so
this reproduces exactly what `dashboard/etl.py` would now produce, and nothing else changes.

What it does:

* `dim_unit.factor` is recomputed with the current `unit_factor`, which gained the six attested
  currency spellings. 660 rows across 4 spellings ("Triệu dồng", "Tiệu đồng", "Tr đồng",
  "Triệu đổng") carried a real figure and no conversion until now.
* `dim_unit.canon` is added: one spelling per factor, so a query can group by unit without
  seeing "Triệu đồng", "triệu đồng", "1.000.000 đồng" and "Tr đồng" as four different things.
* `v_fact.unit` now reports the canonical spelling and `v_fact.unit_source` the spelling the
  workbook actually published, so nothing is lost.

The fact rows themselves are never touched: no value changes, no row is added or removed, and
a blank stays blank. Only the unit dimension and the view are rewritten.
"""
import pathlib, sqlite3, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'pipeline'))
from parse_xml import canon_unit, unit_factor            # noqa: E402

DB = ROOT / 'data' / 'nsnn.db'

VIEW = """
DROP VIEW IF EXISTS v_fact;
CREATE VIEW v_fact AS
SELECT f.rowid AS id, p.name AS province, pe.label AS period, pe.year, pe.kind,
       s.label AS scope, t.label AS table_label, t.form_code,
       i.label AS indicator_raw, i.clean AS indicator, i.depth,
       se.label AS series, u.canon AS unit, u.label AS unit_source, rw.label AS raw, f.value,
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


def plan(con):
    """What would change, per unit spelling. Reads only."""
    rows = []
    for r in con.execute("""SELECT u.id, u.label, u.factor, COUNT(f.rowid) AS n,
                                   SUM(CASE WHEN f.value IS NOT NULL THEN 1 ELSE 0 END) AS nv
                            FROM dim_unit u LEFT JOIN fact_row f ON f.unit_id = u.id
                            GROUP BY u.id ORDER BY n DESC"""):
        want = unit_factor(r['label']) or 0
        rows.append(dict(id=r['id'], label=r['label'], old=r['factor'], new=want,
                         canon=canon_unit(r['label']), n=r['n'], nv=r['nv']))
    return rows


def report(rows):
    gained = sum(r['nv'] for r in rows if r['old'] == 0 and r['new'] > 0)
    renamed = sum(r['n'] for r in rows if r['canon'] != r['label'])
    print(f"{'ĐVT (as published)':<24} {'factor now':>12} {'factor after':>13} "
          f"{'rows':>10}  canonical")
    print('-' * 80)
    for r in rows:
        mark = '  <== gains VND' if r['old'] == 0 and r['new'] > 0 else ''
        if r['old'] > 0 and r['new'] != r['old']:
            mark = '  <== FACTOR CHANGES, INVESTIGATE'
        print(f"{r['label']!r:<24} {r['old']:>12,} {r['new']:>13,} {r['n']:>10,}  "
              f"{r['canon']!r}{mark}")
    print(f"\n{gained:,} rows with a value gain a VND conversion")
    print(f"{renamed:,} rows get a unified spelling (the published spelling is kept in "
          f"v_fact.unit_source)")
    danger = [r for r in rows if r['old'] > 0 and r['new'] != r['old']]
    if danger:
        print(f"\n!! {len(danger)} spellings would have their existing factor CHANGED. That is "
              "not a cleanup, it would restate published figures - stop and check by hand.")
    return not danger


def main():
    if not DB.exists():
        sys.exit(f"{DB} not found - run `dashboard/db.py restore` first")
    apply = '--apply' in sys.argv
    if not apply and '--check' not in sys.argv:
        sys.exit(__doc__)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = plan(con)
    safe = report(rows)
    if not apply:
        print("\n(--check: nothing was written)")
        return
    if not safe:
        sys.exit("refusing to apply while an existing factor would change")

    before = con.execute("""SELECT COUNT(*) FROM fact_row f JOIN dim_unit u ON u.id=f.unit_id
                            WHERE f.value IS NOT NULL AND u.factor > 0""").fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM fact_row").fetchone()[0]

    cols = {r[1] for r in con.execute("PRAGMA table_info(dim_unit)")}
    if 'canon' not in cols:
        con.execute("ALTER TABLE dim_unit ADD COLUMN canon TEXT")
    for r in rows:
        con.execute("UPDATE dim_unit SET factor = ?, canon = ? WHERE id = ?",
                    (r['new'], r['canon'], r['id']))
    con.executescript(VIEW)
    con.commit()

    after = con.execute("""SELECT COUNT(*) FROM fact_row f JOIN dim_unit u ON u.id=f.unit_id
                           WHERE f.value IS NOT NULL AND u.factor > 0""").fetchone()[0]
    now_total = con.execute("SELECT COUNT(*) FROM fact_row").fetchone()[0]
    con.close()
    print(f"\nrows with a VND value : {before:,} -> {after:,}  (+{after - before:,})")
    print(f"fact rows             : {total:,} -> {now_total:,}  (unchanged)")
    if now_total != total:
        sys.exit("fact_row count changed - this migration must never do that")
    print("\nRepack with `.venv/bin/python dashboard/db.py pack` to ship the change.")


if __name__ == '__main__':
    main()
