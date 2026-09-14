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
- CKNS name aliases: the Source Master says `Huế` and `TP Hồ Chí Minh`; CKNS says
  `Thừa Thiên Huế` and `Hồ Chí Minh`. Handled by `ALIAS` in `run.py`.
