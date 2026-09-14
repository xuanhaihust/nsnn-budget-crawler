# `mcp_server/` — the warehouse as an MCP server

Fifteen read-only tools over `data/nsnn.db`, so an AI agent can ask questions about 34
provinces' budget disclosures without being handed 3.8 million rows and a hope.

```bash
.venv/bin/python dashboard/db.py restore                   # once: unpack the warehouse (~18s)
.venv/bin/pip install -r mcp_server/requirements.txt       # once: the MCP SDK

.venv/bin/python mcp_server/server.py                      # stdio  — Claude Code, Claude Desktop
.venv/bin/python mcp_server/server.py --transport http     # HTTP   — streamable, on :8931
```

The repo ships a `.mcp.json`, so in Claude Code the stdio server is already wired up: open the
project and the `nsnn_*` tools are there.

---

## Why this is not just "SQL over a database"

The warehouse is a flat projection of hierarchical published forms, so a parent row and its
children are both rows. That single fact makes the obvious query wrong:

```sql
SELECT SUM(vnd) FROM v_fact WHERE province='Nghệ An' AND year=2021 AND form_code='B46'
```

That sum comes to between **3.95x and 6.98x** the province's own published total — mean 5.75x
across the 138 province-years where both can be measured. There is no aggregate an agent can
safely write across this table, and nothing in the data announces the problem.

So the tools never sum. They read **named cells** — one published figure of one named form —
or they use `nsnn_break_down`, which takes a published parent, returns its direct children,
and prints the residual so the caller can see whether it reconciled:

```
published parent  I Thu nội địa = 16658.847
children sum      18 direct children = 16658.849
residual          -0.002  (-0.000% of parent)
reconciles: YES
24 rows sit under this parent in the source; only the 18 at one level deeper are summed.
```

Four things had to be right for that to work.

**Children are scoped by position, not by pattern.** Arabic numbering restarts under every
roman section, so filtering rows whose marker looks like a number mixes the children of `I`
with those of `II`; on one B50 case the residual went to −5,366,746,000,000 VND. `v_fact.id`
is `fact_row.rowid`, which is source row order, so the children of a parent are the rows after
it up to the next row at its level or shallower — and only within the parent's own report and
table, because 4,016 of 83,559 (province, year, form, series) blocks span more than one
report. Without that bound, one Cà Mau parent of 977 tỷ collected 28 children from a different
document summing 52,850 tỷ.

**Only direct children are summed.** Dash rows beneath a numbered line are its parts, not its
siblings. Including them turned that −2 triệu residual into a 2.3 trillion overstatement.

**`dim_indicator.depth` cannot be used for any of this.** It counts how many markers the ETL
stripped, and every label carries exactly one, so 3.32M of 3.8M rows are `depth=1`. In B63 the
section head `A TỔNG THU CÂN ĐỐI NSNN`, the roman `I Thu nội địa`, the arabic `6 Thuế bảo vệ
môi trường` and the leaf `- Thuế BVMT thu từ hàng hóa nhập khẩu` are all depth 1. The outline
level is parsed back out of the raw label by `warehouse.block_levels`.

**A bare `I` cannot be levelled one label at a time, or one block at a time.** In B63 it is a
roman numeral nested under section `A`. In forms 45/CK-NSNN and 58/CK-NSNN it is a section
letter continuing the run A…H, I, K — *and those same forms also use romans I, II, III as
agency headings under every section*, so one block needs it read both ways. Deciding it once
per block shipped once and made `break_down` assert "is a leaf" for 6,377 section rows in
1,137 blocks across 19 provinces that have children. `block_levels` resolves it per row by
lookahead: from a bare `I`, reaching `II` before another bare `I` or another section letter
means a roman run opened here.

**An unparseable marker is a hard stop.** 19,433 distinct labels carry no marker at all,
including every headline total. Treating "unknown" as "shallower than everything" made the
scan swallow whole sibling sections and still report that it reconciled, so `break_down` now
refuses such a parent and says why.

## Cells are keyed on the raw label, marker and all

`1.1 Chi giáo dục` is the **investment** education line under `I Chi đầu tư phát triển`.
`1 Chi giáo dục` is the **recurrent** one under `II Chi thường xuyên`. They differ by 6.1x in
Bắc Ninh 2017. The ETL's `clean` column strips the marker and merges them.

Keying on `clean` leaves ambiguous cells that have to be thrown away; keying on
`indicator_raw` leaves none:

| form | ambiguous on `clean` | ambiguous on `indicator_raw` |
|---|---:|---:|
| B46 | 0 | 0 |
| B49 | 152 | 0 |
| B50 | 337 | 0 |
| B63 | 0 | 0 |
| B64 | 675 | 0 |
| B65 | 1,555 | 0 |

`dashboard/aggregate.py` keys on `clean` and therefore drops those cells — which is why its
comment calls B65 unusable for sector figures. On the raw key B65 is fully usable.

## The tools

| Tool | What it is for |
|---|---|
| `nsnn_describe_corpus` | Start here. The census and the four traps. |
| `nsnn_list_provinces` | What each of the 34 actually published. |
| `nsnn_list_forms` | Forms published widely enough to compare on. |
| `nsnn_find_indicators` | Find an exact label. Diacritics optional. |
| `nsnn_find_forms` | Find a form by its Vietnamese title. |
| `nsnn_read_timeseries` | One named cell, one province, every year. |
| `nsnn_compare_provinces` | One named cell across provinces, with coverage. |
| `nsnn_read_balance_sheet` | Form B46: the one form where ranking is defensible. |
| `nsnn_break_down` | A published parent split into direct children, reconciled. |
| `nsnn_read_revenue_mix` | Where domestic revenue comes from (B63). |
| `nsnn_read_spending_by_sector` | Recurrent spend by sector — provincial tier only. |
| `nsnn_compare_plan_vs_outturn` | Plan against settled accounts, same B63 report. |
| `nsnn_list_data_quality` | What the corpus does not reliably tell you. |
| `nsnn_trace_source` | The untouched source cell behind one number. |
| `nsnn_run_sql` | Escape hatch: one guarded read-only SELECT. |

## Search

An agent will never guess one of 153,272 verbatim Vietnamese labels, so search is the entry
point to almost everything else. It runs on a folded FTS5 index in its own file,
`data/nsnn-search.db`, built on first use in about 12 seconds and gitignored — it is derived
data, so it is rebuilt rather than committed.

FTS5's own `remove_diacritics` is not usable here. `=1` folds `giáo`→`giao` but leaves `tế`
and `đất` alone; even `=2` never folds `đ`→`d`, and `đ` appears in 43.6% of labels. Searching
`dau tu` against unfolded text returns crude oil (`dầu thô`) and a pagoda (`chùa Dâu`) and
nothing about investment. So folding happens in Python — and the usual NFD recipe is wrong
too, because `đ` is U+0111, a distinct letter with no canonical decomposition, so
`unicodedata.combining` returns 0 for it. `warehouse.fold` translates it explicitly.

Results are ranked by **how many provinces publish the label**, never by bm25. 93.3% of
distinct labels are one-province project lines and bm25 rewards short documents, so for
`chi giáo dục` it puts a 1-province/31-row label above the 34-province/9,748-row national one.

## The SQL escape hatch

`nsnn_run_sql` takes arbitrary SQL from an agent, so the guard is five independent layers.
Each catches something the others miss, and each was checked against the real database:

1. `mode=ro` + `PRAGMA query_only` — no writes to the warehouse file.
2. **A deny-by-default authorizer.** `mode=ro` is not a sandbox: on a read-only connection
   `ATTACH` still creates a new writable database and `VACUUM INTO` still writes a full
   352 MB copy. The authorizer is what stops those, plus `PRAGMA writable_schema`,
   `load_extension`, and reads of `dbstat`/`sqlite_stmt` (compiled into this build and absent
   from `sqlite_master`). It returns `SQLITE_DENY`, never `SQLITE_IGNORE` — IGNORE would
   substitute NULL for a denied column and let the query succeed, manufacturing a blank where
   a real value existed.
3. `execute`, never `executescript` — `executescript('SELECT 1; DROP TABLE fact_row')` drops
   the table. `execute` refuses a second statement, and that refusal is the injection defence.
4. A progress-handler deadline, 10s.
5. A streamed row and byte cap. The deadline does **not** bound a single VDBE step:
   `randomblob(1e9)` ran 7.7s past a 3s deadline at 978 MB RSS, so the function allowlist and
   `SQLITE_LIMIT_LENGTH` are what stop that class. And `SELECT * FROM v_fact` under
   `fetchall()` materialises 3,814,427 rows in 71s at 6.5 GB; streamed, its first 200 rows
   cost 2ms.

The cap is never applied by appending `LIMIT` to the agent's SQL. That is a syntax error on
already-limited, `LIMIT..OFFSET`, `VALUES` and CTE queries — and, the dangerous case, it is
silently swallowed when the query ends in a `--` comment, so the cap you think you applied is
gone.

## Output is text, not JSON

Every tool returns pipe-delimited text with constant columns hoisted into a header line, and
registers with `structured_output=False`. That is measured: the same 40-row result costs
**2,409 characters this way against 8,618** as the JSON the SDK produces by default, because
`pydantic_core.to_json(..., indent=2)` has the indent hardcoded and because a `str` return
without `structured_output=False` is emitted twice — once in `content`, once in
`structuredContent`. `|` is a safe delimiter here (no indicator, series or table label
contains one); newlines are not (5.6% of indicator labels embed one), so labels are collapsed.

Money is one rule everywhere: `vnd / 1e9` as **tỷ đồng**, fixed point, never scientific and
never with thousands separators — Vietnamese sources write `146.068` for 146068 and use `,` as
the decimal mark, so a formatted `146,068` invites a 1000x misreading against the source.

## What it refuses to do

The project's data rules are enforced at the serialization layer, not just in the parser:

- **A blank is never a zero.** 1,036,789 rows have no value — 694,405 from an empty source
  cell and 505 from a literal `-`, against 436,635 rows with a genuine `0`. Blanks come back
  empty.
- **Percentage and unitless rows never get a money value.** 48% of the corpus has no VND at
  all, by design.
- **Ambiguity is reported, never resolved.** A cell published twice with different values is
  returned as `AMBIGUOUS` with both values, never averaged or picked between.
- **Nothing is corrected.** 757 values across 25 provinces sit 100x or further from their own
  cell's history — usually a thousands separator read as a decimal point, as when Đồng Nai's
  B46 total publishes `28.709234` in 2021 against `29106050` in 2020. They are flagged
  `MAGNITUDE?` and left exactly as published; `nsnn_list_data_quality(block='magnitude')`
  shows the whole series, because in Đồng Nai's case more years are affected than not, so even
  that cell's own median is one of the bad values.
- **Province names resolve, or fail with the full list.** Folding and a small alias table
  handle `Hanoi`, `TPHCM`, `thua thien hue`. There is deliberately **no** fuzzy did-you-mean:
  difflib answers `Bac Giang` with `An Giang` and `Ha Nam` with `Hà Nội`, confidently and
  wrongly, and an agent that takes the suggestion gets real rows for the wrong province and
  never notices. Pre-merger names cannot be resolved at all, because the warehouse does not
  store the historical source name.

## Files

| File | |
|---|---|
| `warehouse.py` | Read-only connection, Vietnamese folding, outline parsing, money, rendering |
| `sqlguard.py` | The five-layer guard behind `nsnn_run_sql` |
| `search.py` | The folded FTS5 index and its queries |
| `tools.py` | The 15 tool implementations — plain functions, testable without MCP |
| `server.py` | MCP registration and both transports |
| `test_server.py` | `.venv/bin/python mcp_server/test_server.py` |

`tools.py` deliberately knows nothing about MCP, so every rule above can be tested by calling
a function. `test_server.py` holds 97 checks, including a 15-case attack suite and six
concurrent calls.

Two implementation facts worth knowing before editing:

* **Connections are per thread.** The SDK dispatches every sync tool through
  `anyio.to_thread.run_sync`, so concurrent calls land on different worker threads. One shared
  `sqlite3` connection with `check_same_thread=False` deadlocked 6 of 8 trials of two
  overlapping calls. Connections cost 0.23 ms; open one per thread.
* **Clamp every caller-supplied count, and normalise every label argument to NFC.** SQLite
  reads a negative `LIMIT` as "no limit" (one call returned 2.5 MB of text), and it compares
  text bytewise, so an NFD-composed label matches nothing while looking identical to the row.

## HTTP

```bash
.venv/bin/python mcp_server/server.py --transport http --host 127.0.0.1 --port 8931
curl -s http://127.0.0.1:8931/healthz
```

The MCP endpoint is `/mcp` — **without** a trailing slash; the SDK registers it as an exact
route, so `/mcp/` returns a 307 that a client may not follow on POST. On a non-localhost host
the SDK does not enable DNS-rebinding protection, so put it behind a proxy and pass explicit
`TransportSecuritySettings` before exposing it beyond localhost. There is no authentication:
this serves public disclosure data, but it will also happily run queries for anyone who can
reach the port.

`--json-response --stateless` makes it answer a single unauthenticated `curl` POST with plain
JSON and no session handshake, which is useful from a notebook.

## What two review rounds changed

The server worked before any of this; none of it was found by tests passing. Four adversarial
reviewers (correctness, security, protocol, agent-UX) found 10 defects, and a second pass told
to refute the first pass's findings confirmed 16 more and rejected 13. Full account in
`docs/2026-09-13-mcp-server.md`.

The one worth repeating here: **the fix for round one's `I`-levelling bug caused round two's
worst defect.** A block-wide decision cannot express a form that uses `I` both ways. If you
touch `block_levels`, run the tests — three of them exist only to hold that line.

## Scope limit

This serves the Ministry layer only — provincial aggregate forms from the CKNS portal. It does
not cover agency-level disclosures published on province portals, so **no province is
"complete" on this data alone**, and 527 reports produced no rows at all
(`nsnn_list_data_quality`). Every CKNS query also stops about 50 records short of the portal's
own declared total. That gap is open and is not closed here.
