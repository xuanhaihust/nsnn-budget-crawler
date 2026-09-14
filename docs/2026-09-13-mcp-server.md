# 2026-09-13 — serving the warehouse to agents over MCP

Built `mcp_server/`: 15 read-only tools over `data/nsnn.db`, on stdio or streamable HTTP from
one codebase. Two rounds of adversarial review found 26 defects, all fixed. 97 checks in
`mcp_server/test_server.py`.

The point was never access — SQL already gave that. It was that the obvious query is wrong,
silently, in one direction, for every province.

---

## 1. The measurement that shaped everything

`fact_row` is a flat projection of hierarchical published forms, so a parent and all of its
children are rows side by side with nothing to distinguish them.

```sql
SELECT SUM(vnd) FROM v_fact
WHERE province='Nghệ An' AND year=2021 AND form_code='B46';
```

| | tỷ đồng |
|---|---:|
| naive `SUM(vnd)` | 129,896 |
| the province's own published `B TỔNG CHI NSĐP` | 25,717 |

Across the 138 province-years where both can be measured: **min 3.95x, median 5.97x, max
6.98x, mean 5.75x**. Always an overstatement, never an understatement, and nothing in the data
announces it.

So the tools never aggregate. They read a named cell of a named form, or they break a
published parent into its direct children and print the residual.

## 2. Three premises that turned out to be false

Each was one query away from being disproved, and each would have been load-bearing. The rules
they produced now live in `.claude/memory/gotchas-analysis.md`; the measurements are here.

* **`dim_indicator.depth` is not the outline level** — 3.32M of 3.8M rows are `depth=1`, so a
  depth filter returns the whole form.
* **Keying a cell on the cleaned label merges different lines.** Ambiguous cells keyed on
  `clean` vs on `indicator_raw`: B46 0/0 · B49 152/0 · B50 337/0 · B63 0/0 · B64 675/0 ·
  B65 1,555/0. `1.1 Chi giáo dục` is the investment line, `1 Chi giáo dục` the recurrent one —
  6.1x apart in Bắc Ninh 2017.
* **`mode=ro` is not a sandbox** — on a read-only connection `ATTACH` still created a writable
  database and `VACUUM INTO` still wrote a full 352 MB copy.

`dashboard/aggregate.py` still keys on `clean`, so the dashboard drops cells the MCP server
reads. Unchanged here, recorded as a known improvement.

## 3. How a breakdown is scoped

`v_fact.id` is `fact_row.rowid`, which is source row order. The children of a parent are the
rows after it up to the next row at its level or shallower — bounded three ways:

* **To the parent's own report and table.** 4,016 of 83,559 (province, year, form, series)
  blocks span more than one report.
* **To a parseable parent.** An unreadable marker is refused, not treated as the root.
* **To direct children only.** Dash rows beneath a numbered line are its parts.

Worked example, Nghệ An 2020 B63, series `QUYẾT TOÁN/TỔNG THU NSNN`, parent `I Thu nội địa`:

| | triệu đồng |
|---|---:|
| all 24 descendants, flat | 19,314,379 |
| 18 direct children | 16,658,849 |
| published parent | 16,658,847 |
| residual | −2 |

The two dash rows under line 6 (`613,086` + `1,688,212`) add to exactly line 6 (`2,301,298`).
Counting them as siblings adds 2.3 trillion VND that is already counted.

## 4. Search

FTS5's own `remove_diacritics` is unusable for Vietnamese. `=1` folds `giáo`→`giao` but leaves
`tế` and `đất`; even `=2` never folds `đ`→`d`, which appears in **43.6% of labels**. Searching
`dau tu` unfolded returns crude oil (`dầu thô`) and a pagoda (`chùa Dâu`) and nothing about
investment.

Folding happens in Python — and the standard NFD recipe is also wrong, because `đ` is U+0111,
a distinct letter with no canonical decomposition, so `unicodedata.combining` returns 0 for
it. `warehouse.fold` translates it explicitly.

Ranking is by **province breadth, never bm25**: 93.3% of distinct labels are one-province
project lines and bm25 rewards short documents, so for `chi giáo dục` it puts a
1-province/31-row label above the 34-province/9,748-row national one.

The index lives in its own file, `data/nsnn-search.db`, built in ~12s on first use and
gitignored. 276,813 (indicator, form) pairs — the (indicator, form, series) grain would be
2,692,319.

## 5. The SQL guard

Five layers, each catching what the others miss:

1. `mode=ro` + `PRAGMA query_only`.
2. A deny-by-default authorizer — this is what stops `ATTACH`, `VACUUM INTO`, `PRAGMA
   writable_schema`, `load_extension`, and reads of `dbstat`/`sqlite_stmt`. Returns
   `SQLITE_DENY`, never `SQLITE_IGNORE`: IGNORE substitutes NULL for a denied column and lets
   the query succeed, manufacturing a blank where a value existed.
3. `execute`, never `executescript` — `executescript('SELECT 1; DROP TABLE fact_row')` drops
   the table; `execute` refuses a second statement, and that refusal is the injection defence.
4. A progress-handler deadline (10s, tick 50,000 — +0.3% overhead against +2.4% at tick 1,000).
5. A streamed row and byte cap. The deadline does **not** bound a single VDBE step:
   `randomblob(1e9)` ran 7.7s past a 3s deadline at 978 MB RSS. And `SELECT * FROM v_fact`
   under `fetchall()` materialised 3,814,427 rows in 71s at 6.5 GB; streamed, its first 200
   rows cost 2ms.

The cap is never applied by appending `LIMIT` to the agent's SQL — that is a syntax error on
already-limited, `LIMIT..OFFSET`, `VALUES` and CTE queries, and is silently swallowed when the
query ends in a `--` comment.

15 attacks blocked; recursive CTEs still work.

## 6. A data defect found while building

757 values across 25 provinces sit 100x or further from their own cell's history. They pass
every unit check because the declared unit is correct. The shape is a thousands separator read
as a decimal point:

| Đồng Nai, B46, `B TỔNG CHI NSĐP` | source cell | parsed, tỷ đồng |
|---|---|---:|
| 2018 | `26.003608` | 0.026 |
| 2019 | `20.625921` | 0.021 |
| 2020 | `29106050` | 29,106.050 |
| 2021 | `28.709234` | 0.029 |
| 2022 | `23556345` | 23,556.345 |

Three of five years are affected, so even that cell's own median is one of the bad values —
which is why `nsnn_list_data_quality(block='magnitude')` shows the whole series rather than
naming a culprit. **Nothing was corrected.** Which reading each year intended is not decidable
from the warehouse. Flagged `MAGNITUDE?`, excluded from ratios, left as published. Whether to
normalise them is the data owner's call.

## 7. Two rounds of review, 26 defects

Four lenses (correctness, security, protocol, agent-UX), then a verification pass told to
refute the first round's findings. **16 of 29 second-round claims survived; 13 were rejected.**

Round one (10): unmarked parent treated as the root · bare `I` always roman · one SQLite
connection shared across worker threads (6 of 8 trials of two overlapping calls deadlocked) ·
scan crossed document boundaries · `money()` rendered <500,000 VND as `0` · balance sheet
skipped the magnitude check · `(2)` and `2` ranked equal · reconciliation tested the parent
for truthiness · `json_each` bypassed the function allowlist · orientation text contradicted
the data.

Round two (16), the most important of which was **a regression in round one's own fix**:

> `i_is_section` decided once per block whether a bare `I` was a letter or a roman. Forms
> 45/CK-NSNN and 58/CK-NSNN need it read both ways in the same block — they letter sections
> A…H, I, K *and* use romans I, II, III as agency headings under each. So `break_down` began
> flatly asserting "is a leaf" for **6,377 section rows in 1,137 blocks across 19 provinces**
> that have children. Bắc Ninh 2019 section H reported none; it has six, summing to 1,347.15 tỷ.
>
> `block_levels` now resolves it per row by lookahead. The verifier also found the original
> report's diagnosis was *inverted* and one of its quoted outputs *fabricated* — a fix written
> from the report as filed would have made it worse.
>
> **The scale was wrong too, in this document.** It said 9,919 rows in 1,259 blocks across
> 20 provinces. That was the review agent's figure, repeated here without being checked.
> Running the actual old code from `da54f54` against the new one gives **6,377 rows in
> 1,137 blocks across 19 provinces** — the defect was real and the fix is unchanged, but
> the number was 56% too high for a day. Reproduce with
> `git show da54f54:mcp_server/warehouse.py` and compare `i_is_section` to `block_levels`.

Also in round two: the TIER WARNING named a denominator `share` does not use · `basis` fell
through to the PLAN for unrecognised values including English `"settled"` · no tool clamped
`limit` (SQLite reads a negative limit as no limit — 2.5 MB from one call) · NFD labels matched
nothing · search truncated labels it told the agent to copy exactly · `read_timeseries` had no
magnitude flag · every numeric argument advertised as `"type": "string"` · flag vocabulary
undefined · series glossary explained absent terms · mixed-year ranking · oversized argument
echoed back · `run_sql` had no db-missing message.

## 8. Measurements

| | |
|---|---|
| warehouse restore | 18s |
| search index build | 12s, 69 MB, gitignored |
| `nsnn_find_indicators` | 2–14 ms |
| `nsnn_read_balance_sheet` | ~0.5s |
| guarded `SELECT * FROM v_fact`, 200 rows | 2 ms, 14 MB RSS |
| same under `fetchall()` | 71s, 6.5 GB RSS |
| output format | 2,409 chars vs 8,618 as the SDK's default JSON, same 40 rows |
| tool descriptions, all 15 | ~6,500 chars |

## 9. Still open

* **`dashboard/aggregate.py` still keys on `clean`**, so the dashboard drops cells the MCP
  server can read. Its comment about B65 being unusable for sector figures is no longer true
  on the raw key. Not changed here.
* **757 magnitude defects** left exactly as published, pending a decision.
* **527 reports produce no rows** — 262 PDF/DOC only, 238 parsed to nothing, 27 with no
  attachment. Listed with reasons, not recovered.
* **The ~50-record CKNS gap** is unchanged.
* **Ministry layer only.** No agency-level portal data, so no province is complete on this.
* **No authentication on the HTTP transport.** Public disclosure data, but it will run
  queries for anyone who reaches the port. On a non-localhost host the SDK does not enable
  DNS-rebinding protection; pass explicit `TransportSecuritySettings` before exposing it.
* **Two rounds of review is not a proof.** The second round found a defect the first round
  introduced. A third pass would likely find more.
