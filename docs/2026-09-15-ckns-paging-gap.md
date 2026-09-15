# 2026-09-15 — the "~50 record" CKNS gap was paging, and it cost 2,230 reports

The owner asked why the data stopped at recent years. It does not. The crawler was losing the
newest reports to a paging bug that this project had documented as a server limit and written
off as unfixable.

## What was believed

`.claude/memory/gotchas-crawl.md`, until today:

> **Every query stops ~50 records short of the server's own `TotalItems`.** Hà Nội returns 178
> of a claimed 228. This is a server limit, not a bug in the code. Closing it needs a
> full-catalog harvest instead of per-province queries. **Still open.**

`CLAUDE.md` carried it in the Status section as an open, unfixed item. Neither was ever tested.
Both were inferred from watching the paged walk run out early.

## What is true

`PageIndex=0` means "no paging, return the lot". Measured on Cà Mau, TotalItems 414:

| call | records returned |
|---|---:|
| `PageIndex=1 PageSize=50` | 50 per page, walk ends short |
| `PageIndex=1 PageSize=200` | 200, then page 2 returns 0 |
| `PageIndex=1 PageSize=2000` | 0 |
| **`PageIndex=0 PageSize=2000`** | **414** |
| `PageIndex=0 PageSize=5000` | 414 |

The ceiling is TotalItems, not PageSize. Checked against all 34 provinces: `PageIndex=0`
returns exactly TotalItems for every one.

## What it cost

| | server | warehouse | missing |
|---|---:|---:|---:|
| reports, 34 provinces | 10,360 | 8,130 | **2,230 (21.5%)** |
| reports, period 2024–2026 | 1,661 | 466 | **1,195 (72%)** |

The shortfall is not spread evenly — it lands on the newest data. That is the whole reason the
warehouse looked empty for recent years: **0 reports for period year 2026 and 19 for 2025**,
while CKNS publishes 154 and 1,026.

Per province, the paged walk versus what the server has: Hà Nội 164/228, Bắc Ninh 263/342,
Cà Mau 342/414, Hưng Yên 267/331. Every province loses something.

## The fix

`pipeline/ckns.py` `catalog()` now calls `PageIndex=0, PageSize=5000` first and falls back to
the old paged walk if that comes back short, so a server-side change degrades to the previous
behaviour rather than silently returning less.

**The client fix does not backfill the warehouse.** Ingesting the 2,230 missing reports needs a
re-crawl (`./nsnn`), then the ETL and the dashboard rebuild. Until that runs, every figure in
`output/`, `data/nsnn.db` and the dashboard is drawn from 8,130 of 10,360 reports, and recent
years are the worst affected.

## Why this took months to find

It is the working agreement's **"test a constraint before declaring it"** rule, broken a second
time in the same project. The first time it was *"the workbooks can only be fixed by re-running
`./nsnn`, which re-downloads several GB"* — also stated as fact, also never tested, also false;
`pipeline/fix_units.py` does it offline in minutes.

The pattern is the same both times: a limitation was inferred from a symptom, written down in
the same confident register as a measured fact, and then never questioned — because a claim
that something is impossible stops anyone asking for it. That is exactly why it needs *more*
evidence than a claim that something works, not less.

## Also corrected today

An earlier answer in this session stated that the corpus contains no monthly data at all —
*"not one of the 3,814,427 rows is a month"*. That is wrong, and an adversarial review round
caught it. Monthly figures do exist, as **columns inside an attachment** rather than as a
report period: `THỰC HIỆN THÁNG 08/09/10`, `TH TRONG THÁNG 11`, `UTH THÁNG 10/11`.

The scale is small — **Quảng Ninh 2018 only, months 8–11, 1,328 rows** out of 3.8M, 1 province
of 34 — so it still cannot build a monthly series. But "none exists" was false, and the
distinction that matters is that the report *period* is never a month while an attachment's
*columns* sometimes are. Anything looking for monthly data must search the columns, not
`dim_period`.
