# Speed optimization — 11 Sep 2026

A province took **524s**. It now takes **70s**, producing an identical workbook. This note
records how that was measured, what was found inside the discarded downloads, and what is
still unverified.

All figures come from real runs against the live CKNS API, not estimates, unless a line says
otherwise. The reference province is **Hưng Yên** (281 reports, mid-sized: the median province
claims 326 reports).

---

## 1. Where the time actually went

Baseline, Hưng Yên, cold run, code at commit `4b7c591`:

| Phase | Time | Share |
|---|---|---|
| Catalog paging (6 pages, sequential) | 24.8s | 5% |
| **Downloading attachments** (731 files, 903 MB, 8 threads) | **483.4s** | **92%** |
| Parsing all 473 machine-readable files | 1.2s | 0.2% |
| Writing the xlsx (openpyxl) | 11.6s | 2% |
| **Total** | **524s** | |

A warm re-run took 36s, of which ~25s was re-fetching the catalog it had already stored.

Parsing is essentially free. Optimizing anything other than the download phase was pointless.

## 2. The finding: 98.4% of downloaded bytes were never read

`build.py` has only ever parsed `.xml`, `.xls` and `.xlsx`. Every PDF and DOC was downloaded,
hashed, stored — and then skipped.

Hưng Yên's 731 downloaded files:

| Format | Files | Size | Share of bytes |
|---|---|---|---|
| `.pdf` | 135 | 883.2 MB | 97.7% |
| `.xls` | 74 | 7.2 MB | 0.8% |
| `.xlsx` | 195 | 6.0 MB | 0.7% |
| `.doc` | 86 | 4.3 MB | 0.5% |
| `.xml` | 204 | 1.6 MB | 0.2% |
| `.docx` | 37 | 1.2 MB | 0.1% |
| **Total** | **731** | **903.5 MB** | |

Split by whether the pipeline can read the file at all:

| | Files | Size |
|---|---|---|
| Parseable (`xml`/`xls`/`xlsx`) | 473 | **14.8 MB** |
| Never parsed (`pdf`/`doc`/`docx`) | 258 | **888.7 MB — 98.4%** |

The cost gap per file is just as wide. Measured at 8 threads:

| | Seconds per file | Median latency |
|---|---|---|
| Parseable file | 0.21s | 1.45s |
| PDF | **4.09s** | **15.65s** |

121 of the 258 PDF/DOC files were byte-identical duplicates of each other (same SHA-256,
different URLs — the same scanned decision attached to several reports). No URL was ever
fetched twice, so the existing path cache was working correctly; the waste was inherent to
fetching PDFs at all.

## 3. Do we need anything inside those PDFs?

Checked directly rather than assumed.

**91% of them are redundant by construction.** Of the 258 PDF/DOC files, **235 belong to
reports that also published XML or XLSX** — the same disclosure in a machine-readable form.
Only 23 files, across 12 reports, are a sole source.

Those 12 sole-source reports fall into two kinds:

1. **Administrative letters, 1–2 pages** (`BC 78-UBND`, `CV 391` …), titled *"Báo cáo tình
   hình thực hiện công khai…"*. These are narrative reports *about* having disclosed. They
   contain no budget table, so there is nothing to map onto the 16 columns.

2. **Disclosure decisions, 9–44 pages** (`QĐ 22 … CK DT2021`, `QĐ 25 … CKDT 2022`,
   `QĐ 2880 … CKQT2020` …). These **do** contain budget tables.

The second kind was opened page by page. Every page is a **full-page scanned image**
(1650×2346 px, roughly A4 at 200 dpi) with a low-quality OCR layer beneath it. The 42-page
`QĐ 22` yields **2,139 digits in total across all 42 pages**, and only ~29 digits on a table
page — a real budget table page would carry hundreds. Vietnamese text comes out as noise
(`uy n^tN NSAN nAN` for `UỶ BAN NHÂN DÂN`).

So their tables are not machine-readable today. Reconstructing values from that OCR layer
would mean guessing, which the `never infer a value` rule in `CLAUDE.md` forbids. The correct
place for these reports is `Pending_Review`, which is where they already are.

Cross-check: all six years covered by those 12 reports (2018–2023) are already present in the
parsed output, with substantial volume (Năm 2021 alone: 14,940 rows). This is strong evidence
of redundancy, though **not** a table-by-table proof.

### How far this generalizes

Sampled across all 34 provinces (first catalog page each, 5,215 attachments):

- **55.2% of attachments are machine-readable**, matching the 57% previously documented.
  Per-province range: **38% – 85%**.
- Format mix: pdf 1,312 · xml 1,273 · xlsx 938 · xls 666 · doc 568 · docx 445 · **rar 10** ·
  **crdownload 3**.
- **5.6% of reports (95 of 1,700) publish nothing but PDF/DOC.** Range: **0%** (Phú Thọ, Tây
  Ninh, Đồng Tháp, An Giang) to **16%** (Lào Cai); Ninh Bình 14%; Hà Nội, Hải Phòng, Lai Châu
  and Bắc Ninh 12%.

That 5.6% is exactly the set that must keep reaching `Pending_Review` — see §4.

## 4. Changes made

| # | Change | Measured effect |
|---|---|---|
| 1 | `harvest.py` fetches only `xml/xls/xlsx`; `--with-pdf` restores the old behaviour | download 483.4s → 89.9s |
| 2 | Download threads 8 → 16 | download 89.9s → 46.1s |
| 3 | `ckns.py` fetches catalog pages in parallel | 24.8s → ~5s |
| 4 | `build.py` walks `catalog.json` instead of the downloaded files | correctness, not speed |

**On thread count.** 48 parseable files, same sample:

| Threads | Wall | Median latency | Per file |
|---|---|---|---|
| 8 | 9.7s | 1.25s | 0.20s |
| 16 | 6.1s | 1.61s | 0.13s |
| 32 | 5.1s | **2.84s** | 0.11s |

16 is the stopping point: going to 32 buys 1.2x more throughput while more than doubling
median latency, which is the server queuing. `CLAUDE.md` already warns the server can start
refusing.

**On change 4 — this one is a correctness requirement, not an optimization.** `build.py` used
to derive its report list from what had been downloaded. Once `harvest.py` stops downloading
PDFs, a report whose *only* attachment is a PDF has no downloaded file, so it would disappear
from the workbook entirely — no `Data` rows and no `Pending_Review` entry either. That breaks
the `Never quietly drop a failed report` rule, and it would have silently removed **12 of
Hưng Yên's 281 reports**, or about 5.6% of reports nationally. `build.py` now iterates
`catalog.json` and takes each report's format list from the catalog, so `Pending_Review` still
shows `doc,pdf` for files that were deliberately never fetched.

## 5. Results

**Hưng Yên, cold, before vs after:**

| | Before | After |
|---|---|---|
| Wall clock | 524s | **70s** (7.5x) |
| Rows | 82,220 | **82,220** |
| Reports tracked | 281 | **281** |
| `Pending_Review` entries | 15 | **15** |
| Method split | XML 198 / XLSX 68 / pending 12 / failed 3 | identical |
| Files downloaded | 731 (903 MB) | 473 (**18 MB**) |

**Three provinces together** (`--jobs 3`, 16 threads each = 48 concurrent connections),
**0 download failures**:

| Province | Reports | Time | Rows |
|---|---|---|---|
| Cà Mau (largest in the country) | 364 | 190s | 165,610 |
| Ninh Bình | 224 | 91s | 76,901 |
| Lào Cai | 124 | 47s | 28,524 |

Disk for all four provinces combined: **323 MB**, against 866 MB for Hưng Yên alone before.

### How equivalence was verified

1. Ran old and new `build.py` over the *same* work directory and compared rows as a multiset
   (order-independent, since report iteration order changed): **identical — 0 rows added,
   0 lost**. Row order differs at 7,061 of 82,220 positions; the data does not.
2. Compared the parallel-fetched `catalog.json` against the sequential one: **same 281 report
   IDs, same page order**.
3. Opened the produced workbook: `Data` 82,220 rows, `Pending_Review` 15 entries still listing
   `doc,pdf` formats for reports whose files were never downloaded, `Summary_QA` counts
   unchanged.

## 6. Open items

Stated plainly rather than rounded off.

1. **Row-level equivalence is proven for Hưng Yên only.** It is the one province with a
   pre-change baseline. Cà Mau, Ninh Bình and Lào Cai were run once each, with nothing to
   compare against. Their row counts are plausible, not verified.
2. **"15–25 minutes for all 34 provinces" is an extrapolation from 4 provinces**, not a
   measured end-to-end run. The 1–3 minutes per province figure is measured.
3. **A pre-existing gap, deliberately left alone.** Ninh Bình publishes 1 report with **no
   attachment at all**. It is dropped from `status` entirely, so it appears in neither `Data`
   nor `Pending_Review`. The old code behaved the same way. Whether an attachment-less catalog
   entry counts as a "failed report" under the hard rules is a call for the project owner.
4. **`.rar` and `.crdownload` attachments are never opened.** 10 and 3 respectively in the
   sample. A `.rar` could contain XML or XLSX, which would be a genuine data gap rather than a
   performance question. Not investigated.
5. **PDF content was inspected for one province only.** Other provinces' PDF-only reports may
   be born-digital and text-extractable rather than scans. If OCR is ever built, re-run with
   `--with-pdf` and check before assuming they are all scans.
6. **Unchanged:** the ~50-record server gap (Hưng Yên returns 281 of a claimed 331), and the
   Ministry-layer-only scope limit. Neither is affected by this work.

## 7. Not done

- **Catalog caching on rebuild.** A warm rebuild still re-fetches the catalog (~5s now that
  paging is parallel, down from ~25s). Reusing the stored `catalog.json` would cut a rebuild
  to roughly 13s, at the cost of staleness. Skipped to keep the change small; it needs a
  `--refresh` style flag to be safe.
