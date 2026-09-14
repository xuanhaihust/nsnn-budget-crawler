<!-- Imported by CLAUDE.md. Each entry cost a real bug; the cost is named so a
     future reader can judge whether it still applies. -->

# Working agreement

Every rule here was paid for by something going wrong in this project. None of it is
generic advice; the cost is named so you can judge whether it still applies.

**Measure it; do not repeat a claim.** The README said a province took ~5 minutes. Measured,
it was 524s. `--status`, a stopwatch and a row count are cheap. A number you did not measure
does not go in a commit message, a doc, or an answer to the owner.

**Check a premise against the corpus before building on it.** Three separate premises that
everyone believed turned out false: `dim_indicator.depth` is not the outline level, keying a
cell on the cleaned label merges different lines, and `mode=ro` is not a sandbox. Each was one
query away from being disproved, and each would have been load-bearing.

**Reproduce a bug report before fixing it.** A review finding once had its diagnosis exactly
inverted and one of its quoted outputs fabricated; fixing from the report as written would
have made the bug worse. Run the repro, read the real output, then fix what you actually saw.

**A fix is not finished until you re-run the case it was for AND its neighbours.** The fix for
the bare-`I` levelling bug caused the worst defect of the next review round — 6,377 rows
wrongly reported as childless. One green test is not evidence that a change was safe.

**Every fix gets a regression test.** `mcp_server/test_server.py` has 97 checks and several
exist only because that line broke once. Run it after touching anything under `mcp_server/`.

**Never correct the source data.** When a published figure is clearly wrong — 757 of them sit
100x from their own history — flag it, show the whole series, and leave the number exactly as
published. Deciding what the source meant is the owner's call, not the code's. This is the
same rule as "never infer a value", applied one layer out.

**A script's success message is not evidence.** A commit once described a `CLAUDE.md` change
that never landed: the patch script printed `docs updated`, then hit an AssertionError on the
next block, and the commit ran anyway because it was a separate command. `print("done")` proves
only that the print ran. Re-read the file, or grep for the string you claim you added, before
saying it is there - and check the exit status before committing.

**Test a constraint before declaring it.** "The workbooks can only be fixed by re-running
`./nsnn`, which re-downloads several GB" was stated as fact and was simply false - all three
affected columns are pure functions of the workbook's own contents, and `pipeline/fix_units.py`
does it offline in minutes. It was never tested, only inferred from "`work/` is absent". A
claim that something is impossible or expensive is exactly the kind that stops the owner asking
for it, so it needs more evidence than a claim that something works, not less.

**Say which claims are verified and which are inferred.** The corrosive thing is not being
wrong, it is being wrong in the same confident register as being right, so the owner cannot
tell which sentences to trust. Mark the difference in the sentence itself: measured, or
assumed-and-not-checked.

**Lead with the answer.** "Is it done?" got buried under caveats twice. State done or not done
in the first line, then the detail. A list of open items that mixes real unfinished work with
documented data limitations reads as evasion even when every line is true.

**Report your own errors plainly, including shipped ones.** If something you already told the
owner turns out wrong, say so first and unprompted, before the rest of the update. Two rounds
of review is not a proof of correctness; say that too rather than implying the work is
finished.

**Documentation is part of the change, not a follow-up.** Three commits shipped here before
anyone noticed `README.md` never mentioned `mcp_server/` and a `CLAUDE.md` pointer had gone
stale. A commit that changes behaviour updates: this file (a new gotcha), the relevant
`README.md`, and a dated note in `docs/` if the work was substantial.

**Language split.** `README.md` is for a non-programmer owner and is written in Vietnamese.
`CLAUDE.md`, `docs/`, code comments and commit messages are English. The owner writes in
Vietnamese — reply in Vietnamese unless asked otherwise, and follow the most recent
instruction about a specific deliverable.

**Report numbers honestly**, including failed reports and the open 50-record gap. If a rule in
this file would have to be broken to make output look better, stop and say so instead.
