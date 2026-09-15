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



# --- structure: revenue, spending, sectors, plan-vs-outturn ------------------------------
# A form can publish the same indicator in several columns - "dự toán" beside "quyết toán",
# or a province-level column beside a district one. Where the header rebuild gave two of
# those columns the same series label, the cell is ambiguous and there is no way to tell
# which column a value came from. Those keys are DROPPED rather than guessed at, and the
# count of drops is printed. B46 and B63 survive this cleanly; B50 and B65 largely do not,
# which is why the sector figures come from B64.
def cells(form, series, names):
    rows = q(f"""SELECT province, year, indicator, vnd, COUNT(*) AS n
                 FROM v_fact
                 WHERE form_code=? AND series=? AND vnd IS NOT NULL
                   AND indicator IN ({','.join('?' * len(names))})
                 GROUP BY province, year, indicator""", form, series, *names)
    out, dropped = {}, 0
    for r in rows:
        if r['n'] > 1:                      # same key in two columns: unusable
            dropped += 1
            continue
        out.setdefault((r['province'], r['year']), {})[r['indicator']] = r['vnd']
    AMBIG[form] = AMBIG.get(form, 0) + dropped
    return out


AMBIG = {}


def latest_of(d, need):
    """Each province's most recent year that carries every indicator in `need`."""
    best = {}
    for (prov, year), v in d.items():
        if all(v.get(k) for k in need) and (prov not in best or year > best[prov][0]):
            best[prov] = (year, v)
    return best


def mix(raw, total_key, parts, min_parts=5):
    """Composition of one published parent, with the unnamed remainder shown as its own slice."""
    out = []
    for prov, (year, v) in sorted(latest_of(raw, [total_key]).items()):
        tot = v[total_key]
        got = {k: v[k] for k in parts if v.get(k)}
        named = sum(got.values())
        if len(got) < min_parts or named > tot * 1.02:
            continue
        out.append(dict(p=prov, y=year, tot=tot, parts=got, other=max(tot - named, 0)))
    return out


# Revenue: the settled accounts (quyết toán), not the plan
REV_PARTS = ['Thu tiền sử dụng đất', 'Thu từ khu vực doanh nghiệp có vốn đầu tư nước ngoài',
             'Thu từ khu vực kinh tế ngoài quốc doanh', 'Thu từ khu vực DNNN do Trung ương quản lý',
             'Thu từ khu vực DNNN do địa phương quản lý', 'Thuế thu nhập cá nhân',
             'Thu từ hoạt động xổ số kiến thiết', 'Lệ phí trước bạ',
             'Thuế bảo vệ môi trường', 'Thu phí, lệ phí']
revmix = mix(cells('B63', 'QUYẾT TOÁN/TỔNG THU NSNN', ['Thu nội địa'] + REV_PARTS),
             'Thu nội địa', REV_PARTS)

# Spending by sector. B65 (quyết toán) publishes these lines in two same-labelled columns
# and loses ~120 of ~125 keys to ambiguity; B50 (dự toán) keeps 81-106 of them, so the
# sector split is read there and is a plan, like the self-sufficiency headline.
SPEND_PARTS = ['Chi giáo dục - đào tạo và dạy nghề', 'Chi y tế, dân số và gia đình',
               'Chi bảo đảm xã hội', 'Chi các hoạt động kinh tế',
               'Chi hoạt động của cơ quan quản lý nhà nước, đảng, đoàn thể',
               'Chi khoa học và công nghệ', 'Chi bảo vệ môi trường', 'Chi văn hóa thông tin']
sectmix = mix(cells('B50', 'DỰ TOÁN', ['Chi thường xuyên'] + SPEND_PARTS),
              'Chi thường xuyên', SPEND_PARTS, min_parts=5)

# Plan vs outturn, both columns of the SAME form - no cross-form join to get wrong
pa_plan = cells('B63', 'DỰ TOÁN/TỔNG THU NSNN', ['Thu nội địa'])
pa_act = cells('B63', 'QUYẾT TOÁN/TỔNG THU NSNN', ['Thu nội địa'])
planact = []
for key, v in sorted(pa_plan.items()):
    a = pa_act.get(key, {}).get('Thu nội địa')
    pl = v.get('Thu nội địa')
    if not a or not pl:
        continue
    planact.append(dict(p=key[0], y=key[1], plan=pl, act=a, ratio=round(a / pl, 4)))

# Borrowing and deficit straight off the balance sheet
debt_raw = cells('B46', 'DỰ TOÁN', ['BỘI CHI NSĐP/BỘI THU NSĐP', 'Đ TỔNG MỨC VAY CỦA NSĐP',
                                    'CHI TRẢ NỢ GỐC CỦA NSĐP', 'TỔNG CHI NSĐP'])
debt = [dict(p=prov, y=year, exp=v['TỔNG CHI NSĐP'],
             deficit=v.get('BỘI CHI NSĐP/BỘI THU NSĐP'),
             borrow=v.get('Đ TỔNG MỨC VAY CỦA NSĐP'),
             repay=v.get('CHI TRẢ NỢ GỐC CỦA NSĐP'))
        for prov, (year, v) in sorted(latest_of(debt_raw, ['TỔNG CHI NSĐP']).items())]

# National trend. Provinces reporting per year swing from 6 to 29, so a SUM would read as a
# collapse in 2023 when it is only thinner reporting. The median province is comparable.
tot_raw = cells('B46', 'DỰ TOÁN', ['TỔNG NGUỒN THU NSĐP', 'TỔNG CHI NSĐP'])
peryear = {}
for (prov, year), v in tot_raw.items():
    if v.get('TỔNG NGUỒN THU NSĐP'):
        peryear.setdefault(year, []).append(v['TỔNG NGUỒN THU NSĐP'])
trend = [dict(y=year, med=sorted(vals)[len(vals)//2], n=len(vals),
              lo=sorted(vals)[len(vals)//4], hi=sorted(vals)[len(vals)*3//4])
         for year, vals in sorted(peryear.items()) if len(vals) >= 8]

# ---------------------------------------------------------------- intra-year cash flow
# The finest period this corpus carries is the quarter, and only as a CUMULATIVE
# year-to-date figure: forms B60/B61 publish "Quý I", "6 tháng", "9 tháng" and "Năm" in one
# column, ƯỚC THỰC HIỆN QUÝ. Verified cumulative rather than per-period: 73 of 80 (province,
# year) triples run Q1 <= 6T <= 9T. Differencing gives four quarterly FLOWS per year.
#
# Two things this is NOT, and the page says so: it is not monthly (no month exists anywhere
# in the corpus), and it is not the country (34 provinces' local budgets, no central budget).
CUM = {'Quý': 'c1', '6 tháng': 'c2', '9 tháng': 'c3', 'Năm': 'c4'}


def quarters(form, indicator):
    """Quarterly flows per (province, year), from the cumulative points. Negative quarters are
    a source defect - a cumulative series that falls - and are dropped, never clamped."""
    cum = {}
    for r in q("""SELECT p.name AS prov, pe.year AS y, pe.kind AS k, v.vnd AS vnd
                  FROM v_fact v JOIN fact_row f ON f.rowid = v.id
                  JOIN dim_province p  ON p.id  = f.province_id
                  JOIN dim_period   pe ON pe.id = f.period_id
                  WHERE v.form_code = ? AND v.indicator_raw = ?
                    AND v.series = 'ƯỚC THỰC HIỆN QUÝ' AND v.vnd IS NOT NULL""",
               form, indicator):
        cum.setdefault((r['prov'], r['y']), {})[CUM[r['k']]] = r['vnd']
    flows, dropped = {}, 0
    for key, v in cum.items():
        if len(v) < 4 or v['c4'] <= 0:
            continue
        qs = [v['c1'], v['c2'] - v['c1'], v['c3'] - v['c2'], v['c4'] - v['c3']]
        if min(qs) < 0:
            dropped += 1
            continue
        flows[key] = dict(q=qs, year_total=v['c4'])
    return flows, len(cum), dropped


def profile(flows):
    """Median share of the year landing in each quarter, per year and overall."""
    med = lambda xs: sorted(xs)[len(xs) // 2]
    by_year = {}
    for (prov, year), v in flows.items():
        by_year.setdefault(year, []).append([x / v['year_total'] for x in v['q']])
    rows = [dict(y=year, n=len(s), share=[med([x[i] for x in s]) for i in range(4)])
            for year, s in sorted(by_year.items())]
    alls = [[x / v['year_total'] for x in v['q']] for v in flows.values()]
    return rows, [med([x[i] for x in alls]) for i in range(4)]


REV_FORM, REV_IND = 'B60', 'A TỔNG THU NSNN TRÊN ĐỊA BÀN'
EXP_FORM, EXP_IND = 'B61', 'TỔNG CHI NSĐP'
rev_flows, rev_seen, rev_drop = quarters(REV_FORM, REV_IND)
exp_flows, exp_seen, exp_drop = quarters(EXP_FORM, EXP_IND)
rev_rows, rev_all = profile(rev_flows)
exp_rows, exp_all = profile(exp_flows)
cashflow = dict(
    revenue=dict(form=REV_FORM, indicator=REV_IND, by_year=rev_rows, overall=rev_all,
                 n=len(rev_flows), seen=rev_seen, dropped=rev_drop),
    spending=dict(form=EXP_FORM, indicator=EXP_IND, by_year=exp_rows, overall=exp_all,
                  n=len(exp_flows), seen=exp_seen, dropped=exp_drop),
)

out = dict(
    generated_from='data/nsnn.db',
    kpi=kpi,
    cashflow=cashflow,
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
    revmix=revmix,
    sectmix=sectmix,
    planact=planact,
    debt=debt,
    trend=trend,
)
dest = ROOT / 'dashboard' / 'data.json'
dest.write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')))
print(f"{dest}  {dest.stat().st_size/1024:.0f} KB")
for k, v in kpi.items():
    print(f"  {k:<12} {v:,}")
print(f"  heat cells {len(heat)}, timeline {len(timeline)}, units {len(units)}, methods {len(method)}")
print(f"  B46 province-years {len(budget)}, provinces with a self-sufficiency ratio {len(latest)}")
print(f"  revmix {len(revmix)} · sectmix {len(sectmix)} · planact {len(planact)}"
      f" · debt {len(debt)} · trend {len(trend)}")
print(f"  ambiguous keys dropped: {AMBIG}")
for name, blk in cashflow.items():
    print(f"  cashflow {name:<9} {blk['n']:>3} of {blk['seen']} (province,year) usable, "
          f"{blk['dropped']} dropped for a negative quarter · median share "
          + " ".join(f"Q{i+1} {s*100:.1f}%" for i, s in enumerate(blk['overall'])))
