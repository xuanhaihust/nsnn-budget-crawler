# dashboard/

A single self-contained page over the 3,814,427 rows the crawler produced.

Open `index.html` — no server, no build step, no network. Everything it needs, including
the numbers, is inside that one file.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../docs/images/dashboard-hero-dark.png">
  <img alt="The top of index.html: title, the five headline counts, and the first chart" src="../docs/images/dashboard-hero.png">
</picture>

The page follows the reader's theme; the screenshots below are the light one. Every figure on
it is computed from `data/nsnn.db` at build time — nothing on the page is estimated.

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

**Ten analyses of the budget**, then four of the disclosure behind it.

| # | Chart | Source | Headline |
|---|---|---|---|
| 1 | Self-sufficiency | B46 | Bắc Ninh raises 98% of its own revenue, Lạng Sơn 15% |
| 2 | Self-sufficiency vs investment | B46 | r = 0.88 — the self-funded provinces are the ones that build |
| 3 | Where revenue comes from | B63 quyết toán | Five largest sources per province, rest as "khác" |
| 4 | Land-sale dependence | B63 | Median 25% of internal revenue; a one-off source that does not repeat |
| 5 | Recurring spending by sector | B50 | Education 26%, health 24%, science and technology 1% |
| 6 | Education vs health | B50 | The two dominant lines, side by side |
| 7 | Plan vs outturn | B63 (both columns) | Revenue beats plan in 86% of province-years, median 106% |
| 8 | Borrowing and repayment | B46 | All provinces low — local borrowing is capped by law |
| 9 | Absolute size | B46 | The largest budget is ~16x the smallest |
| 10 | Trend | B46 | Median province revenue 12.0 → 18.6 nghìn tỷ, 2018–2024 |
| 11–14 | Coverage, volume, machine-readability, units | — | Disclosure and data quality |

Chart 1 — each province's revenue split into what it raises itself and what the centre sends.
Two published cells of one form, so there is nothing to double count:

![Self-sufficiency: Bắc Ninh 98% at the top, Lạng Sơn 15% at the bottom](../docs/images/dashboard-self.png)

Chart 3 — the five largest revenue sources per province, everything else as `khác`:

![Where revenue comes from, per province](../docs/images/dashboard-rev.png)

Chart 5 — recurrent spending by sector, and the legend that says one province was held back:

![Recurrent spending by sector, education 26% and health 24% at the median](../docs/images/dashboard-sect.png)

Chart 7 — plan against outturn, both read from the same B63 report, so no cross-form join:

![Plan vs outturn: revenue beats plan in 86% of province-years](../docs/images/dashboard-pa.png)

Chart 10 — the median province rather than a national sum, with the reporting count printed
under each year, which is the reason for using a median at all:

![Median province revenue 12.0 to 18.6 nghìn tỷ, 2018 to 2024, with n under each year](../docs/images/dashboard-trend.png)

Chart 11 — who published what, and from which year:

![A coverage grid of 34 provinces against years](../docs/images/dashboard-heat.png)

### The rule every one of these follows

Each figure is **a named cell of one named form**, and the derived numbers are ratios of two
such cells. Nothing is a sum of line items, so nothing can double count.

Two consequences are enforced in code rather than trusted:

- **Ambiguous cells are dropped, not guessed.** Where the header rebuild gave two different
  columns of a form the same series label, there is no way to know which column a value came
  from. Those keys are discarded and the count is printed by `aggregate.py` (B50 loses 289,
  B46 and B63 lose none). B65 loses ~120 of ~125 sector keys this way, which is why the
  sector split is read from B50 instead.
- **A province missing one charted component is excluded from that chart**, not drawn with a
  zero slice. Tây Ninh publishes 661.8 tỷ of education spending but its cell is ambiguous;
  drawn naively it became a province that spends nothing on schools. The legend says how many
  provinces were held back — visible under chart 5 above as *1 tỉnh không hiển thị vì thiếu ít
  nhất một lĩnh vực*.

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
mapping per form, which does not exist yet. The trend chart shows the **median province**
rather than a sum for the same reason — the number of provinces reporting swings from 17 to
29 by year, so a sum would read as a collapse in a thin year.

The colour palette is validated, not eyeballed: three categorical slots and a six-step
sequential blue, checked in both modes with the data-viz validator (worst all-pairs CVD
ΔE 9.2 light / 9.4 dark). Every chart also has a table view, which is what carries the
light-mode aqua slot below the 3:1 contrast line.
