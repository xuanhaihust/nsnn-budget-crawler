# Unit correctness audit — 12 Sep 2026

An audit of the whole pipeline against the hard data rules found four defects in how the unit
of measure is read. Two of them put **wrong numbers** in `Quy đổi VND`; two left correct
conversions **missing**. All four are fixed, all 34 provinces were rebuilt, and every rule now
passes across 3,814,427 rows.

The most serious finding is a repeat of the failure `CLAUDE.md` already records as having
shipped once: a unit matched by suffix, resolving to factor 1.

---

## How the audit was run

Two passes, because the first was too slow to be useful.

1. **A subagent workflow** over eight dimensions (number parsing, units, spreadsheet structure,
   silent drops, dedupe, metadata columns, coverage, the API client) with three adversarial
   verifiers per finding. It was **abandoned after ~40 minutes**: the container has 4 CPUs, so
   the workflow cap was 2 concurrent agents and no finder had completed.
2. **Deterministic checks** run directly over the real corpus — rebuilding all 34 provinces in
   memory from the cached sources and querying the resulting rows. This found everything below
   in a fraction of the time, and is reproducible.

Worth recording: the slow path produced nothing, and the cheap path found four real bugs. For
numeric and statistical questions over a fixed corpus, a query beats a panel of agents.

## What was wrong

### 1. Units matched by suffix — values 1,000 to 1,000,000 times too small

`unit_factor` tested `u.endswith(' ' + k)`, so **any** string ending in `đồng` matched the
plain-`đồng` entry and got factor 1.

| `ĐVT` in the source | factor applied | correct factor | rows | error |
|---|---|---|---|---|
| `1.000 đồng` | 1 | 1,000 | 540 | 1,000x too small |
| `1.000.000 đồng` | 1 | 1,000,000 | 401 | 1,000,000x too small |
| `Tiệu đồng` (typo) | 1 | — | 166 | 1,000,000x too small |

`CLAUDE.md` warned about exactly this shape after `"triệu đồng"` once resolved to factor 1. The
warning said to try the longest name first; that fix held for the spellings known at the time
and failed for these three.

**Fix.** `unit_factor` now splits the string and reads the word before `đồng`: a known scale
word (`nghìn`, `triệu`, `tỷ`, and the variants `ngàn`, `tỉ`), or a stated numeric multiplier
(`1.000 đồng` → 1,000, `1.000.000 đồng` → 1,000,000, restricted to powers of ten). Anything
else returns **None**, so an unrecognised spelling leaves `Quy đổi VND` empty instead of wrong.

That deliberately leaves 635 rows unconverted — `Triệu dồng` (347), `Tiệu đồng` (166),
`Tr đồng` (122), `Triệu đổng` (25). These are real misspellings in the published sources. A
human reads them instantly, but repairing them is guessing, and the rules forbid it. Blank and
honest beats confident and wrong.

### 2. NFD-encoded Vietnamese never matched

Some sources write Vietnamese decomposed: `ê` as `e` + U+0323, `ồ` as `ô` + U+0300. The string
is visually identical to the NFC form and compares unequal to every literal in the unit table.

| Province | `ĐVT` | rows | was | now |
|---|---|---|---|---|
| Hưng Yên | `Triệu đồng` (NFD) | 1,520 | no conversion | 1,000,000 |
| Hưng Yên | `Triệu đồng` (partly NFD) | 134 | no conversion | 1,000,000 |
| Hưng Yên | `đồng` (NFD) | 51 | no conversion | 1 |
| Lạng Sơn | `triệu đồng` (NFD) | 21 | no conversion | 1,000,000 |

**Fix.** `clean_unit` normalises to NFC before anything else.

### 3. `Đơn vị:` also means "organisation"

Vietnamese `đơn vị` means both *unit of measure* and *organisation*. `RE_UNIT` matched both, so
department headings landed in the `ĐVT` column:

| `ĐVT` written into the data | rows |
|---|---|
| `UBND tỉnh Cao Bằng` | 16,648 |
| `Ban Quản lý dự án đầu tư xây dựng các công trình Giao thông` | 15,696 |
| `Trạm Chăn nuôi thú y và Thủy sản, …` | 2,669 |
| various `Sở …`, `Ủy ban nhân dân huyện …`, `Trung tâm …` | ~1,960 |

About **37,000 rows** carried an agency name where a unit belongs, and their money values went
unconverted as a result.

**Fix.** `Đơn vị tính:` is unambiguous and wins outright. A bare `Đơn vị:` is trusted only when
what follows actually looks like a unit — it resolves to a known unit, contains `%`, or is a
short token with no organisation keyword.

### 4. A greedy capture ran past its own cell

`RE_UNIT` was matched against a row *flattened* to a single string, so cell boundaries were
invisible and `(.+)` swallowed every later cell. One row in Tuyên Quang held three separate
cells — `"Đơn vị tính: %"`, `"Đơn vị: Triệu đồng"`, `"Đơn vị: Triệu đồng"` — and produced the
unit `"% Đơn vị: Triệu đồng Đơn vị: Triệu đồng"`, which resolved to a currency and put a VND
value on a **percentage** table: `100.0%` became `100,000,000 VND`.

**Fix.** `find_unit` matches one cell at a time, so a capture can never leave the cell it was
found in, falling through to the next non-empty cell when the label and its value are split.
`unit_factor` additionally refuses any string naming both a percentage and a currency.

## Result

All 34 provinces rebuilt from the cached sources (no re-download), then swept end to end.

| Check | Before | After |
|---|---|---|
| Blank turned into a value | 0 | **0** |
| `-` read as zero | 0 of 505 | **0 of 505** |
| VND filled with no known unit | 72,485 | **0** |
| VND on a percentage row | 532 | **0** |
| VND arithmetic wrong | 872 | **0** |
| Commune/ward rows | 0 | **0** |
| Empty province name | 0 | **0** |
| `ĐVT` longer than 40 chars (polluted) | 96,336 | **0** |
| Distinct `ĐVT` spellings | 129 | **21** |
| Rows with a VND value | 1,969,683 | **1,992,983** |
| Rows total | 3,814,427 | **3,814,427** |
| Pending_Review entries | 500 | **500** (the coverage pass below then raises it to 527) |

Row count and Pending_Review are unchanged here, which is the point: these fixes correct the unit
and conversion columns without adding, losing, or reclassifying a single row. The VND count
rises by 23,300 because conversions that were wrongly missing are now present.

Every surviving `ĐVT` value and the factor applied to it:

```
2,621,055  x1,000,000  Triệu đồng          540  x1,000      1.000 đồng
  156,467  none        %                   439  x1e9        Tỷ đồng
  156,434  x1,000,000  triệu đồng          401  x1,000,000  1.000.000 đồng
   65,875  x1          đồng                347  none        Triệu dồng   (typo)
   65,597  x1          Đồng                280  none        Phần trăm (%)
    2,741  none        % (phần trăm)       166  none        Tiệu đồng    (typo)
    2,733  x1e9        tỷ đồng             165  none        %(phần trăm)
      160  x1,000      Nghìn đồng          159  none        đơn vị
       98  x1,000      1000 đồng           155  none        dự án
      122  none        Tr đồng   (abbrev)   25  none        Triệu đổng   (typo)
```

## Coverage fixes in the same pass

Three of the open items from the first draft of this note were closed rather than left standing.

**Reports that published no attachment at all are no longer dropped.** `build.py` skipped them
with `if not atts: continue`, so 27 catalog reports across the 34 provinces reached neither
`Data` nor `Pending_Review` — a quiet drop, which the rules forbid. They now land in
`Pending_Review` under a new method `PENDING_NO_FILE`, counted separately in `Summary_QA`.

**Every pending entry now carries a reason.** A report that yielded nothing previously reached
`Pending_Review` with an empty error cell, so there was no way to tell a broken parser from a
genuinely empty sheet. Each outcome now names itself:

| Method | Reason written into the sheet |
|---|---|
| `PENDING_NO_FILE` | the catalog lists this report with no attachment at all |
| `PENDING_PDF_DOC` | only non-machine-readable formats published (`doc,pdf`) |
| `PARSE_FAILED` | a machine-readable attachment is listed but was not downloaded |
| `PARSE_FAILED` | parsed N machine-readable file(s) without error but found no table rows |
| `PARSE_FAILED` | the exception, when one was raised |

**`.rar` attachments are not a coverage gap.** 16 archive attachments exist (Cà Mau 10,
Khánh Hoà 6) plus 3 `.crdownload` partials in TP Hồ Chí Minh. Khánh Hoà names its archives
after TT343 form codes, which looked alarming. Checked every one: **all 19 reports also publish
an XML or XLSX sibling**, so nothing is lost by never opening them. No work needed.

After these changes, across all 34 provinces:

| | Before | After |
|---|---|---|
| Catalog reports tracked in neither sheet | 27 | **0** |
| Reports tracked | 8,633 | **8,660** |
| Pending_Review entries | 500 | **527** |
| Pending entries with an empty reason | 238 | **0** |
| Rows | 3,814,427 | **3,814,427** |

Method breakdown: XML 5,976 · XLSX 2,157 · PARSE_FAILED 238 · PENDING_PDF_DOC 262 ·
PENDING_NO_FILE 27.

## Still open

> **CLOSED 2026-09-13.** The owner decided: these are now normalised by an explicit
> allowlist, and the count was 660 rather than 635 (the earlier filter missed `Triệu đổng`,
> whose `ổng` the pattern did not match). See `docs/2026-09-13-unit-canonicalisation.md`.

1. **635 rows carry a misspelled currency unit** — `Triệu dồng` (347), `Tiệu đồng` (166),
   `Tr đồng` (122), `Triệu đổng` (25) — and are left without a VND conversion. A human reads
   them instantly, but repairing them is guessing, and `never infer a value when the unit is
   unclear` forbids it. Whether an obvious misspelling should be normalised is the project
   owner's call, not the pipeline's.
2. **238 reports still parse to zero rows.** They now say why, which makes them diagnosable
   rather than invisible, but they are not yet recovered. The largest group parses without
   raising and finds no table — worth attacking next, the same way the BIFF misdetection was.
3. The ~50-record server gap and the Ministry-layer-only scope limit are unchanged by any of
   this work.
