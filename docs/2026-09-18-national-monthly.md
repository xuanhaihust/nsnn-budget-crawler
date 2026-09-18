# 2026-09-18 — the national monthly series, and why it is a separate table

The owner's question was monthly state budget revenue and spending for the whole country.
`fact_row` cannot answer it: CKNS publishes four period types and none is a month, and it
carries 34 provinces' local budgets with no central budget in them. So the figures come from
a different source — the statistics office's monthly socio-economic report — and they now sit
in the warehouse as **`fact_national_monthly`**, reachable through
`nsnn_read_national_monthly`.

## Why not merge it into `fact_row`

It is budget data in VND and merging is the obvious move. It is also the wrong one, for three
separate reasons, any one of which is sufficient:

1. **Every unfiltered query would start double counting.** `fact_row` is 34 provinces. A
   `CẢ NƯỚC` row inside it sits beside the provincial rows anyone would sum, and a national
   total is not a 35th province. This is the single failure the whole warehouse is built to
   prevent.
2. **It would make existing documentation false.** `dim_period.kind` takes exactly four
   values, and `describe_corpus`, `CLAUDE.md` and the analysis notes all state that. A month
   row makes those statements quietly wrong.
3. **The distinction that matters most would be lost.** This series carries three parallel
   bases for the same month, one of which this project computed rather than read.
   `fact_row` has no column for that.

So: separate table, separate tool, and **no join path** — no `province_id`, no `period_id`, no
`report_id`. A regression test asserts that, because the easiest way to undo this decision is
to add a convenience column later.

## The table

```
fact_national_monthly(year, month, side, basis, published_text, unit_source, vnd, source_url)
```

94 rows. `side` is `thu`/`chi`. `basis` is one of three, and they must never be summed together:

| basis | rows | range |
|---|---:|---|
| `tháng` | 42 | 2024-12 .. 2026-08 |
| `luỹ kế` | 30 | 2025-02 .. 2026-08 |
| `tháng (suy từ luỹ kế)` | 22 | 2025-05 .. 2026-08 |

The cumulative basis is stored as plain `luỹ kế` with the count in `month`. It was first
loaded as `luỹ kế N tháng`, which made each N its own basis value — eleven of them — so a
caller had to know N before it could ask for the series at all.

## The defect in the data, which is the source's

The source's own monthly figures do not sum to its own cumulative figure, and the bias is
one-directional:

| | cộng tháng | luỹ kế nguồn | lệch |
|---|---:|---:|---:|
| 2026 8T thu | 1,847.8 | 2,023.8 | **−176.0 (−8.7%)** |
| 2026 8T chi | 1,608.0 | 1,608.3 | −0.3 (−0.0%) |

14 of the checkpoints differ by more than 50 nghìn tỷ and **all 14 are revenue**. A month
figure is an early estimate published that month and never revised; the cumulative one is
re-estimated upward. Spending barely moves, revenue moves a lot.

Nothing is corrected. Both series are stored and the tool says which to use for what: `tháng`
for when money moved, `tháng (suy từ luỹ kế)` for how much. `dashboard/load_national.py`
re-derives the gap from the table on every load rather than quoting it, so a reload that
changes the numbers reports the new ones.

## One flag had to become corpus-specific

`money()` stamped `!scale?` on every correct national cumulative figure. Its ceiling —
`IMPLAUSIBLE = 1e15` — was calibrated for a *provincial* figure, where the largest real one in
the corpus is Hà Nội 2021 at 1.96e14 and anything above 1e15 is a declared-unit error. The
national budget really is ~2.6e15.

`money()` now takes `cap`, defaulting to the provincial ceiling, and the national tool passes
`cap=None`. A flag that fires on good data is worse than no flag, because it teaches the
reader to ignore it on the 7,816 provincial rows it was built for. Two tests hold both halves:
the national cumulative is not flagged, and a provincial outlier still is.

## Also

`READABLE` in `warehouse.py` is an allowlist checked by name, so the new table was denied
until it was named there — which is the design working, and is now covered by a test that
fails if a future table is added without that step.

146 checks pass, up from 133.
