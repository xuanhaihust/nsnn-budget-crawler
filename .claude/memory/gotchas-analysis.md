<!-- Imported by CLAUDE.md. Each entry cost a real bug; the cost is named so a
     future reader can judge whether it still applies. -->

# Reading the data without double counting

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
  block contain an `H`?) shipped once and made `break_down` assert "is a leaf" for **6,377
  section rows in 1,137 blocks across 19 provinces** that have children (counted by running
  the old code from `da54f54` against the new one; an earlier figure of 9,919 came from a
  review agent and was repeated without checking). `block_levels`
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
