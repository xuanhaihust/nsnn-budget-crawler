# 2026-09-13/14 — one currency unit

The unit column was audited and unified. 22 published ĐVT spellings now resolve to **6 units**,
and the four currency scales collapse to a single one: **VND**.

This closes what `docs/2026-09-12-unit-correctness-audit.md` left for the owner to decide —
*"whether an obvious misspelling should be normalised is the project owner's call."*

| unit | rows | published spellings behind it |
|---|---:|---:|
| `VND` | 2,913,992 | 14 |
| *(blank)* | 740,462 | 1 |
| `%` | 159,653 | 4 |
| `đơn vị` | 159 | 1 |
| `dự án` | 155 | 1 |
| `Công an tỉnh` | 6 | 1 |

`Công an tỉnh` is an agency name that reached the unit column — residue of the `Đơn vị:` bug.
Six rows, none with a value. Left alone, because it is not a unit and guessing what it meant
is not this code's job.

---

## What changed in the data

**660 rows gained a conversion.** Four spellings named đồng and never converted —
`Triệu dồng` (347), `Tiệu đồng` (166), `Tr đồng` (122), `Triệu đổng` (25) — of which 595 carry
a figure.

**Every currency row now reads `VND`**, and `Giá trị chuẩn hóa` carries the VND figure rather
than the published number. đồng, nghìn đồng, triệu đồng and tỷ đồng are four scales of one
currency, not four currencies.

Nothing about what a province wrote is lost:

| column | holds |
|---|---|
| `Giá trị gốc` | the published text, e.g. `6193000` |
| `Giá trị chuẩn hóa` | the VND figure, e.g. `6193000000000` |
| `ĐVT` | `VND` |
| `Ghi chú` | `ĐVT nguồn: Triệu đồng` |

`etl.py` reads `ĐVT nguồn` back out of `Ghi chú`, so `v_fact.unit_source` still returns all 14
original spellings. Without that a rebuild would read every ĐVT as `VND` and the last link back
to the published spelling would break at that hop.

## The two design decisions

**Recognition is an explicit allowlist, never a fuzzy match.** Stripping diacritics is the
obvious way to catch `dồng` and `đổng`, and it also collapses these onto one string:

```
đồng  dồng  đổng  dòng  đóng  dọng  động   ->  all fold to "dong"
```

`dòng` means **line**. A unit column reading `dòng` is a row count, and folding would price it
at 1 VND. The corpus contains exactly three spellings of the currency word, so they are listed
literally in `parse_xml.CURRENCY` and everything else gets no conversion. Percentages work the
same way via `PERCENT`: a string merely containing `%` is not enough, because `% so với dự
toán` and `tỷ lệ %` name comparison columns, not units. The collision cases are in the tests.

**Idempotency**, because a rescale applied twice squares the error. `unit_factor('VND')` is 1,
so a second pass multiplies by 1; `triệu VND` returns None rather than 1e6, so a half-converted
string is refused rather than doubled. Verified by applying twice to Phú Thọ and diffing — the
second pass reports 0 respelt, 0 converted, identical cells.

## The check that had to come first

Multiplying out to VND is where precision dies. Measured **before** committing to the change:

* 5,501 rows exceed float64's exact-integer range once converted.
* **All 5,501 are rows already flagged implausible** (>1e15), where the source declared the
  wrong scale.
* **Zero rows of real data lose a digit.** Vietnam's largest provincial budget is ~1.5e14
  against an exact range of 9.007e15.

Had that come out the other way, this change would have been wrong to make.

## The check that proves no number moved

The warehouse migration rescaled 1,864,198 values. The VND aggregate is bit-identical either
side:

```
before  n=1,993,578  sum=8.052995e+22  min=-8.969861e+17  max=5.323818e+21
after   n=1,993,578  sum=8.052995e+22  min=-8.969861e+17  max=5.323818e+21
```

Workbooks and warehouse read back identical too: Phú Thọ 9,515 VND rows summing 4.060410e+15
in both, Nghệ An 102,684 rows summing 4.092877e+17 in both.

## Where it lands

| | |
|---|---|
| `pipeline/parse_xml.py` | `CURRENCY`, `PERCENT`, `SCALE`, `CANON`, `canon_unit` |
| `pipeline/parse_xlsx.py` | same rules on the spreadsheet path |
| `pipeline/run.py` | `./nsnn --units` — a standing audit over `output/` |
| `pipeline/fix_units.py` | rewrites existing workbooks in place, offline, one process each |
| `dashboard/etl.py` | `dim_unit.canon`; recovers `ĐVT nguồn` from `Ghi chú` |
| `dashboard/migrate_units.py` | the same rules against an existing `data/nsnn.db` |
| `mcp_server/` | tool text updated; the unit checks are in `test_server.py` |
| `dashboard/` | rebuilt 2026-09-15 — see below; the page was two days stale until then |

**The dashboard did not follow automatically, and said so on its own face.** `data.json` and
`index.html` are a separate build stage, so after the migration they still carried the
pre-VND aggregates. Its unit chart kept claiming *"660 dòng … cố ý để trống cột quy đổi VND"*
— true on 2026-09-12, false from 2026-09-14. Found on 2026-09-15 by screenshotting the page
for its README, which is the argument for screenshots that regenerate.

`aggregate.py` + `build_html.py` fixed it in seconds and moved exactly what it should:
`vnd_rows` 1,992,983 → **1,993,578** (+595, the rows that gained a conversion), the six
misspelling rows from factor 0 to factor 1, and per-province/indicator `vnd_n` up by the same
595 in total. **No row count moved anywhere.** The chart's caption had to be rewritten as well,
because with nothing left unconverted it rendered as *"0 dòng …"* followed by an empty list;
it now states the good case and says a new misspelling will appear as a grey bar containing
`đồng`.

**No re-crawl is needed for any of it.** ĐVT, Giá trị chuẩn hóa, Quy đổi VND and Ghi chú are
pure functions of what a workbook already holds. `./nsnn` is only for when the SOURCE data
changes. Serially the full rewrite is a five-hour job; one process per workbook brings it to
minutes, because openpyxl is CPU-bound on XML.

Both migration tools refuse to run where an *existing* conversion would change — that would be
restating a published figure, not tidying a spelling.

## Still open

* **The declared unit is table-level, so a percentage column inherits the table's currency.**
  Found on 2026-09-14 while building the README screenshots, which is exactly why a screenshot
  of real output is worth having. A form declares *"Đơn vị tính: Triệu đồng"* once at the top
  and then contains a `SO SÁNH (%)` column; every row of that column gets `ĐVT = VND`.

  Mostly harmless: **34,232 rows whose published text contains `%` carry `ĐVT = VND`, and not
  one of them has a VND figure** — `121%` does not parse as a number, so nothing is converted.
  Real where the column prints a bare number: An Giang 2020, `66/CK-NSNN`,
  `SO SÁNH (%) / TỔNG SỐ / CHI THƯỜNG XUYÊN`, source cell `216.87660624369599`, stored as
  **216,876,606 VND**. That is a percentage priced as money.

  **The corpus-wide count is not known and is not guessable from the column name.**
  `Trong đó: 90% NST` contains a `%` and is money; `SO SÁNH ƯỚC THỰC HIỆN VỚI (%)` has values
  up to 2.976e12 in some reports, so that column is not always a percentage either. Counting
  it needs a per-form reading. Nothing has been changed: blanking a conversion on a guess is
  the same mistake as filling one on a guess.
* **Storage.** Each full rewrite of `output/` creates a new set of Git LFS objects (~250 MB).
* **`Công an tỉnh` in the unit column.** A stricter `looks_like_unit` in `parse_xlsx.py` would
  reject an agency name outright rather than letting it through with no factor.
* **`đơn vị` and `dự án` are units of count.** Nothing reads them as a quantity today; if
  counts ever matter they need their own handling.
* **Unknown spellings will keep arriving.** That is what `./nsnn --units` is for. Anything it
  lists under NOT CONVERTED that names đồng is a new spelling to check by hand — after looking
  at the source, not before.
