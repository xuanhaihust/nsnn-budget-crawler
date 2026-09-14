"""Diacritic-folded full-text search over indicator and form labels.

An agent cannot guess a label out of 153,272 verbatim Vietnamese strings, so search is the
entry point to almost every other tool. Three findings shape it, all measured:

* FTS5's own `remove_diacritics` is not usable for Vietnamese. `=1` is half-broken (it folds
  giáo→giao but leaves tế and đất alone) and even `=2` never folds đ→d, which appears in 43.6%
  of labels. So the folding happens in Python (`warehouse.fold`) and FTS5 indexes the already
  folded text with `remove_diacritics 0`.

* Ranking by bm25 is wrong here. 93.3% of distinct labels are one-province project lines and
  bm25 rewards short documents, so for "chi giáo dục" it puts a 1-province/31-row label above
  the 34-province/9,748-row national line. Rank by province breadth, then row count.

* The grain is (indicator, form), not (indicator, form, series): there are 276,813 of the
  first and 2,692,319 of the second. Series is resolved later, by the read tools, which report
  the choices rather than picking one.

The index is derived data, so it lives in its own file and is rebuilt rather than committed.
It never touches data/nsnn.db, which every other connection opens read-only.
"""
import sqlite3, time

from warehouse import ROOT, connect, fold

INDEX = ROOT / 'data' / 'nsnn-search.db'

SCHEMA = """
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
CREATE TABLE ind (key INTEGER PRIMARY KEY, indicator TEXT, clean TEXT, form_code TEXT,
                  nrows INTEGER, nprov INTEGER, nvnd INTEGER, y0 INTEGER, y1 INTEGER,
                  folded TEXT);
CREATE TABLE frm (code TEXT, label TEXT, nrows INTEGER, nprov INTEGER, folded TEXT);
CREATE VIRTUAL TABLE ind_fts USING fts5(folded, content='ind', content_rowid='key',
                                        tokenize='unicode61 remove_diacritics 0');
CREATE VIRTUAL TABLE frm_fts USING fts5(folded, tokenize='unicode61 remove_diacritics 0');
CREATE INDEX ix_ind_form ON ind(form_code);
"""


def build(verbose=True):
    """Rebuild the index from the warehouse. ~30s; run once, then reuse."""
    t0 = time.time()
    src = connect()
    ind = {r['id']: (r['label'], r['clean']) for r in src.execute(
        "SELECT id, label, clean FROM dim_indicator")}
    tab = {r['id']: (r['label'], r['form_code']) for r in src.execute(
        "SELECT id, label, form_code FROM dim_table")}

    INDEX.unlink(missing_ok=True)
    dst = sqlite3.connect(INDEX)
    dst.executescript(SCHEMA)

    # Aggregate on integer ids only. Grouping 3.8M rows on text columns exhausts sqlite's
    # temp space; on ids it is a 3s indexed pass.
    rows, key = [], 0
    for r in src.execute("""
            SELECT f.indicator_id AS i, f.table_id AS t, COUNT(*) AS n,
                   COUNT(DISTINCT f.province_id) AS p,
                   SUM(CASE WHEN f.value IS NOT NULL AND u.factor > 0 THEN 1 ELSE 0 END) AS v,
                   MIN(pe.year) AS y0, MAX(pe.year) AS y1
            FROM fact_row f
            JOIN dim_unit u ON u.id = f.unit_id
            JOIN dim_period pe ON pe.id = f.period_id
            GROUP BY f.indicator_id, f.table_id"""):
        label, clean = ind.get(r['i'], ('', ''))
        key += 1
        rows.append((key, label, clean, tab.get(r['t'], ('', ''))[1],
                     r['n'], r['p'], r['v'], r['y0'], r['y1'], fold(clean or label)))
        if len(rows) >= 50_000:
            dst.executemany("INSERT INTO ind VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            rows = []
    if rows:
        dst.executemany("INSERT INTO ind VALUES (?,?,?,?,?,?,?,?,?,?)", rows)

    forms = [(r['form_code'], r['label'], r['n'], r['p'], fold(r['label']))
             for r in src.execute("""
                 SELECT t.form_code, t.label, COUNT(*) AS n,
                        COUNT(DISTINCT f.province_id) AS p
                 FROM fact_row f JOIN dim_table t ON t.id = f.table_id
                 GROUP BY t.id""")]
    dst.executemany("INSERT INTO frm VALUES (?,?,?,?,?)", forms)
    dst.execute("INSERT INTO ind_fts(ind_fts) VALUES ('rebuild')")
    dst.execute("INSERT INTO frm_fts(rowid, folded) SELECT rowid, folded FROM frm")
    dst.commit()
    n = dst.execute("SELECT COUNT(*) FROM ind").fetchone()[0]
    dst.close()
    src.close()
    if verbose:
        print(f"{INDEX.name}: {n:,} indicator/form pairs, {len(forms):,} forms, "
              f"{INDEX.stat().st_size/1e6:.1f} MB, {time.time()-t0:.0f}s")
    return n


_CON = None


def index(rebuild_if_missing=True):
    global _CON
    if _CON is None:
        if not INDEX.exists():
            if not rebuild_if_missing:
                raise FileNotFoundError(INDEX)
            build(verbose=False)
        _CON = sqlite3.connect(f"file:{INDEX}?mode=ro", uri=True, check_same_thread=False)
        _CON.row_factory = sqlite3.Row
    return _CON


def match_expr(q, prefix=True):
    """Fold the query and quote every token.

    MATCH is a query language, not a string: a bare quote is a syntax error and NEAR/AND/OR/
    NOT/*/^/:/(/)/- are operators, so raw user text either throws or searches for the wrong
    thing. Quoting each token also gives AND-of-tokens semantics, which found 1,202 indicators
    for "chi dau tu" where LIKE's contiguous-substring semantics found 650.
    """
    toks = [t.replace('"', '""') for t in fold(q).split() if t.strip('"')]
    if not toks:
        return ''
    quoted = [f'"{t}"' for t in toks[:-1]]
    quoted.append(f'"{toks[-1]}"' + ('*' if prefix and len(toks[-1]) >= 3 else ''))
    return ' '.join(quoted)


def indicators(q, form_code=None, min_provinces=1, limit=15):
    """Search indicator labels. Ranked by province breadth, then rows - never by bm25."""
    expr = match_expr(q)
    if not expr:
        return []
    con = index()
    sql = """SELECT i.indicator, i.clean, i.form_code, i.nrows, i.nprov, i.nvnd, i.y0, i.y1
             FROM ind_fts JOIN ind i ON i.key = ind_fts.rowid
             WHERE ind_fts MATCH ? AND i.nprov >= ?"""
    args = [expr, min_provinces]
    if form_code:
        sql += " AND i.form_code = ?"
        args.append(form_code)
    sql += " ORDER BY i.nprov DESC, i.nrows DESC LIMIT ?"
    args.append(int(limit))
    return [dict(r) for r in con.execute(sql, args)]


def forms(q, limit=15):
    """Search form labels (`Cân đối ngân sách địa phương` -> B62), diacritics optional."""
    expr = match_expr(q)
    if not expr:
        return []
    con = index()
    return [dict(r) for r in con.execute(
        """SELECT f.code, f.label, f.nrows, f.nprov FROM frm_fts
           JOIN frm f ON f.rowid = frm_fts.rowid
           WHERE frm_fts MATCH ? ORDER BY f.nprov DESC, f.nrows DESC LIMIT ?""",
        (expr, int(limit)))]


if __name__ == '__main__':
    build()
