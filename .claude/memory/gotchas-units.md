<!-- Imported by CLAUDE.md. Each entry cost a real bug; the cost is named so a
     future reader can judge whether it still applies. -->

# Units and the VND column

- **Never match a unit by suffix — match the word before `đồng`.** Any suffix test treats every
  string ending in `"đồng"` as plain đồng. That made `"triệu đồng"` factor 1 once (values
  1,000,000x too small), and the same shape came back a second time through `"Tiệu đồng"`
  (166 rows), `"1.000 đồng"` (540) and `"1.000.000 đồng"` (401), all silently resolving to 1.
  `unit_factor` now splits the string and reads the scale word, accepting a stated numeric
  multiplier (`1.000 đồng` → 1,000) and returning **None** for anything else, so an unknown
  spelling leaves `Quy đổi VND` blank instead of wrong.
- **One currency unit: `VND`. `Giá trị chuẩn hóa` holds the VND figure, not the published
  number.** đồng, nghìn đồng, triệu đồng and tỷ đồng are four scales of one currency, so all
  14 published currency spellings collapse to `VND` and the value is multiplied out. Nothing
  is lost: `Giá trị gốc` keeps the published text, `Ghi chú` keeps `ĐVT nguồn: …`, and
  `v_fact.unit_source` returns the original spelling (`etl.py` reads it back out of `Ghi chú`,
  because the ĐVT column no longer carries it). Safe for every real figure — 5,501 rows exceed
  float64's exact-integer range once multiplied out and **all** of them are rows already
  flagged implausible; the largest real provincial budget is ~1.5e14 against an exact range of
  9.007e15. Idempotent: `unit_factor('VND')` is 1, so re-running a conversion multiplies by 1,
  and `triệu VND` is refused outright rather than doubled.
- **Unit spellings are matched by allowlist — never by stripping diacritics.** 22 published ĐVT
  spellings resolve to 6 units (`VND`, `%`, two counts, one agency name that leaked in,
  and blank); `canon_unit` writes the canonical one. Six
  currency spellings are listed literally in `parse_xml.CURRENCY`/`SCALE`, including the
  attested misspellings `Triệu dồng`, `Tiệu đồng`, `Tr đồng`, `Triệu đổng` — 660 rows that
  named đồng and never converted. Do **not** replace the allowlist with diacritic folding:
  đồng, dồng, đổng, dòng, đóng and động all fold to `dong`, and `dòng` means LINE, so folding
  turns a row count into money. `./nsnn --units` lists every spelling and what it resolves to;
  anything under NOT CONVERTED that names đồng is a new spelling to check by hand. Percentages
  are canonicalised the same way, via `PERCENT`; a string merely containing `%` is not enough,
  because `% so với dự toán` and `tỷ lệ %` name comparison columns, not units.
- **Fixing the unit column does NOT need a re-crawl.** ĐVT, Quy đổi VND and Ghi chú are pure
  functions of what a workbook already holds, so `pipeline/fix_units.py --apply` rewrites them
  in place with no network and no `work/`. `./nsnn` is only for when the SOURCE data changes.
  Applied to all 34 on 2026-09-14: every currency row now reads VND, 595 rows gained a
  conversion, 0 conflicts, and the warehouse migration rescaled 1,864,198 values with the
  total VND figure coming out bit-identical - which is the check that proves a rescale moved
  the scale without moving a number. Runs one process per
  workbook - serially it is a five-hour job. Storage, not compute, is the constraint to watch:
  each full rewrite creates a new set of Git LFS objects for `output/`.
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
