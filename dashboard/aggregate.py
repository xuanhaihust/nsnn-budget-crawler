"""Query data/nsnn.db down to the compact JSON the dashboard embeds.

The warehouse is ~360 MB, far too heavy to ship to a browser, and a dashboard only
ever needs the grouped answers. Everything below is a GROUP BY over the full corpus,
written once into dashboard/data.json (~50 KB) so the page stays a single file with
no network calls at all.
"""
import json, pathlib, sqlite3, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
con = sqlite3.connect(ROOT / 'data' / 'nsnn.db')
con.row_factory = sqlite3.Row
q = lambda s, *a: [dict(r) for r in con.execute(s, a).fetchall()]

provinces = q("SELECT id, name FROM dim_province ORDER BY name")
pname = {p['id']: p['name'] for p in provinces}

kpi = q("""SELECT (SELECT COUNT(*) FROM fact_row)                      AS rows_total,
                  (SELECT COUNT(*) FROM dim_province)                  AS provinces,
                  (SELECT COUNT(*) FROM dim_report)                    AS reports,
                  (SELECT COUNT(*) FROM fact_row f JOIN dim_unit u ON u.id=f.unit_id
                    WHERE f.value IS NOT NULL AND u.factor>0)          AS vnd_rows,
                  (SELECT COUNT(*) FROM dim_report WHERE rows=0)       AS pending,
                  (SELECT COUNT(DISTINCT year) FROM dim_period WHERE year IS NOT NULL) AS years,
                  (SELECT COUNT(*) FROM dim_indicator)                 AS indicators""")[0]

# province x year report counts - the coverage grid
heat = q("""SELECT province_id AS p, CAST(doc_date AS INTEGER) AS y, COUNT(*) AS n
            FROM dim_report WHERE doc_date GLOB '[12][0-9][0-9][0-9]'
            GROUP BY p, y ORDER BY y""")

# rows per year per scope
timeline = q("""SELECT pe.year AS y, s.label AS scope, COUNT(*) AS n
                FROM fact_row f JOIN dim_period pe ON pe.id=f.period_id
                JOIN dim_scope s ON s.id=f.scope_id
                WHERE pe.year IS NOT NULL GROUP BY y, scope ORDER BY y""")

# how each province's reports were read
method = q("""SELECT province_id AS p, method AS m, COUNT(*) AS n
              FROM dim_report GROUP BY p, m""")

volume = q("""SELECT f.province_id AS p, COUNT(*) AS rows_n,
                     SUM(CASE WHEN f.value IS NOT NULL AND u.factor>0 THEN 1 ELSE 0 END) AS vnd_n
              FROM fact_row f JOIN dim_unit u ON u.id=f.unit_id GROUP BY p""")

units = q("""SELECT u.label AS u, u.factor AS f, COUNT(*) AS n
             FROM fact_row r JOIN dim_unit u ON u.id=r.unit_id
             WHERE u.label <> '' GROUP BY u.label ORDER BY n DESC""")

pending = q("""SELECT method AS m, COUNT(*) AS n FROM dim_report
               WHERE rows=0 GROUP BY m ORDER BY n DESC""")

formats = q("""SELECT formats AS f, COUNT(*) AS n FROM dim_report
                GROUP BY f ORDER BY n DESC LIMIT 14""")

# the deepest, most-repeated line items - what the corpus is actually made of
topind = q("""SELECT i.clean AS label, COUNT(*) AS n,
                     SUM(CASE WHEN f.value IS NOT NULL AND u.factor>0 THEN 1 ELSE 0 END) AS vnd_n
              FROM fact_row f JOIN dim_indicator i ON i.id=f.indicator_id
              JOIN dim_unit u ON u.id=f.unit_id
              WHERE LENGTH(i.clean) > 6
              GROUP BY i.clean ORDER BY n DESC LIMIT 25""")

periods = q("""SELECT kind, COUNT(*) AS n FROM dim_period p
                JOIN fact_row f ON f.period_id=p.id GROUP BY kind ORDER BY n DESC""")

# --- the budget itself -------------------------------------------------------------
# Form B46 is Cân đối ngân sách địa phương, the provincial balance sheet, and it is the one
# place in this corpus where a cross-province comparison is defensible: every figure below is
# a single published cell of one named form, never a sum of line items that might double
# count. The series is DỰ TOÁN, so these are budget PLANS, not settled accounts.
B46 = ('TỔNG NGUỒN THU NSĐP', 'TỔNG CHI NSĐP', 'Thu NSĐP được hưởng theo phân cấp',
       'Thu bổ sung từ NSTW', 'Chi đầu tư phát triển', 'Chi thường xuyên',
       'BỘI CHI NSĐP/BỘI THU NSĐP')
marks = q(f"""SELECT province, year, indicator, vnd FROM v_fact
              WHERE form_code='B46' AND vnd IS NOT NULL
                AND indicator IN ({','.join('?' * len(B46))})""", *B46)

bal = {}
for m in marks:
    bal.setdefault((m['province'], m['year']), {})[m['indicator']] = m['vnd']

budget = []
for (prov, year), d in sorted(bal.items()):
    rev, exp = d.get('TỔNG NGUỒN THU NSĐP'), d.get('TỔNG CHI NSĐP')
    own, central = d.get('Thu NSĐP được hưởng theo phân cấp'), d.get('Thu bổ sung từ NSTW')
    inv, rec = d.get('Chi đầu tư phát triển'), d.get('Chi thường xuyên')
    budget.append(dict(
        p=prov, y=year, rev=rev, exp=exp, own=own, central=central, inv=inv, rec=rec,
        # a ratio of two published cells - no summing, so no double-count risk
        selfrate=round(own / rev, 4) if own and rev else None,
        invrate=round(inv / exp, 4) if inv and exp else None))

# one headline row per province: its most recent year that carries both cells
latest = {}
for b in budget:
    if b['selfrate'] is None:
        continue
    if b['p'] not in latest or b['y'] > latest[b['p']]['y']:
        latest[b['p']] = b

out = dict(
    generated_from='data/nsnn.db',
    kpi=kpi,
    provinces=[{'id': p['id'], 'name': p['name']} for p in provinces],
    heat=heat,
    timeline=timeline,
    method=method,
    volume=volume,
    units=units,
    pending=pending,
    formats=formats,
    topind=topind,
    periods=periods,
    budget=budget,
    latest=sorted(latest.values(), key=lambda b: -b['selfrate']),
)
dest = ROOT / 'dashboard' / 'data.json'
dest.write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')))
print(f"{dest}  {dest.stat().st_size/1024:.0f} KB")
for k, v in kpi.items():
    print(f"  {k:<12} {v:,}")
print(f"  heat cells {len(heat)}, timeline {len(timeline)}, units {len(units)}, methods {len(method)}")
print(f"  B46 province-years {len(budget)}, provinces with a self-sufficiency ratio {len(latest)}")
