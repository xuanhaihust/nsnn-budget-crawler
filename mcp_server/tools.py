"""The curated tools. Plain functions returning text, so they are testable without MCP.

Every tool here exists to keep an agent out of one specific trap that this corpus sets.

The trap that matters most: fact_row is a flat projection of hierarchical published forms, so
parents and children are both rows. SUM(vnd) over one province-year of B46 comes to between
3.95x and 6.98x that province's own published TỔNG CHI NSĐP (mean 5.75x over 138 province-
years). There is no aggregate an agent can safely write across this table. So the tools never
sum: they read named cells, or they break a published parent into its direct children and
report the residual so the caller can see whether it reconciled.

Cells are keyed on `indicator_raw` - the label with its outline marker still attached - never
on `indicator`/`clean`. The marker is what distinguishes `1.1 Chi giáo dục` (the investment
line under `I Chi đầu tư phát triển`) from `1 Chi giáo dục` (the recurrent line under
`II Chi thường xuyên`); they differ by 6.1x in Bắc Ninh 2017. Keying on the cleaned label
merges them and makes 1,555 B65 cells ambiguous; keying on the raw label leaves exactly 0
ambiguous on B46, B49, B50, B63, B64 and B65.
"""
import functools, threading

import search, sqlguard, warehouse as w
from warehouse import DASH, flat, fold, money, outline, render

# Names an agent is likely to type that are not the warehouse spelling. Folding handles
# accents, so these are only the genuinely different strings.
ALIAS = {'ha noi': 'Hà Nội', 'hanoi': 'Hà Nội', 'saigon': 'TP Hồ Chí Minh',
         'sai gon': 'TP Hồ Chí Minh', 'ho chi minh': 'TP Hồ Chí Minh',
         'tphcm': 'TP Hồ Chí Minh', 'hcm': 'TP Hồ Chí Minh', 'tp hcm': 'TP Hồ Chí Minh',
         'thua thien hue': 'Huế', 'da nang': 'Đà Nẵng', 'danang': 'Đà Nẵng'}

_LOCAL = threading.local()


def con():
    """One connection per thread.

    The SDK dispatches every sync tool through anyio.to_thread.run_sync, so concurrent calls
    land on different worker threads. A single shared sqlite3 connection opened with
    check_same_thread=False then deadlocks: 6 of 8 trials of two overlapping compare_provinces
    calls hung, both threads stuck inside the same cursor. Connections are cheap (0.23 ms).
    """
    c = getattr(_LOCAL, 'con', None)
    if c is None:
        c = _LOCAL.con = w.connect()
    return c


@functools.lru_cache(maxsize=1)
def province_names():
    return [r['name'] for r in con().execute("SELECT name FROM dim_province ORDER BY name")]


def resolve_province(name):
    """Exact, then folded, then alias. Never difflib.

    difflib answers 'Bac Giang' with 'An Giang' and 'Ha Nam' with 'Hà Nội' - confidently, and
    wrongly. An agent that takes the suggestion gets real rows for the wrong province and never
    notices, so the failure is silent. Listing all 34 names costs ~180 tokens and is terminal.
    """
    names = province_names()
    raw = str(name or '').strip()
    if raw in names:
        return raw
    f = fold(raw)
    for n in names:
        if fold(n) == f:
            return n
    if f in ALIAS:
        return ALIAS[f]
    raise ValueError(
        f"no province named {raw!r}. The warehouse holds exactly these 34, and only under "
        f"their current (post-2025-merger) names:\n  " + ' · '.join(names) +
        "\nA pre-merger name (Bắc Giang, Hà Nam, Quảng Nam, Vĩnh Phúc, Bà Rịa - Vũng Tàu …) "
        "cannot be resolved here: the warehouse does not store the historical source name, so "
        "which current province absorbed it is not something this server can tell you.")


# --- the two primitives --------------------------------------------------------------------

def cells(form_code, indicator, series=None, province=None, year=None):
    """Named cells keyed on (province, year, series, indicator_raw), with COUNT(*) alongside.

    The count travels with the value so a key published twice is reported as ambiguous rather
    than silently collapsed to whichever row came last.
    """
    sql = """SELECT province, year, series, unit, COUNT(*) AS n,
                    MIN(vnd) AS lo, MAX(vnd) AS hi, MIN(id) AS id
             FROM v_fact WHERE form_code = ? AND indicator_raw = ? AND vnd IS NOT NULL"""
    args = [form_code, indicator]
    for col, val in (('series', series), ('province', province), ('year', year)):
        if val is not None:
            sql += f" AND {col} = ?"
            args.append(val)
    sql += " GROUP BY province, year, series ORDER BY province, year"
    return [dict(r) for r in con().execute(sql, args)]


def series_for(form_code, indicator, province=None):
    """Which series columns publish this cell, commonest first."""
    sql = """SELECT series, COUNT(*) AS n, COUNT(DISTINCT province) AS nprov
             FROM v_fact WHERE form_code = ? AND indicator_raw = ? AND vnd IS NOT NULL"""
    args = [form_code, indicator]
    if province:
        sql += " AND province = ?"
        args.append(province)
    sql += " GROUP BY series ORDER BY nprov DESC, n DESC"
    return [dict(r) for r in con().execute(sql, args)]


def children(province, year, form_code, series, parent):
    """The DIRECT children of a published parent row, scoped positionally within its document.

    Returns (parent_row, children, rows_scanned) or (None, [], 0) when the parent is not found,
    and raises ValueError when the parent's own outline level cannot be read.

    v_fact.id is fact_row.rowid, which is source row order, so the children of a parent are the
    rows after it up to the next row at its level or shallower. Positional scoping is not
    optional: arabic numbering restarts under every roman section, so a flat marker filter
    mixes the children of `I` with those of `II` and the residual goes to -5,366,746,000,000.

    Three things bound the scan:

    * The parent's OWN report and table. 4,016 of 83,559 (province, year, form, series) blocks
      span more than one report, because two reports can publish the same form code and a
      same-named series column in the same year. Without this the scan runs off the end of one
      document into another and attributes its rows to the parent.
    * A parent whose marker cannot be parsed is refused outright. 19,433 distinct labels carry
      no marker - including every headline total - and treating "unknown" as "shallower than
      everything" made the scan swallow whole sibling sections and still report a reconciliation.
    * Only DIRECT children are summed. Dash rows under a numbered line are its parts, not its
      siblings - including them turned a -2 triệu residual into a 2.3 trillion overstatement.
    """
    loc = con().execute(
        """SELECT id, report_id, table_label FROM v_fact
           WHERE province = ? AND year = ? AND form_code = ? AND series = ?
             AND indicator_raw = ? ORDER BY id LIMIT 1""",
        (province, int(year), form_code, series, parent)).fetchone()
    if loc is None:
        return None, [], 0
    rows = [dict(r) for r in con().execute(
        """SELECT id, indicator_raw, vnd, value, unit, raw FROM v_fact
           WHERE province = ? AND year = ? AND form_code = ? AND series = ?
             AND report_id = ? AND table_label = ? ORDER BY id""",
        (province, int(year), form_code, series, loc['report_id'], loc['table_label']))]
    lettered = w.i_is_section(r['indicator_raw'] for r in rows)
    idx = next((i for i, r in enumerate(rows) if r['id'] == loc['id']), None)
    _, plevel = outline(parent, lettered)
    if plevel is None:
        raise ValueError(
            f"{flat(parent, 70)!r} carries no outline marker, so its children cannot be "
            "identified by position and this tool will not guess at them. Headline totals are "
            "usually unmarked; break down the marked section rows beneath it instead - "
            "nsnn_run_sql with ORDER BY id over this form will show the rows in source order.")
    kids, scanned = [], 0
    for r in rows[idx + 1:]:
        _, lvl = outline(r['indicator_raw'], lettered)
        if lvl is not None and lvl <= plevel:
            break
        scanned += 1
        if lvl is not None and lvl > plevel:
            kids.append((lvl, r))
    if not kids:
        return rows[idx], [], scanned
    top = min(lvl for lvl, _ in kids)               # the shallowest rank actually present
    return rows[idx], [r for lvl, r in kids if lvl == top], scanned


def coverage(rows, key='province'):
    """Always say how much of the country a cross-province answer actually covers."""
    have = {r[key] for r in rows}
    missing = [n for n in province_names() if n not in have]
    line = f"coverage: {len(have)}/34 provinces"
    if missing:
        line += f"; no value for: {', '.join(missing)}"
    return line


def money_rows(rows, label_key='indicator_raw'):
    """Render value rows with the one money rule, and never turn a blank into 0."""
    out = []
    for r in rows:
        vnd = r.get('vnd')
        flag = w.confidence(vnd, r.get('series'), r.get('unit'))
        out.append({label_key: flat(r.get(label_key), 70), 'ty': money(vnd),
                    'flag': flag, 'id': r.get('id')})
    return out


# --- orientation ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def describe_corpus():
    """START HERE. What is in the warehouse, and the four things that will otherwise go wrong."""
    c = con()
    one = lambda s: c.execute(s).fetchone()[0]
    rows = one("SELECT COUNT(*) FROM fact_row")
    vnd = one("""SELECT COUNT(*) FROM fact_row f JOIN dim_unit u ON u.id = f.unit_id
                 WHERE f.value IS NOT NULL AND u.factor > 0""")
    blank = one("SELECT COUNT(*) FROM fact_row WHERE value IS NULL")
    reps = one("SELECT COUNT(*) FROM dim_report")
    pend = one("SELECT COUNT(*) FROM dim_report WHERE rows = 0")
    y0, y1 = c.execute("SELECT MIN(year), MAX(year) FROM dim_period WHERE year IS NOT NULL").fetchone()
    scopes = [f"{r['label']} ({r['n']:,})" for r in c.execute(
        """SELECT s.label, COUNT(*) AS n FROM fact_row f JOIN dim_scope s ON s.id = f.scope_id
           GROUP BY s.label ORDER BY n DESC""")]
    return f"""Vietnam provincial budget disclosures, crawled from the Ministry of Finance CKNS
portal (ckns.mof.gov.vn) for the 34 current provinces. {rows:,} rows from {reps:,} reports,
{y0}-{y1}. {vnd:,} rows carry a VND figure; {blank:,} have no value at all.

FOUR THINGS THAT WILL OTHERWISE GO WRONG

1. NEVER SUM ROWS. The table is a flat projection of hierarchical forms, so a parent and its
   children are both rows. SUM(vnd) over one province-year of form B46 comes to 3.95x-6.98x
   that province's own published total (mean 5.75x, n=138). Read a named cell, or use
   nsnn_break_down, which returns the published parent, its direct children and the residual.

2. A BLANK IS NOT A ZERO. {blank:,} rows have no value because the source cell was empty or
   held '-'. They are returned empty, never as 0. Separately, all 3,916 rows of Bắc Ninh's
   2023 'Dự toán HĐND quyết định' scope carry no VND at all because the source declared no
   unit - 582 of them are blank as well. Absent is not zero, in either sense.

3. SCOPE IS A STAGE, NOT A CATEGORY. The same figure exists as a proposal, as an approved
   plan, and as settled accounts, and they are different numbers. Comparing a dự toán in one
   province with a quyết toán in another is the commonest wrong answer this corpus produces.
   Scopes: {' · '.join(scopes)}.

4. COVERAGE IS UNEVEN AND SILENT. No indicator reaches all 34 provinces in any single year;
   the best peak around 28-32 and collapse in thin years. Every comparison tool here prints
   its coverage. A ranking without one is a ranking of whoever happened to publish.

SCOPE LIMIT: this is the Ministry layer only - provincial aggregate forms. It does NOT include
agency-level disclosures on province portals, so a province is never 'complete' on this data
alone. {pend:,} reports produced no rows and are listed by nsnn_list_data_quality. Every query
also stops about 50 records short of the portal's own declared total; that gap is unclosed.

MONEY: figures are reported in tỷ đồng (1e9 VND) unless a column says otherwise. Rows whose
unit is a percentage or is unknown have no VND value and are never converted.

NEXT: nsnn_find_indicators to get an exact label, then a read tool. Labels are verbatim
Vietnamese and cannot be guessed."""


def list_provinces():
    """The 34 provinces with what each actually published."""
    rows = [dict(r) for r in con().execute(
        """SELECT p.name AS province, COUNT(f.rowid) AS rows_n,
                  (SELECT COUNT(*) FROM dim_report r WHERE r.province_id = p.id) AS reports,
                  (SELECT COUNT(*) FROM dim_report r WHERE r.province_id = p.id AND r.rows = 0)
                    AS no_rows,
                  MIN(pe.year) AS y0, MAX(pe.year) AS y1
           FROM dim_province p
           LEFT JOIN fact_row f ON f.province_id = p.id
           LEFT JOIN dim_period pe ON pe.id = f.period_id
           GROUP BY p.id ORDER BY rows_n DESC""")]
    return render(rows, note=f"{len(rows)} provinces · rows_n counts fact rows, not values; "
                             "no_rows counts reports that produced nothing (see "
                             "nsnn_list_data_quality)")


def list_forms(min_provinces=25, limit=30):
    """Forms published widely enough that a cross-province comparison means something."""
    rows = [dict(r) for r in con().execute(
        """SELECT t.form_code, COUNT(DISTINCT f.province_id) AS nprov, COUNT(*) AS rows_n,
                  MIN(pe.year) AS y0, MAX(pe.year) AS y1,
                  MIN(t.label) AS example_title
           FROM fact_row f JOIN dim_table t ON t.id = f.table_id
           JOIN dim_period pe ON pe.id = f.period_id
           WHERE t.form_code <> ''
           GROUP BY t.form_code HAVING nprov >= ?
           ORDER BY nprov DESC, rows_n DESC LIMIT ?""", (int(min_provinces), int(limit)))]
    for r in rows:
        r['example_title'] = flat(r['example_title'], 58)
    return render(rows, note=(
        f"{len(rows)} forms published by >= {min_provinces} provinces. The reliable ones for "
        "comparison: B46 balance sheet (dự toán) · B63 revenue (quyết toán) · B64/B65 spending "
        "(quyết toán) · B48/B49 (dự toán) · B50 provincial-tier spend by sector. Forms with "
        "huge row counts but few shared indicators (B51, B53, B67) list agencies and projects "
        "whose names differ per province and do not compare."))


def find_indicators(query, form_code=None, min_provinces=1, limit=15):
    """Find exact indicator labels. Diacritics optional."""
    hits = search.indicators(query, form_code=form_code,
                             min_provinces=int(min_provinces), limit=int(limit))
    if not hits:
        return (f"nothing matches {query!r}"
                + (f" in form {form_code}" if form_code else '')
                + f" with at least {min_provinces} provinces.\nTry fewer words, or drop "
                  "min_provinces. Search is diacritic-insensitive, so 'giao duc' and 'giáo "
                  "dục' are the same query, but it does not translate English - use Vietnamese "
                  "terms (chi = spending, thu = revenue, dự toán = plan, quyết toán = settled).")
    rows = [{'indicator': flat(h['indicator'], 74), 'form_code': h['form_code'],
             'nprov': h['nprov'], 'rows': h['nrows'], 'with_vnd': h['nvnd'],
             'years': f"{h['y0']}-{h['y1']}"} for h in hits]
    return render(rows, hoist=False, note=(
        "Copy `indicator` EXACTLY into the read tools - matching is literal, and the leading "
        "outline marker is part of the key. Two labels differing only by marker are DIFFERENT "
        "lines: '1.1 Chi giáo dục' is the investment line, '1 Chi giáo dục' the recurrent one. "
        "Prefer high nprov for comparisons. Then call nsnn_read_timeseries, which will list the "
        "available series if you do not name one."))


def find_forms(query, limit=10):
    """Find a form by its title rather than its code."""
    hits = search.forms(query, limit=int(limit))
    if not hits:
        return f"no form title matches {query!r}"
    rows = [{'form_code': h['code'] or '(none)', 'title': flat(h['label'], 62),
             'nprov': h['nprov'], 'rows': h['nrows']} for h in hits]
    return render(rows, hoist=False, note="Codes are not unique across families: B62 and "
                                          "62/CK-NSNN are different forms, not the same one.")


# --- reading values ------------------------------------------------------------------------

def _pick_series(form_code, indicator, series, province=None):
    """Resolve the series, or raise with the actual choices. Never guess between two."""
    opts = series_for(form_code, indicator, province)
    if not opts:
        raise ValueError(
            f"no VND values for {indicator!r} on form {form_code}"
            + (f" in {province}" if province else '')
            + ". Check the label with nsnn_find_indicators - the outline marker is part of it. "
              "A published row with no VND value is also possible: percentage and unitless "
              "rows correctly have none.")
    if series:
        if any(o['series'] == series for o in opts):
            return series
        raise ValueError(f"series {series!r} does not publish {indicator!r} on {form_code}. "
                         "Available: " + ' | '.join(o['series'] for o in opts[:8]))
    if len(opts) == 1:
        return opts[0]['series']
    raise ValueError(
        f"{indicator!r} on {form_code} is published in {len(opts)} different series columns"
        + (f" for {province}" if province else '')
        + ", which are different numbers - naming one is required. Pass series= as one of:\n  "
        + '\n  '.join(f"{o['series']}  ({o['n']} values"
                      + (f", {o['nprov']} provinces" if not province else '') + ')'
                      for o in opts[:8])
        + "\nDỰ TOÁN is the plan, QUYẾT TOÁN the settled accounts; TỔNG THU NSNN is total "
          "state revenue collected in the province, THU NSĐP only the part the province keeps.")


def read_timeseries(province, form_code, indicator, series=None):
    """One named cell of one named form, for one province, across every year it was published."""
    province = resolve_province(province)
    series = _pick_series(form_code, indicator, series, province)
    rows = cells(form_code, indicator, series, province=province)
    if not rows:
        return f"no values for {indicator!r} on {form_code} / {series} in {province}"
    out = []
    for r in rows:
        amb = r['n'] > 1 and r['lo'] != r['hi']
        out.append({'year': r['year'],
                    'ty': '' if amb else money(r['lo']),
                    'flag': 'AMBIGUOUS' if amb else w.confidence(r['lo'], series, r['unit']),
                    'n_cells': r['n'],
                    'range': f"{money(r['lo'])}..{money(r['hi'])}" if amb else ''})
    note = (f"{province} · {form_code} · {series} · {flat(indicator, 70)} · tỷ đồng. "
            "One published cell per year - nothing is summed here.")
    if any(o['flag'] == 'AMBIGUOUS' for o in out):
        note += (" AMBIGUOUS years are published more than once with different values; the "
                 "range shows both. They are not averaged or picked between.")
    return render(out, note=note)


def compare_provinces(form_code, indicator, series=None, year=None):
    """The same named cell across provinces for one year, with explicit coverage."""
    series = _pick_series(form_code, indicator, series)
    if year is None:
        years = {}
        for r in cells(form_code, indicator, series):
            years.setdefault(r['year'], set()).add(r['province'])
        if not years:
            return f"no values for {indicator!r} on {form_code} / {series}"
        year = max(years, key=lambda y: (len(years[y]), y))
    rows = cells(form_code, indicator, series, year=year)
    if not rows:
        return f"no values for {indicator!r} on {form_code} / {series} in {year}"
    vals = sorted(r['lo'] for r in rows if r['lo'] and r['lo'] > 0)
    med = vals[len(vals) // 2] if vals else 0
    out, odd = [], 0
    for r in sorted(rows, key=lambda r: -(r['hi'] or 0)):
        amb = r['n'] > 1 and r['lo'] != r['hi']
        # No province's budget is a hundredth of the median province's, so a value that small
        # is a source defect, not a small province. Flagged against the CROSS-province median
        # because a province with more bad years than good has a bad median of its own.
        low = med and r['lo'] and r['lo'] < med / 100
        odd += bool(low)
        out.append({'province': r['province'],
                    'ty': '' if amb else money(r['lo']),
                    'flag': ('AMBIGUOUS' if amb else 'MAGNITUDE?' if low
                             else w.confidence(r['lo'], series, r['unit'])),
                    'n_cells': r['n']})
    return render(out, note=(
        f"{form_code} · {series} · {flat(indicator, 70)} · {year} · tỷ đồng, largest first.\n"
        f"{coverage(rows)}\nThese are single published cells of one form, so they are "
        "comparable. A province missing here did not publish this cell in this year - it is "
        "not a zero. Pass year= to compare a different one."
        + (f"\n{odd} value(s) marked MAGNITUDE? are more than 100x below the median province "
           "and are almost certainly a source unit/separator defect, not a small budget. They "
           "are shown as published, not corrected - see nsnn_list_data_quality(block="
           "'magnitude')." if odd else '')))


B46 = {'revenue': 'A TỔNG NGUỒN THU NSĐP', 'spend': 'B TỔNG CHI NSĐP',
       'own': 'I Thu NSĐP được hưởng theo phân cấp', 'central': 'II Thu bổ sung từ NSTW',
       'investment': '1 Chi đầu tư phát triển', 'recurrent': '2 Chi thường xuyên',
       'borrowing': 'Đ TỔNG MỨC VAY CỦA NSĐP'}


def read_balance_sheet(year=None, province=None):
    """Form B46, the provincial balance sheet: the one form where ranking provinces is sound."""
    if province:
        province = resolve_province(province)
    got = {}
    for key, ind in B46.items():
        for r in cells('B46', ind, 'DỰ TOÁN', province=province, year=year):
            if r['n'] == 1 or r['lo'] == r['hi']:
                got.setdefault((r['province'], r['year']), {})[key] = r['lo']
    if not got:
        return ("no B46 balance-sheet cells for that selection. B46 covers 32 provinces, "
                "mostly 2017-2023; try omitting year.")
    if year is None and province is None:          # one headline row per province: latest year
        best = {}
        for (p, y), v in got.items():
            if 'revenue' in v and 'own' in v and (p not in best or y > best[p][0]):
                best[p] = (y, v)
        got = {(p, y): v for p, (y, v) in best.items()}
    # Same magnitude check compare_provinces applies. Without it the separator-defect cells
    # enter a national ranking unflagged, and their ratios look perfectly ordinary: Đồng Nai
    # 2021 reads 0.029 tỷ of revenue with a self-sufficiency of 0.679.
    med = {k: _cell_medians('B46', ind, 'DỰ TOÁN') for k, ind in B46.items()}
    rows = []
    for (p, y), v in sorted(got.items(), key=lambda kv: -(kv[1].get('revenue') or 0)):
        bad = {k for k in v if magnitude_flag(v[k], med[k].get(p))}
        ratio = lambda a, b: ('' if not (v.get(a) and v.get(b)) else
                              '?' if bad & {a, b} else f"{v[a]/v[b]:.3f}")
        rows.append({
            'province': p, 'year': y,
            'revenue': money(v.get('revenue')), 'spend': money(v.get('spend')),
            'own': money(v.get('own')), 'central': money(v.get('central')),
            'investment': money(v.get('investment')), 'recurrent': money(v.get('recurrent')),
            'self_suff': ratio('own', 'revenue'), 'inv_share': ratio('investment', 'spend'),
            'flag': 'MAGNITUDE?' if bad else ''})
    return render(rows, note=(
        "Form B46 'Cân đối ngân sách địa phương', series DỰ TOÁN · tỷ đồng.\n"
        "THESE ARE PLANS (dự toán), not settled accounts - the outturn differs.\n"
        "self_suff = own / revenue; inv_share = investment / spend. Both divide two published "
        "cells, so neither sums line items; a ratio shows '?' when either operand is flagged.\n"
        "Rows marked MAGNITUDE? hold a figure 100x or more away from that cell's own history in "
        "this province - a source defect, shown as published and not corrected. See "
        "nsnn_list_data_quality(block='magnitude').\n" + coverage(
            [{'province': p} for p, _ in got])))


def _cell_medians(form_code, indicator, series):
    """Each province's own median for this cell across years, for the magnitude check below."""
    out = {}
    for r in con().execute(
            """SELECT province, vnd FROM v_fact WHERE form_code = ? AND indicator_raw = ?
               AND series = ? AND vnd IS NOT NULL AND vnd > 0""",
            (form_code, indicator, series)):
        out.setdefault(r['province'], []).append(r['vnd'])
    return {p: sorted(v)[len(v) // 2] for p, v in out.items() if len(v) >= 3}


def magnitude_flag(vnd, median):
    """Flag a value far from its own cell's history. Reported, never corrected.

    757 values across 25 provinces sit 100x or further from the median of the same cell in the
    same province in other years, and they pass every unit check because the declared unit is
    right. The commonest shape is a thousands separator read as a decimal point: Đồng Nai's
    2021 B46 total is published as '28.709234' where 2020 and 2022 publish '29106050' and
    '23556345'. Which reading the source intended is not something this server can decide, so
    it says the figure is out of line and leaves it alone.
    """
    if not vnd or not median or median <= 0:
        return ''
    r = vnd / median
    return 'MAGNITUDE?' if r < 0.01 or r > 100 else ''


# --- breakdowns: the alternative to summing --------------------------------------------------

def break_down(province, year, form_code, indicator, series=None):
    """Split a published parent into its direct children and reconcile. Use this, never SUM."""
    province = resolve_province(province)
    series = _pick_series(form_code, indicator, series, province)
    parent, kids, scanned = children(province, int(year), form_code, series, indicator)
    if parent is None:
        return (f"{indicator!r} is not a row of {form_code} / {series} for {province} {year}. "
                "The label must match exactly, outline marker included.")
    if not kids:
        return (f"{flat(indicator, 70)} is a leaf on this form for {province} {year} - it has "
                f"no child rows beneath it. Published value: {money(parent['vnd'])} tỷ đồng.")
    rows, total, blanks = [], 0.0, 0
    for k in kids:
        if k['vnd'] is None:
            blanks += 1
        else:
            total += k['vnd']
        rows.append({'indicator': flat(k['indicator_raw'], 66), 'ty': money(k['vnd']),
                     'share': f"{k['vnd']/parent['vnd']:.3f}"
                              if k['vnd'] and parent['vnd'] else '', 'id': k['id']})
    pub = parent['vnd']
    resid = (pub - total) if pub is not None else None
    # Three states, not two. `pub` of 0.0 is falsy, so a truthiness test reported "NO" on
    # 18,679 breakdowns whose residual was exactly zero; and a parent that published nothing
    # has nothing to reconcile against at all.
    if pub is None:
        ok = None
    elif pub == 0:
        ok = (total == 0)
    else:
        ok = abs(resid) <= abs(pub) * 0.005
    note = (f"{province} {year} · {form_code} · {series} · tỷ đồng\n"
            f"published parent  {flat(indicator, 60)} = {money(pub)}\n"
            f"children sum      {len(kids)} direct children = {money(total)}\n"
            f"residual          {money(resid)}"
            + (f"  ({resid/pub*100:+.3f}% of parent)" if pub else '')
            + "\nreconciles: " + ('YES' if ok else
                                      'N/A - the parent published no value' if ok is None else 'NO'))
    if ok is False:
        note += ("  <- the children do not add up to the published parent. Do NOT present the "
                 "sum as the total; report the published parent and say the split is partial.")
    if blanks:
        note += f"\n{blanks} child rows have no published value (blank, not zero)."
    note += (f"\n{scanned} rows sit under this parent in the source; only the {len(kids)} at "
             "one level deeper are summed. The rest are their sub-items - adding those too is "
             "the double count this tool exists to prevent.")
    return render(rows, hoist=False, note=note)


def read_revenue_mix(province, year=None, basis='quyết toán'):
    """Where a province's domestic revenue comes from (form B63)."""
    province = resolve_province(province)
    series = ('QUYẾT TOÁN/TỔNG THU NSNN' if 'quyết' in fold(basis).replace('quyet', 'quyết')
              or 'quyet' in fold(basis) else 'DỰ TOÁN/TỔNG THU NSNN')
    if year is None:
        yr = con().execute(
            """SELECT MAX(year) FROM v_fact WHERE province = ? AND form_code = 'B63'
               AND series = ? AND indicator_raw = 'I Thu nội địa' AND vnd IS NOT NULL""",
            (province, series)).fetchone()[0]
        if yr is None:
            return f"{province} publishes no B63 'I Thu nội địa' on the {basis} basis."
        year = yr
    return break_down(province, year, 'B63', 'I Thu nội địa', series)


def read_spending_by_sector(province, year=None, basis='dự toán'):
    """Recurrent spending by sector - PROVINCIAL TIER ONLY (forms B50 / B65)."""
    province = resolve_province(province)
    f = fold(basis)
    form, series = (('B65', 'QUYẾT TOÁN') if 'quyet' in f else ('B50', 'DỰ TOÁN'))
    if year is None:
        yr = con().execute(
            """SELECT MAX(year) FROM v_fact WHERE province = ? AND form_code = ?
               AND indicator_raw = 'II Chi thường xuyên' AND vnd IS NOT NULL""",
            (province, form)).fetchone()[0]
        if yr is None:
            return f"{province} publishes no {form} 'II Chi thường xuyên'."
        year = yr
    body = break_down(province, year, form, 'II Chi thường xuyên', series)
    split = cells(form, 'A CHI BỔ SUNG CÂN ĐỐI CHO NGÂN SÁCH HUYỆN', series,
                  province=province, year=int(year))
    tier = cells(form, 'B CHI NGÂN SÁCH CẤP TỈNH THEO LĨNH VỰC', series,
                 province=province, year=int(year))
    warn = ("\nTIER WARNING: form " + form + " is 'chi ngân sách CẤP TỈNH theo lĩnh vực' - the "
            "PROVINCIAL tier only. Section A is the block transfer to districts and is not "
            "broken down by sector anywhere in this corpus.")
    if split or tier:
        warn += ("\n  A transfer to districts = " + (money(split[0]['lo']) if split else '?')
                 + " tỷ\n  B provincial tier      = " + (money(tier[0]['lo']) if tier else '?')
                 + " tỷ\nA sector share below is a share of B, NOT of the province's total "
                   "spending. Reporting it as province-wide roughly halves the real figure.")
    return body + warn


def compare_plan_vs_outturn(province=None, year=None, indicator='I Thu nội địa'):
    """Plan against settled accounts - both columns of form B63, so no cross-form join."""
    if province:
        province = resolve_province(province)
    plan = {(r['province'], r['year']): r for r in
            cells('B63', indicator, 'DỰ TOÁN/TỔNG THU NSNN', province=province, year=year)
            if r['n'] == 1}
    act = {(r['province'], r['year']): r for r in
           cells('B63', indicator, 'QUYẾT TOÁN/TỔNG THU NSNN', province=province, year=year)
           if r['n'] == 1}
    rows = []
    for key in sorted(plan.keys() & act.keys()):
        p, a = plan[key]['lo'], act[key]['lo']
        if not p or not a:
            continue
        rows.append({'province': key[0], 'year': key[1], 'plan_ty': money(p),
                     'outturn_ty': money(a), 'ratio': f"{a/p:.3f}"})
    if not rows:
        return f"no B63 row has both a DỰ TOÁN and a QUYẾT TOÁN value for {indicator!r} there."
    rows.sort(key=lambda r: -float(r['ratio']))
    over = sum(1 for r in rows if float(r['ratio']) > 1)
    return render(rows, hoist=False, note=(
        f"Form B63 · {flat(indicator, 60)} · tỷ đồng. ratio = outturn / plan.\n"
        f"{over} of {len(rows)} province-years came in above plan. Both figures are single "
        "cells of the SAME report, so this is not a cross-form comparison."))


# --- honesty surface -------------------------------------------------------------------------

def list_data_quality(province=None, block='pending', limit=25):
    """What this corpus does NOT reliably tell you. Four blocks; every one is measured."""
    if province:
        province = resolve_province(province)
    b = fold(block)
    if b.startswith('pend'):
        sql = """SELECT p.name AS province, r.method AS why, r.rows, r.formats,
                        substr(r.title, 1, 62) AS title, r.reason
                 FROM dim_report r JOIN dim_province p ON p.id = r.province_id
                 WHERE r.rows = 0"""
        args = []
        if province:
            sql += " AND p.name = ?"
            args.append(province)
        sql += " ORDER BY p.name LIMIT ?"
        args.append(int(limit))
        rows = [dict(r) for r in con().execute(sql, args)]
        tot = con().execute(
            "SELECT COUNT(*) FROM dim_report r JOIN dim_province p ON p.id=r.province_id "
            "WHERE r.rows=0" + (" AND p.name=?" if province else ''),
            ([province] if province else [])).fetchone()[0]
        return render(rows, note=(
            f"{tot} reports produced no rows{' in ' + province if province else ''}; "
            f"showing {len(rows)}.\nPENDING_PDF_DOC = published only as PDF/DOC, which the "
            "pipeline does not parse (scanned images with a poor OCR layer; guessing at them "
            "would break the never-infer rule). PARSE_FAILED = a machine-readable file was "
            "read without error but yielded no table rows. PENDING_NO_FILE = the portal lists "
            "the report with no attachment at all. None of these are dropped silently - this "
            "is the list."))
    if b.startswith('magn'):
        rows = []
        for form, ind, ser in (('B46', 'B TỔNG CHI NSĐP', 'DỰ TOÁN'),
                               ('B46', 'A TỔNG NGUỒN THU NSĐP', 'DỰ TOÁN'),
                               ('B63', 'I Thu nội địa', 'QUYẾT TOÁN/TỔNG THU NSNN')):
            hist = {}
            for r in cells(form, ind, ser, province=province):
                if r['lo'] and r['lo'] > 0:
                    hist.setdefault(r['province'], []).append((r['year'], r['lo']))
            for prov, ys in sorted(hist.items()):
                vals = [v for _, v in ys]
                if len(vals) < 2 or max(vals) < min(vals) * 100:
                    continue
                rows.append({'province': prov, 'form': form, 'indicator': flat(ind, 34),
                             'spread': f"{max(vals)/min(vals):.0f}x",
                             'by_year': ' '.join(f"{y}:{money(v)}" for y, v in sorted(ys))})
        return render(rows[:int(limit)], hoist=False, note=(
            f"{len(rows)} headline cells whose own published history spans 100x or more between "
            "its largest and smallest year. Across the seven main forms, 757 individual values "
            "in 25 provinces sit 100x or further from their cell's median.\n"
            "THE WHOLE SERIES IS SHOWN because the defect cannot be localised from here. The "
            "commonest shape is a thousands separator read as a decimal point - Đồng Nai's B46 "
            "publishes '28.709234' in 2021 and '29106050' in 2020 - but in Đồng Nai's case MORE "
            "years are affected than not, so even that cell's own median is one of the bad "
            "values. Which reading each year intended is not decidable here. Nothing is "
            "corrected; judge the series yourself, or use nsnn_trace_source to see the raw "
            "cell."))
    if b.startswith('unit') or b.startswith('not'):
        rows = [dict(r) for r in con().execute(
            """SELECT u.label AS unit, u.factor, COUNT(*) AS n FROM fact_row f
               JOIN dim_unit u ON u.id = f.unit_id
               WHERE u.factor = 0 AND u.label <> '' GROUP BY u.label
               ORDER BY n DESC LIMIT ?""", (int(limit),))]
        return render(rows, hoist=False, note=(
            "Units with factor 0 get NO VND value, deliberately. Percentages and unitless "
            "tables correctly have none. The misspelled currency units here (Triệu dồng, Tiệu "
            "đồng, Tr đồng, Triệu đổng - 660 rows) are left unconverted rather than guessed at; "
            "normalising an obvious typo is the data owner's call, not this server's."))
    if b.startswith('cover'):
        rows = [dict(r) for r in con().execute(
            """SELECT pe.year, COUNT(DISTINCT f.province_id) AS provinces, COUNT(*) AS rows_n
               FROM fact_row f JOIN dim_period pe ON pe.id = f.period_id
               WHERE pe.year IS NOT NULL GROUP BY pe.year ORDER BY pe.year""")]
        return render(rows, hoist=False, note=(
            "Reporting thins out sharply in recent years, so a 'national' figure for 2023-2025 "
            "rests on far fewer provinces than one for 2018-2021. Always read coverage before "
            "reading a trend."))
    return ("block must be one of: pending (reports that produced nothing) · magnitude (values "
            "far from their own history) · units (unconverted units) · coverage (provinces per "
            "year)")


def trace_source(province, form_code, indicator, year, series=None):
    """The evidence chain for one number: the untouched source cell and the report it came from."""
    province = resolve_province(province)
    series = _pick_series(form_code, indicator, series, province)
    rows = [dict(r) for r in con().execute(
        """SELECT v.id, v.province, v.period, v.scope, v.table_label, v.series, v.indicator_raw,
                  v.raw, v.value, v.unit, v.vnd, v.report_id,
                  r.title, r.method, r.formats, r.doc_date
           FROM v_fact v LEFT JOIN dim_report r ON r.report_id = v.report_id
           WHERE v.province = ? AND v.form_code = ? AND v.indicator_raw = ?
             AND v.series = ? AND v.year = ? ORDER BY v.id""",
        (province, form_code, indicator, series, int(year)))]
    if not rows:
        return f"no row for that combination in {province} {year}."
    out = []
    for r in rows:
        out.append({'id': r['id'], 'source_cell': r['raw'], 'value': r['value'],
                    'unit': r['unit'], 'ty': money(r['vnd']),
                    'report_id': r['report_id'], 'method': r['method'],
                    'formats': r['formats'], 'title': flat(r['title'], 58)})
    return render(out, hoist=False, note=(
        f"{province} · {rows[0]['period']} · {rows[0]['scope']} · {flat(rows[0]['table_label'], 60)}\n"
        "`source_cell` is the untouched text of the published cell; `value` is it parsed; `ty` "
        "is value x the unit factor, filled only when the unit is known.\n"
        "CITATION: province + CKNS report_id + title. The warehouse stores no document number "
        "and no source URL for any of its 8,660 reports, so neither is available here - do not "
        "construct one. The downloaded file itself is under work/<province>/raw/ with a "
        "SHA-256, in a full checkout."))


def run_sql(sql, max_rows=200):
    """The escape hatch: one read-only SELECT over the warehouse."""
    cols, rows, truncated, ms = sqlguard.run(sql, max_rows=max_rows)
    if not rows:
        return f"0 rows ({ms}ms)"
    body = render([dict(zip(cols, r)) for r in rows], columns=cols, hoist=False)
    note = f"{len(rows)} rows, {ms}ms"
    if truncated:
        note += (f" — TRUNCATED at {len(rows)}. This is the head of the result in source order, "
                 "NOT a sample: do not compute a ratio or a total over it. Add a WHERE filter, "
                 "GROUP BY, or your own LIMIT/OFFSET. There is no cursor.")
    return body + '\n' + note
