# NSNN — Vietnam provincial budget disclosure crawl

Collect published budget data for the **34 current provinces/cities** into one fixed
16-column schema.

## Run it

```bash
cd <project root>
./nsnn                          # all 34 provinces, 5 in parallel
./nsnn "Bắc Ninh" "Hưng Yên"    # only these
./nsnn --status                 # what is built, what remains
./nsnn --jobs 3                 # smaller batch
./nsnn --list                   # every CKNS department + id
./nsnn --with-pdf               # also store PDF/DOC (for a future OCR pass)
```

No province argument means all 34. Provinces run in separate processes; each writes only to
its own `work/<slug>/` and its own output file, so a batch cannot collide. Three concurrent
provinces at 16 threads each (48 connections) were measured with **0 download failures**;
drop to `--jobs 3` if the server starts refusing.

One province takes about 1–3 minutes and writes
`output/<Province>_budget_CKNS_<date>.xlsx`. Re-running is safe and cheap: downloads are
cached by path, so a rebuild after a parser change takes seconds. Always use `.venv/bin/python`
— the system Python is externally managed and has neither `xlrd` nor `openpyxl`.

## Layout

| Path | Holds |
|---|---|
| `samples/` | Reference data supplied by a collaborator. **Read-only. Never edit these.** |
| `output/` | Generated workbooks, one per province. |
| `work/<Province>/` | `catalog.json`, `files.json`, and `raw/` — every downloaded source file. |
| `pipeline/` | The code. `./nsnn` in the project root is the only entry point you need. |
| `data/nsnn.db.xz` | The 3.8M-row SQLite warehouse, packed to 33 MB. `dashboard/db.py restore` unpacks it in ~13s. Ordinary git object, not LFS. |
| `dashboard/` | `index.html` — a self-contained page over the warehouse. See `dashboard/README.md`. |
| `mcp_server/` | The warehouse as an MCP server: 15 read-only tools for an AI agent. See `mcp_server/README.md`. |

`work/raw/` is the evidence trail: every row traces back to a stored file with a SHA-256.
Do not delete it — a province is ~10–250 MB (it was ~900 MB before PDFs were skipped).

## Data source

CKNS, the Ministry of Finance disclosure portal. Its page calls an undocumented JSON API
that answers anonymously:

```
POST https://ckns.mof.gov.vn/_vti_bin/DeptService.svc/SearchReport
{"SiteId":"e9e24430-…","WebId":"60d972cc-…","ListId":"dde649ff-…",
 "DeparmentId":<id>,"PageIndex":1,"PageSize":50, …}
```

About 20,000 reports across all provinces. Each record carries the province, title, form
code, period, circular, approval date, and **direct attachment URLs**. Measured across all
34 provinces, 55% of attachments are machine-readable (XML, xlsx, xls), so most data needs
no OCR. The rest is PDF/DOC, which the pipeline does not download by default — see below.

The richest format is the Circular 343 **XML**: it is self-describing (`code`, `circular`,
`department`, `curencyunit`, `year`, `periodType`, header labels, rows) and maps almost
one-to-one onto the schema. Prefer XML; fall back to the spreadsheet only when no XML exists.

## Hard data rules — do not break these

These come from the project owner and override any convenience.

- **Never turn a blank into `0`.** A blank source cell stays blank in every column.
- **A `-` in the source stays `-`.** Do not read it as zero.
- **Never infer a value** when the layout or unit is unclear. Leave it and record it in
  `Pending_Review` instead.
- `Quy đổi VND` is filled **only** when the unit is known. Percentages and unitless tables
  correctly have no VND value.
- Do not crawl commune or ward level (`xã`/`phường`).
- `Tỉnh/TP hiện hành` is the current name; `Tỉnh/TP theo nguồn` keeps the historical name
  exactly as the source wrote it. Never rewrite history retroactively.

## Output workbook

Three sheets. `Data` (the 16 columns), `Summary_QA` (counts, dedupe, method breakdown),
`Pending_Review` (every report that produced no rows, with the reason). Never quietly drop a
failed report — it belongs in `Pending_Review`.

The 16 columns, in order: Tỉnh/TP hiện hành · Tỉnh/TP theo nguồn · Số QĐ/Văn bản · Ngày tài
liệu · Kỳ dữ liệu · Cơ quan · Phạm vi · Nội dung/Bảng · Chỉ tiêu · Loại số liệu · Giá trị gốc
· Giá trị chuẩn hóa · ĐVT · Quy đổi VND · Nguồn · Ghi chú.

## Gotchas already found and fixed — do not rediscover these

- **PDF/DOC are not downloaded by default.** `build.py` only ever parsed xml/xls/xlsx. On
  Hưng Yên the 258 PDF/DOC attachments were **98.4% of the bytes (889 of 903 MB) and produced
  zero rows**; fetching them cost 483s of a 524s run. Skipping them took the province to 70s
  with a byte-identical 82,220-row result. 235 of those 258 files belonged to reports that
  also published XML/XLSX, so they were pure duplication. The other 23 (12 reports) are
  scanned images with a poor OCR layer — a 42-page decision yielded 2,139 digits in total —
  so their tables cannot be read without real OCR, and guessing at them would break the
  "never infer a value" rule. Use `--with-pdf` when the OCR pass gets built.
- **`Pending_Review` is built from `catalog.json`, not from what was downloaded.** Otherwise
  a report whose only attachment is a PDF vanishes from the workbook entirely. Across 34
  provinces about 5.6% of reports publish nothing but PDF/DOC (0% in Tây Ninh, 16% in Lào Cai).
- **Page size above ~50 breaks paging.** `PageSize=200` silently returns 28 rows and then
  empty pages. `PAGE = 50` in `ckns.py`.
- **Every query stops ~50 records short of the server's own `TotalItems`.** Hà Nội returns 178
  of a claimed 228. This is a server limit, not a bug in the code. Closing it needs a
  full-catalog harvest instead of per-province queries. **Still open.**
- **`Year` + `DeparmentId` together return nothing.** Filter by department only.
- **Attachment URLs contain raw spaces** and must be percent-encoded, or every fetch fails.
- **Files named `.xls` are often xlsx zips.** Sniff the magic bytes; do not trust the extension.
- **Some real BIFF `.xls` files carry malformed UTF-16.** `parse_xlsx.py` patches
  `codecs.utf_16_le_decode` to fall back to `errors='replace'`.
- **TT343 XML declares `utf-16` but is `utf-8`.** Decode defensively.
- **Headers span up to 3 merged rows.** They are rebuilt by forward-filling across merged
  cells and joining levels with ` / `.
- **Never match a unit by suffix — match the word before `đồng`.** Any suffix test treats every
  string ending in `"đồng"` as plain đồng. That made `"triệu đồng"` factor 1 once (values
  1,000,000x too small), and the same shape came back a second time through `"Tiệu đồng"`
  (166 rows), `"1.000 đồng"` (540) and `"1.000.000 đồng"` (401), all silently resolving to 1.
  `unit_factor` now splits the string and reads the scale word, accepting a stated numeric
  multiplier (`1.000 đồng` → 1,000) and returning **None** for anything else, so an unknown
  spelling leaves `Quy đổi VND` blank instead of wrong.
- **One spelling per unit, by allowlist — never by stripping diacritics.** 22 published ĐVT
  spellings resolve to 4 currency units; `canon_unit` writes the canonical one and the source
  spelling is kept (`Ghi chú` in the workbook, `v_fact.unit_source` in the warehouse). Six
  currency spellings are listed literally in `parse_xml.CURRENCY`/`SCALE`, including the
  attested misspellings `Triệu dồng`, `Tiệu đồng`, `Tr đồng`, `Triệu đổng` — 660 rows that
  named đồng and never converted. Do **not** replace the allowlist with diacritic folding:
  đồng, dồng, đổng, dòng, đóng and động all fold to `dong`, and `dòng` means LINE, so folding
  turns a row count into money. `./nsnn --units` lists every spelling and what it resolves to;
  anything under NOT CONVERTED that names đồng is a new spelling to check by hand.
- **Unit strings arrive in NFD as well as NFC.** Some sources write `ê` as `e`+U+0323 and `ồ` as
  `ô`+U+0300. The string looks identical but never equals an NFC literal, so `"Triệu đồng"` from
  Hưng Yên (1,520 rows) and Lạng Sơn matched nothing and lost its conversion. `clean_unit`
  normalises to NFC first.
- **`Đơn vị:` is not always a unit of measure.** In Vietnamese it also means *organisation*, so
  `"Đơn vị: UBND tỉnh Cao Bằng"` is a department heading. Accepting it put agency names in the
  `ĐVT` column of ~37,000 rows. `Đơn vị tính:` is unambiguous and wins; a bare `Đơn vị:` is only
  trusted when what follows actually looks like a unit.
- **Match the unit inside one cell, never across a flattened row.** Joining a row with spaces
  hides cell boundaries, so a greedy capture swallows every later cell. One row holding
  `"Đơn vị tính: %"`, `"Đơn vị: Triệu đồng"`, `"Đơn vị: Triệu đồng"` in three cells produced a
  unit that resolved to a currency and put a VND value on a percentage table.
- **`dim_indicator.depth` is not the outline level.** It counts how many markers the ETL
  stripped, and every label carries exactly one, so 3.32M of 3.8M rows are `depth=1`. In B63
  the section head `A TỔNG THU CÂN ĐỐI NSNN`, the roman `I Thu nội địa`, the arabic `6 Thuế
  bảo vệ môi trường` and the leaf `- Thuế BVMT thu từ hàng hóa nhập khẩu` are all depth 1.
  Filtering on it to get "top-level items" returns the whole form and double counts by 3.6-7x.
  Parse the marker back out of the raw label (`mcp_server/warehouse.block_levels`).
- **Key a cell on `indicator_raw`, never on the cleaned `indicator`.** The outline marker is
  part of the identity: `1.1 Chi giáo dục` is the investment line under `I Chi đầu tư phát
  triển` and `1 Chi giáo dục` the recurrent one under `II Chi thường xuyên`, differing by 6.1x
  in Bắc Ninh 2017. Keying on the cleaned label merges them and makes 1,555 B65 cells, 675
  B64 and 337 B50 ambiguous; keying on the raw label leaves exactly 0 on all six main forms.
  `dashboard/aggregate.py` keys on `clean` and drops those cells, which is why its comments
  call B65 unusable for sector figures — on the raw key it is usable.
- **Summing rows double counts, always.** A parent and its children are both rows, so
  `SUM(vnd)` over one province-year of B46 is 3.95x-6.98x that province's own published total
  (mean 5.75x, n=138). Scope children positionally by `fact_row.rowid` between a parent and
  the next row at its level or shallower, take only the level directly below, and reconcile
  against the published parent.
- **A bare `I` is a section letter in some forms and a roman numeral in others — and both in
  the same form.** 45/CK-NSNN and 58/CK-NSNN letter their sections A…H, I, K AND use romans
  I, II, III as agency headings under every one of them. Deciding it once per block (does the
  block contain an `H`?) shipped once and made `break_down` assert "is a leaf" for **9,919
  section rows in 1,259 blocks across 20 provinces** that have children. `block_levels`
  resolves it per row by lookahead: from a bare `I`, reaching `II` before another bare `I` or
  another section letter means a roman run opened here. Never decide this per block.
- **An unparseable outline marker is a hard stop, never a wildcard.** 19,433 distinct labels
  carry no marker at all, including every headline total (`TỔNG CHI NSĐP`, `TỔNG THU NGÂN
  SÁCH NHÀ NƯỚC`). Treating "unknown" as "shallower than everything" made a breakdown swallow
  whole sibling sections and still report that it reconciled.
- **Scope a positional scan to the parent's own report AND table.** 4,016 of 83,559
  (province, year, form, series) blocks span more than one report, because two reports can
  publish the same form code and a same-named series column in the same year. Without it a
  Cà Mau parent of 977 tỷ collected 28 children from another document summing 52,850 tỷ.
- **Never let a non-zero VND render as `0`.** 34,306 rows carry a real value below 500,000
  VND, which three decimals of tỷ đồng rounds away. Printing those as `0` makes a published
  figure indistinguishable from a published zero — the blank-is-not-zero rule, inverted at
  the formatting layer instead of the parsing one.
- **SQLite reads a negative LIMIT as "no limit".** An unclamped caller-supplied `limit` of -1
  returned the whole table: 2.5 MB of text through one tool call. Clamp every count.
- **757 values across 25 provinces sit 100x or further from their own cell's history.** They
  pass every unit check because the declared unit is right; the usual shape is a thousands
  separator read as a decimal point (Đồng Nai's 2021 B46 total is `28.709234` where 2020 and
  2022 are `29106050` and `23556345`). Not corrected — flagged, and listed by
  `nsnn_list_data_quality(block='magnitude')`.
- CKNS name aliases: the Source Master says `Huế` and `TP Hồ Chí Minh`; CKNS says
  `Thừa Thiên Huế` and `Hồ Chí Minh`. Handled by `ALIAS` in `run.py`.

## Scope limit — important

This pipeline covers **only the Ministry layer**: provincial aggregate budget forms, which is
the backbone plus much of checkpoint CP3.

It does **not** cover agency-level disclosures published on province portals — the 12
mandatory agency groups, special agencies, and predecessor-province archives (CP3/CP4/CP5).
Measured evidence: the manual `samples/Hai_Phong_budget_full_scan_COMPLETE` file drew 100%
from province portals and **zero** rows from CKNS; indicator overlap with the pipeline output
is near zero.

So the two are complementary. **Never present a CKNS workbook as replacing a manual file, and
never mark a province COMPLETE on CKNS data alone.** A portal crawler for the agency layer is
not built yet.

## Status

All 34 provinces are built and committed under `output/` (tracked by Git LFS): 3,814,427 rows,
0 failed reports, 500 entries across the Pending_Review sheets. Run `./nsnn --status` for the
live list.
Manual, portal-sourced, kept in `samples/`: Hải Phòng, Huế.

`mcp_server/` serves the warehouse to AI agents over MCP (stdio or streamable HTTP): 15
read-only tools, 97 checks in `mcp_server/test_server.py`. Two rounds of adversarial review
found and fixed 26 defects — see `docs/2026-09-13-mcp-server.md`. Run the tests with
`.venv/bin/python mcp_server/test_server.py` after touching anything under `mcp_server/`.

## Working agreement

Every rule here was paid for by something going wrong in this project. None of it is generic
advice; the cost is named so you can judge whether it still applies.

**Measure it; do not repeat a claim.** The README said a province took ~5 minutes. Measured,
it was 524s. `--status`, a stopwatch and a row count are cheap. A number you did not measure
does not go in a commit message, a doc, or an answer to the owner.

**Check a premise against the corpus before building on it.** Three separate premises that
everyone believed turned out false: `dim_indicator.depth` is not the outline level, keying a
cell on the cleaned label merges different lines, and `mode=ro` is not a sandbox. Each was one
query away from being disproved, and each would have been load-bearing.

**Reproduce a bug report before fixing it.** A review finding once had its diagnosis exactly
inverted and one of its quoted outputs fabricated; fixing from the report as written would
have made the bug worse. Run the repro, read the real output, then fix what you actually saw.

**A fix is not finished until you re-run the case it was for AND its neighbours.** The fix for
the bare-`I` levelling bug caused the worst defect of the next review round — 9,919 rows
wrongly reported as childless. One green test is not evidence that a change was safe.

**Every fix gets a regression test.** `mcp_server/test_server.py` has 97 checks and several
exist only because that line broke once. Run it after touching anything under `mcp_server/`.

**Never correct the source data.** When a published figure is clearly wrong — 757 of them sit
100x from their own history — flag it, show the whole series, and leave the number exactly as
published. Deciding what the source meant is the owner's call, not the code's. This is the
same rule as "never infer a value", applied one layer out.

**Report your own errors plainly, including shipped ones.** If something you already told the
owner turns out wrong, say so first and unprompted, before the rest of the update. Two rounds
of review is not a proof of correctness; say that too rather than implying the work is
finished.

**Documentation is part of the change, not a follow-up.** Three commits shipped here before
anyone noticed `README.md` never mentioned `mcp_server/` and a `CLAUDE.md` pointer had gone
stale. A commit that changes behaviour updates: this file (a new gotcha), the relevant
`README.md`, and a dated note in `docs/` if the work was substantial.

**Language split.** `README.md` is for a non-programmer owner and is written in Vietnamese.
`CLAUDE.md`, `docs/`, code comments and commit messages are English. The owner writes in
Vietnamese — reply in Vietnamese unless asked otherwise, and follow the most recent
instruction about a specific deliverable.

**Report numbers honestly**, including failed reports and the open 50-record gap. If a rule in
this file would have to be broken to make output look better, stop and say so instead.
