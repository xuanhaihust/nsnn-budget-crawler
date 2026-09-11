---
name: nsnn-crawl
description: Use when crawling, rebuilding, or extending Vietnam provincial budget disclosure data (NSNN) from the Ministry of Finance CKNS portal — running a province, running all 34, checking coverage, diagnosing a parse failure, or changing the parsers. Examples: "crawl Bắc Ninh", "run the remaining provinces", "why did this report fail to parse", "rebuild the output".
---

# NSNN budget crawl

A working pipeline already exists. **Do not write a new crawler.** Read `CLAUDE.md` in the
project root for the data rules and the list of gotchas already solved, then use the commands
below.

## Run a province

```bash
cd <project root>
./nsnn "Bắc Ninh"          # one or more provinces
./nsnn                     # no argument means all 34, 5 in parallel
```

Writes `output/<Province>_budget_CKNS_<date>.xlsx`. Takes about 5 minutes on a cold run and
seconds on a rebuild, because downloads are cached in `work/<Province>/raw/`.

Use `.venv/bin/python`, never `python3` — the system Python lacks `xlrd` and `openpyxl`.

Other entry points:

```bash
./nsnn --status    # which provinces are built, which remain
./nsnn --list      # every CKNS department name and id
./nsnn --jobs 3    # smaller batch if the server throttles
```

The runner batches 5 provinces at a time in separate processes, prints one line per province
as it finishes, and collects failures instead of stopping. Two concurrent provinces measured
0 download failures. All 34 takes roughly 45-60 minutes — run it in the background and report
progress as it lands; do not block on it.

## Before you report a result

Always check the workbook against the owner's rules. A silent rule break is worse than a
smaller row count.

```bash
.venv/bin/python - <<'PY'
import openpyxl, sys
p = sys.argv[1] if len(sys.argv)>1 else 'output/Bac_Ninh_budget_CKNS_2026-09-11.xlsx'
wb = openpyxl.load_workbook(p, read_only=True)
rows = list(wb['Data'].iter_rows(min_row=3, values_only=True))
hdr, data = rows[0], rows[1:]
i = {c: n for n, c in enumerate(hdr)}
bad = leak = dash = 0
for r in data:
    g, s, u, v = r[i['Giá trị gốc']], r[i['Giá trị chuẩn hóa']], str(r[i['ĐVT']] or ''), r[i['Quy đổi VND']]
    if g in (None, '') and s not in (None, ''): leak += 1
    if str(g).strip() in ('-', '–', '—') and s not in (None, ''): dash += 1
    if v not in (None, '') and s not in (None, ''):
        exp = {'triệu': 1e6, 'nghìn': 1e3, 'tỷ': 1e9}
        f = next((k for k in exp if k in u.lower()), None)
        if abs(float(v) - float(s) * (exp[f] if f else 1)) > 1: bad += 1
print(f"rows {len(data)} cols {len(hdr)} | VND errors {bad} | blank-leak {leak} | '-' converted {dash}")
print("pending:", len(list(wb['Pending_Review'].iter_rows(min_row=2, values_only=True))))
PY
```

All three counters must be **0**, and the schema must be **16 columns**. If any is non-zero,
fix the parser — never patch the workbook by hand.

## Diagnosing a report that produced no rows

Open `Pending_Review` in the workbook: it names each report id, the formats available, and
the reason. Two causes are normal:

- `PENDING_PDF_DOC` — only PDF or DOC exists. Parsing those is not built yet.
- `PARSE_FAILED` — usually an unrecognised header layout.

To inspect one file's actual grid:

```bash
.venv/bin/python -c "
import sys,json,pathlib; sys.path.insert(0,'pipeline'); import parse_xlsx
out=pathlib.Path('work/Bac_Ninh')
f=[x for x in json.load(open(out/'files.json')) if x['report_id']==REPORT_ID][0]
for nm,grid in parse_xlsx.sheet_rows(out/f['path']):
    for i,r in enumerate(grid[:14]): print(i, ' | '.join(c[:24] for c in r[:7]))
    break"
```

Header detection lives in `is_header_row` / `RE_IDX` / `RE_LBL` in `parse_xlsx.py`. Broaden
those patterns rather than special-casing a single file.

## Rules you must not break

From the project owner, repeated here because they are easy to violate while "improving"
coverage:

- A blank source cell **never** becomes `0`.
- A source `-` stays `-`.
- Never infer a value from an unclear layout or unit — send it to `Pending_Review`.
- `Quy đổi VND` only when the unit is known.
- No commune or ward (`xã`/`phường`) crawling.
- Never edit anything in `samples/`.

## Scope — say this plainly when reporting

The pipeline covers the **Ministry layer only**: provincial aggregate budget forms.

It does **not** cover agency disclosures on province portals (the 12 mandatory groups,
CP3/CP4/CP5). The owner's manual files come entirely from those portals and barely overlap
this output. Never call a CKNS workbook a replacement for a manual file, and never mark a
province COMPLETE from CKNS data alone.

One open limitation to keep mentioning: every query returns about 50 records fewer than the
server's own `TotalItems`. Closing it needs a full-catalog harvest rather than per-province
queries.
