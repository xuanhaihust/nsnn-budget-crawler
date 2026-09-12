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

## Working agreement

Report numbers honestly, including failed reports and the open 50-record gap. If a rule above
would be broken to make output look better, stop and say so instead.
