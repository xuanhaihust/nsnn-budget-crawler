# NSNN — Vietnam provincial budget disclosure crawl

Collect published budget data for the **34 current provinces/cities** into one fixed
16-column schema, from CKNS, the Ministry of Finance disclosure portal.

## Run it

```bash
./nsnn                          # all 34 provinces, 5 in parallel
./nsnn "Bắc Ninh" "Hưng Yên"    # only these
./nsnn --status                 # what is built, what remains
./nsnn --units                  # every ĐVT spelling in output/, and which convert
./nsnn --list                   # every CKNS department + id
./nsnn --jobs 3                 # smaller batch if the server throttles
./nsnn --with-pdf               # also store PDF/DOC (for a future OCR pass)
```

One province takes 1–3 minutes and writes `output/<Province>_budget_CKNS_<date>.xlsx`.
Provinces run in separate processes, each writing only to its own `work/<slug>/`, so a batch
cannot collide. Re-running is cheap: downloads are cached by path.

Always use `.venv/bin/python` — the system Python has neither `xlrd` nor `openpyxl`.

| Also | |
|---|---|
| `dashboard/db.py restore` | unpack the warehouse (~18s) before anything queries it |
| `mcp_server/server.py` | 15 read-only MCP tools over the warehouse |
| `pipeline/fix_units.py` | re-apply the unit rules to existing workbooks, offline |
| `dashboard/migrate_units.py` | the same for `data/nsnn.db` |
| `mcp_server/test_server.py` | 133 checks — run after touching `mcp_server/` |
| `docs/make_images.py` | rebuild every README screenshot from live data |

## Layout

| Path | Holds |
|---|---|
| `samples/` | Reference data from a collaborator. **Read-only. Never edit these.** |
| `output/` | Generated workbooks, one per province (Git LFS). |
| `work/<Province>/` | `catalog.json`, `files.json`, `raw/` — every downloaded source file. |
| `pipeline/` | The crawler and parsers. `./nsnn` is the only entry point you need. |
| `data/nsnn.db.xz` | The 3.8M-row SQLite warehouse, packed to 33 MB. Ordinary git object. |
| `dashboard/` | `index.html` over the warehouse, plus the ETL. See `dashboard/README.md`. |
| `mcp_server/` | The warehouse as an MCP server. See `mcp_server/README.md`. |
| `docs/` | Dated notes on substantial changes, with the numbers behind them. |
| `docs/images/` | README screenshots. Generated — never edit one by hand, rerun the script. |

`work/raw/` is the evidence trail: every row traces back to a stored file with a SHA-256.
Do not delete it — a province is ~10–250 MB.

## Data source

CKNS's page calls an undocumented JSON API that answers anonymously:

```
POST https://ckns.mof.gov.vn/_vti_bin/DeptService.svc/SearchReport
{"SiteId":"e9e24430-…","WebId":"60d972cc-…","ListId":"dde649ff-…",
 "DeparmentId":<id>,"PageIndex":1,"PageSize":50, …}
```

~20,000 reports. Each record carries province, title, form code, period, circular, approval
date and **direct attachment URLs**. 55% of attachments are machine-readable; the rest is
PDF/DOC, which the pipeline does not download by default.

The richest format is the Circular 343 **XML** — self-describing and near one-to-one onto the
schema. Prefer XML; fall back to the spreadsheet only when no XML exists.

## Hard data rules — do not break these

From the project owner. They override any convenience.

- **Never turn a blank into `0`.** A blank source cell stays blank in every column.
- **A `-` in the source stays `-`.** Do not read it as zero.
- **Never infer a value** when the layout or unit is unclear. Leave it and record it in
  `Pending_Review` instead.
- **A currency figure is filled only when the unit is known.** Percentages and unitless
  tables correctly have none.
- **Never correct the source data.** A figure that is clearly wrong gets flagged and shown as
  published; deciding what the source meant is the owner's call, not the code's.
- Do not crawl commune or ward level (`xã`/`phường`).
- `Tỉnh/TP hiện hành` is the current name; `Tỉnh/TP theo nguồn` keeps the historical name
  exactly as the source wrote it. Never rewrite history retroactively.

## Output workbook

Three sheets: `Data` (the 16 columns), `Summary_QA`, `Pending_Review` (every report that
produced no rows, with the reason). **Never quietly drop a failed report.**

Tỉnh/TP hiện hành · Tỉnh/TP theo nguồn · Số QĐ/Văn bản · Ngày tài liệu · Kỳ dữ liệu · Cơ quan ·
Phạm vi · Nội dung/Bảng · Chỉ tiêu · Loại số liệu · Giá trị gốc · Giá trị chuẩn hóa · ĐVT ·
Quy đổi VND · Nguồn · Ghi chú.

`ĐVT` reads `VND` on every currency row and `Giá trị chuẩn hóa` holds the VND figure;
`Giá trị gốc` keeps the published text and `Ghi chú` the published scale. Details in
`.claude/memory/gotchas-units.md`.

## Scope limit — important

This covers **only the Ministry layer**: provincial aggregate budget forms. It does **not**
cover agency-level disclosures on province portals (the 12 mandatory agency groups, special
agencies, predecessor-province archives — CP3/CP4/CP5). Measured: the manual
`samples/Hai_Phong_budget_full_scan_COMPLETE` drew 100% from province portals and **zero**
rows from CKNS.

They are complementary. **Never present a CKNS workbook as replacing a manual file, and never
mark a province COMPLETE on CKNS data alone.** A portal crawler for the agency layer does not
exist yet.

Two more limits, both measured, that a question about time or the country as a whole runs into
before any query: **the finest time resolution is a quarter, never a month** — `dim_period.kind`
is only `Quý` (always *Quý I*), `6 tháng`, `9 tháng`, `Năm`, and those sub-annual points are
cumulative year-to-date, so a quarterly flow is their difference — and **there is no national
total**: this is 34 provinces' local budgets, with no central budget, bonds, treasury deposits
or OMO, and summing provinces does not produce a country figure.
`docs/2026-09-15-intra-year-cash-flow.md`.

## Status

All 34 provinces built and committed under `output/`: 3,814,427 rows, 0 failed reports, 500
`Pending_Review` entries. `./nsnn --status` for the live list. Manual, portal-sourced files for
Hải Phòng and Huế are in `samples/`.

Open and unfixed: the ~50-record CKNS server gap · 527 reports that produce no rows · 757
values 100x from their own history, flagged not corrected · `dashboard/aggregate.py` still
keys on the cleaned indicator and drops cells the MCP server can read · **the declared unit is
table-level, so a percentage COLUMN inherits the table's currency** — harmless where the cell
reads `121%` (34,232 such rows carry `ĐVT=VND` and not one has a VND figure) but real where it
reads `216.87`, which becomes 216,876,606 VND. Confirmed in An Giang 2020 `66/CK-NSNN`. The
corpus-wide count is **not known**: a `%` in the column name does not settle it, since
`Trong đó: 90% NST` is money and some `SO SÁNH … (%)` columns hold absolute figures.

## Project memory

Detail lives in these, imported here rather than inlined. Each entry names what it cost, so a
reader can judge whether it still applies.

@.claude/memory/working-agreement.md
@.claude/memory/gotchas-crawl.md
@.claude/memory/gotchas-units.md
@.claude/memory/gotchas-analysis.md
