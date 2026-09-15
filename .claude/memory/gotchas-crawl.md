<!-- Imported by CLAUDE.md. Each entry cost a real bug; the cost is named so a
     future reader can judge whether it still applies. -->

# Crawling and parsing — traps already hit

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
- **The "~50 records short" gap was paging, not a server limit — `PageIndex=0` returns
  everything.** This was documented here for months as "a server limit, not a bug in the code"
  and listed in `CLAUDE.md` as open and unfixable. It was never tested; it was inferred from
  the paged walk running out early. One parameter closes it. Measured on Cà Mau
  (TotalItems=414): `PageIndex=1 PageSize=50` → 50 per page and the walk ends at ~364;
  `PageIndex=1 PageSize=200` → 200 then page 2 returns 0; `PageIndex=1 PageSize=2000` → 0;
  **`PageIndex=0 PageSize=2000` → 414**, and `PageSize=5000` also 414, so the ceiling is
  TotalItems and not PageSize.
  What it cost: the paged walk had **8,130 of 10,360** reports across the 34 provinces —
  **2,230 missing** — and the shortfall lands on the newest data, **1,195 of the 1,661**
  reports for period years 2024–2026. That is why the warehouse looked like it had nothing
  recent: 0 reports for 2026 and 19 for 2025, while CKNS publishes 154 and 1,026.
  `catalog()` now calls `PageIndex=0` first and falls back to the paged walk if it comes back
  short. **Re-running `./nsnn` is what actually ingests the missing reports — the fix to the
  client does not backfill the warehouse by itself.**
  This is the working agreement's "test a constraint before declaring it" rule, paid for a
  second time: a claim that something is impossible stops the owner asking for it, so it needs
  more evidence than a claim that something works, not less.
- **`Year` + `DeparmentId` together return nothing.** Filter by department only.
- **Attachment URLs contain raw spaces** and must be percent-encoded, or every fetch fails.
- **Files named `.xls` are often xlsx zips.** Sniff the magic bytes; do not trust the extension.
- **Some real BIFF `.xls` files carry malformed UTF-16.** `parse_xlsx.py` patches
  `codecs.utf_16_le_decode` to fall back to `errors='replace'`.
- **TT343 XML declares `utf-16` but is `utf-8`.** Decode defensively.
- **Headers span up to 3 merged rows.** They are rebuilt by forward-filling across merged
  cells and joining levels with ` / `.
- CKNS name aliases: the Source Master says `Huế` and `TP Hồ Chí Minh`; CKNS says
  `Thừa Thiên Huế` and `Hồ Chí Minh`. Handled by `ALIAS` in `run.py`.

## Screenshots for the READMEs

- **`docs/make_images.py` regenerates every image under `docs/images/`** from live data, so a
  README image is a reproducible claim rather than a stale one. Rerun it after anything that
  changes tool output or the dashboard. Two traps, each of which took several wrong fixes
  before the real cause showed up.
  **DejaVu Sans Mono has no glyph for `ế`, `ồ`, `ữ`** — it is the default `monospace` in
  headless Chromium and renders them as base letter + spacing accent, so `Thuế` comes out
  `Thuê´` and `tỷ đồng` as `tỷ đôǹg`. It reads as data corruption and is not one. Liberation
  Mono covers Vietnamese and is embedded base64, because a `file://` page will not fetch a
  `file://` font. And **`<pre>` does not inherit the body font**: the UA stylesheet's
  `pre{font-family:monospace}` beats inheritance, so the face must be named on the `pre` rule
  itself — otherwise the font loads, reports `loaded`, and is never used.
