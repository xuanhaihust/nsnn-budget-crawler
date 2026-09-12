# dashboard/

A single self-contained page over the 3,814,427 rows the crawler produced.

Open `index.html` — no server, no build step, no network. Everything it needs, including
the numbers, is inside that one file.

## Why it is built this way

The 34 workbooks are the deliverable but they are a terrible analysis surface: 250 MB of
xlsx, one file per province, no way to ask a question across them. So the pipeline gains
one more stage, and the dashboard reads the end of it rather than the workbooks.

```
work/<province>/     →  ETL  →  data/nsnn.db  →  aggregate  →  dashboard/data.json
cached sources          3.8M rows, 352 MB         GROUP BY          29 KB
                             ↕  db.py pack/restore                     ↓
                        data/nsnn.db.xz  33 MB, committed   build_html → index.html
```

**SQLite for the warehouse.** A single file, no server, and the whole corpus fits in one
`GROUP BY`. The fact table is fully normalised — 34 provinces share 21 periods, 6 scopes and
153,272 indicator labels across 3.8M rows, so every repeated string is interned into a
`dim_*` table and `fact_row` carries integer ids. Nothing is aggregated at load time:
`fact_row` is one row per source row, so any number on the page can be traced back through
`dim_report` to a stored file with a SHA-256.

**The database is not committed.** It is 360 MB and rebuilt in ~6 minutes from `work/`.
`data/` is gitignored; `dashboard/data.json` — the 29 KB of grouped answers the page
actually needs — is committed instead.

**The data is embedded, not fetched.** The page has to open from `file://`, from GitHub
Pages and inside a sandboxed frame. A `fetch()` fails in at least one of those, so
`build_html.py` inlines `data.json` into the HTML.

## Rebuild

```bash
.venv/bin/python dashboard/db.py restore  # nsnn.db.xz -> nsnn.db      (~13 s)  ← usually this
.venv/bin/python dashboard/etl.py         # work/ -> data/nsnn.db      (~6 min, needs work/)
.venv/bin/python dashboard/aggregate.py   # nsnn.db -> data.json       (~10 s)
.venv/bin/python dashboard/build_html.py  # data.json -> index.html    (instant)
.venv/bin/python dashboard/db.py pack     # nsnn.db -> nsnn.db.xz      (~93 s)
```

`etl.py` calls `pipeline/build.py` directly rather than reading the xlsx files, so the
warehouse always reflects the current parsers. It needs `work/` — the cached sources — so
after a fresh clone `db.py restore` is the route in, not `etl.py`.

`Quy đổi VND` is not a stored column: it is exactly `value * dim_unit.factor`, and `factor`
is `0` in precisely the cases where the parser leaves the conversion blank. The `v_fact`
view derives it, which keeps the rule in one place and takes 30 MB of duplicated floats out
of the file. The view reproduces all 1,992,983 converted values in the workbooks exactly.

## Querying the warehouse

```sql
-- how much of each province's corpus carries a VND conversion
SELECT p.name, COUNT(*) AS rows,
       SUM(f.vnd IS NOT NULL) AS with_vnd
FROM fact_row f JOIN dim_province p ON p.id = f.province_id
GROUP BY p.name ORDER BY rows DESC;

-- one indicator across every province and year
SELECT p.name, pe.year, f.raw, u.label AS unit, f.vnd
FROM fact_row f
JOIN dim_province p  ON p.id = f.province_id
JOIN dim_period   pe ON pe.id = f.period_id
JOIN dim_indicator i ON i.id = f.indicator_id
JOIN dim_unit     u  ON u.id = f.unit_id
WHERE i.clean = 'TỔNG CHI NGÂN SÁCH ĐỊA PHƯƠNG'
ORDER BY p.name, pe.year;
```

`dim_indicator.clean` strips the outline marker that the source glues onto every label
(`"I Thu nội địa"` → `Thu nội địa`, depth 1), so indicators group across provinces that
number their rows differently. `dim_unit.factor` is the VND multiplier, `0` where the unit
is unknown and the conversion was deliberately left blank.

## What the page shows

**The budget itself**, from form **B46 — Cân đối ngân sách địa phương**, the provincial
balance sheet. Two findings lead the page:

- **How much of its own money each province raises.** `Thu NSĐP được hưởng theo phân cấp`
  over `TỔNG NGUỒN THU NSĐP`. The spread is enormous: Bắc Ninh funds 98% of its own budget,
  Lạng Sơn 15%. The industrial north and the two big cities sit at the top; the mountainous
  border provinces sit at the bottom.
- **Self-sufficiency tracks investment almost one-for-one** (r = 0.88, n = 31). Provinces
  above 80% self-sufficiency put a median 46% of spending into `Chi đầu tư phát triển`;
  those below 40% put in 15%, with the rest going to recurring costs. Correlation, not
  causation — the data cannot say which way it runs.

**And disclosure quality**: which province published what, in which year, in which format,
and how much of it converts to VND.

### Why B46 and nothing wider

The corpus is long-format hierarchical line items. `TỔNG CHI NSĐP`, `B TỔNG CHI NSĐP` and
`A CHI CÂN ĐỐI NSĐP` all appear as separate rows of the same table, so summing line items
would double count.

B46 escapes that because every figure used is **a single published cell of one named form** —
never a sum. The ratio behind the headline is two such cells divided by each other, so there
is no summing assumption anywhere. Checked against the source: An Giang's 2019 own revenue
(5,243.9 tỷ) plus central transfers (8,230.2 tỷ) equals its published total (13,474.1 tỷ)
exactly, and its total spending equals its total revenue, as a budget plan must.

The figures are **dự toán** (plans), not **quyết toán** (settled accounts), and the latest
year with data differs by province (2020–2024) because disclosure coverage is uneven. Both
are stated on the page rather than smoothed over. 32 of 34 provinces carry B46 at all.

A national total across provinces is still not attempted: that needs a curated indicator
mapping per form, which does not exist yet.

The colour palette is validated, not eyeballed: three categorical slots and a six-step
sequential blue, checked in both modes with the data-viz validator (worst all-pairs CVD
ΔE 9.2 light / 9.4 dark). Every chart also has a table view, which is what carries the
light-mode aqua slot below the 3:1 contrast line.
