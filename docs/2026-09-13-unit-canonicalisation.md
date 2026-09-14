# 2026-09-13 — one spelling per unit

The owner asked for the unit column to be audited and made consistent. It now is: 22 published
spellings resolve to 4 currency units plus the things that correctly are not currency, and the
660 rows that named đồng without ever converting now convert.

This closes the item `docs/2026-09-12-unit-correctness-audit.md` §"Still open" left for the
owner to decide: *"Whether an obvious misspelling should be normalised is the project owner's
call."* The call was made.

---

## 1. What was actually in the data

Counted across all 34 workbooks in `output/` — not the warehouse, because the workbook is what
is handed over. `./nsnn --units` reproduces this at any time.

| ĐVT as published | rows | factor before | factor after | reported as |
|---|---:|---:|---:|---|
| `Triệu đồng` | 2,621,055 | 1e6 | 1e6 | `triệu đồng` |
| `triệu đồng` | 156,434 | 1e6 | 1e6 | `triệu đồng` |
| `1.000.000 đồng` | 401 | 1e6 | 1e6 | `triệu đồng` |
| **`Triệu dồng`** | **347** | **none** | **1e6** | `triệu đồng` |
| **`Tiệu đồng`** | **166** | **none** | **1e6** | `triệu đồng` |
| **`Tr đồng`** | **122** | **none** | **1e6** | `triệu đồng` |
| **`Triệu đổng`** | **25** | **none** | **1e6** | `triệu đồng` |
| `đồng` / `Đồng` | 131,472 | 1 | 1 | `đồng` |
| `tỷ đồng` / `Tỷ đồng` | 3,172 | 1e9 | 1e9 | `tỷ đồng` |
| `Nghìn đồng` / `1.000 đồng` / `1000 đồng` | 798 | 1e3 | 1e3 | `nghìn đồng` |
| `%`, `% (phần trăm)`, `Phần trăm (%)`, `%(phần trăm)` | 159,653 | none | none | unchanged |
| `đơn vị`, `dự án` | 314 | none | none | unchanged |
| `Công an tỉnh` | 6 | none | none | unchanged |
| *(blank)* | 740,462 | none | none | unchanged |

The four bold rows are the whole substantive change: **660 rows**, of which **595 carry a
figure** and had no `Quy đổi VND`. Everything else is spelling only.

`Công an tỉnh` is an agency name that reached the unit column — the residue of the
`Đơn vị:` bug recorded in CLAUDE.md. Six rows, none with a value. Left alone.

## 2. The rule

Two functions in `pipeline/parse_xml.py`:

* `unit_factor(u)` — unchanged in shape. The last word must be a listed spelling of đồng and
  the word before it decides the scale. It gained three scale spellings (`tiệu`, `tr` beside
  `triệu`) and two currency spellings (`dồng`, `đổng` beside `đồng`).
* `canon_unit(u)` — new. A recognised currency returns its canonical spelling; anything else
  is returned cleaned but untouched, because rewriting a string we could not classify would be
  a guess.

**Recognition is an explicit allowlist, never a fuzzy match.** That is the one design decision
worth keeping. Stripping diacritics is the obvious way to catch `dồng` and `đổng`, and it also
collapses these onto the same string:

```
đồng  dồng  đổng  dòng  đóng  dọng  động   ->  all fold to "dong"
```

`dòng` means **line/row**. A unit column reading `dòng` is a row count, and folding would
convert it to money at factor 1. Since the corpus contains exactly three spellings of the
currency word, they are listed literally and everything else gets no conversion — which is
what `never infer a value when the unit is unclear` requires. The five collision cases are in
the test suite.

## 3. Where the change lands

| | |
|---|---|
| `pipeline/parse_xml.py` | `CURRENCY`, expanded `SCALE`, new `canon_unit` |
| `pipeline/parse_xml.py`, `parse_xlsx.py` | `ĐVT` is written canonical; the published spelling goes to `Ghi chú` as `ĐVT nguồn: …` whenever it differs |
| `pipeline/run.py` | `./nsnn --units`, a standing audit over `output/` |
| `dashboard/etl.py` | `dim_unit.canon`; `v_fact.unit` is canonical and `v_fact.unit_source` keeps the published spelling |
| `dashboard/migrate_units.py` | applies the same rules to an existing `data/nsnn.db` without a re-crawl |
| `mcp_server/` | tool text updated; 19 new checks |

## 4. Migration rather than rebuild

The warehouse is built from `work/`, several GB of cached downloads absent from a fresh
checkout, so a rebuild was not available. `dim_unit.factor` is a pure function of the label, so
`dashboard/migrate_units.py` recomputes exactly what `etl.py` would now produce and touches
nothing else. It refuses to run if any *existing* factor would change — that would be
restating published figures, not cleaning up spelling.

```
rows with a VND value : 1,992,983 -> 1,993,578  (+595)
fact rows             : 3,814,427 -> 3,814,427  (unchanged)
```

Verified afterwards through `v_fact`: 7 published spellings now report as `triệu đồng`, 3 as
`nghìn đồng`, 2 each as `đồng` and `tỷ đồng`, and `unit_source` still returns every original.

## 5. The workbooks do not need a re-crawl

An earlier draft of this note said the workbooks could only be brought in line by re-running
`./nsnn`, which would re-download several GB because `work/` is absent. **That was wrong**, and
it conflated "rebuild through the pipeline" with "fix the unit column".

All three affected columns are pure functions of what the workbook already holds:

    ĐVT         -> canon_unit(ĐVT)
    Quy đổi VND -> Giá trị chuẩn hóa x unit_factor(ĐVT)
    Ghi chú     -> "; ĐVT nguồn: <as published>" when the spelling changed

`pipeline/fix_units.py` does exactly that, in place, with no network and no `work/`. It refuses
to save a workbook where a row already carries a *different* VND figure, because restating a
published conversion is not a cleanup. Measured on Phú Thọ: 14,380 rows, 10,675 spellings
unified, 347 conversions added, and the three sheets, freeze panes, bold header and column
widths all survive the round-trip.

`./nsnn` is only needed when the SOURCE data changes.

## 6. Known divergence — read this before comparing outputs

`output/*.xlsx` were generated on 2026-09-12 and **still carry the old spellings**, with
`Quy đổi VND` blank on those 595 rows. The warehouse has been migrated; the workbooks have
not, because regenerating them needs `work/` and therefore a re-crawl.

So until `pipeline/fix_units.py --apply` is run:

* the warehouse and the MCP server report `triệu đồng` and a converted value,
* the workbook for the same row reports `Tr đồng` and a blank conversion.

Neither is wrong. `fix_units.py --apply` reconciles them in minutes without touching the
network - but it rewrites all 34 files, which is **250 MB of fresh Git LFS objects**. Three
full sets have already been pushed against a 1 GB free tier, so committing a fourth is likely
to exceed the quota. That is why the fix ships as a tool rather than as a committed rewrite:
anyone can bring their own checkout up to date deterministically, at no storage cost.

## 7. Still open

* **The workbooks need a re-run** to match the warehouse (§5).
* **`Công an tỉnh` in the unit column.** Six rows. A stricter `looks_like_unit` in
  `parse_xlsx.py` would reject an agency name outright, rather than letting it through with
  factor 0.
* **`đơn vị` and `dự án` are real units of count**, not currency and not percentages. Nothing
  reads them as a quantity today; if counts ever matter they need their own handling.
* **Unknown spellings will keep arriving.** That is why `./nsnn --units` exists. Anything it
  lists under NOT CONVERTED that names đồng is a new spelling to add to `CURRENCY` or `SCALE`
  after looking at the source — not before.
