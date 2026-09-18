"""The MCP surface over the NSNN budget warehouse.

    .venv/bin/python mcp_server/server.py                      # stdio (Claude Code, Desktop)
    .venv/bin/python mcp_server/server.py --transport http     # streamable HTTP on :8931

One MCPServer object serves both; the transport is only how it is called. stdio and HTTP are
deliberately not served together: the stdio half's lifetime belongs to the client that spawned
it, so binding a network port to that lifetime would be wrong.

Every tool returns plain text, with structured_output=False. That is measured, not stylistic:
a dict return is serialised by the SDK through pydantic_core.to_json(indent=2) with the indent
hardcoded, which cost 8,618 characters for the same 40 rows that the pipe-delimited form
renders in 2,409; and a str return WITHOUT structured_output=False is emitted twice, once in
content and once in structuredContent.

Nothing here writes to stdout. On the stdio transport stdout is the JSON-RPC frame stream, and
a stray print corrupts it.
"""
import argparse, inspect, logging, sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

import sqlguard, tools, warehouse

log = logging.getLogger('nsnn')

INSTRUCTIONS = inspect.cleandoc("""
    Read-only access to published budget data for Vietnam's 34 current provinces, crawled from
    the Ministry of Finance CKNS portal. Call nsnn_describe_corpus first.

    Four rules this data enforces, and that any answer drawn from it must respect:
    1. Never sum rows. Parents and children are both rows, so SUM(vnd) over one province-year
       of form B46 comes to 3.95x-6.98x that province's own published total. Use
       nsnn_break_down, which reconciles against the published parent.
    2. A blank is not a zero. 1,036,789 rows have no value because the source cell was empty
       or held '-'. They come back empty. Never read one as 0.
    3. Scope (dự toán / quyết toán) is a stage, not a category. A plan and a settled account
       are different numbers; comparing one province's plan with another's outturn is the
       commonest wrong answer this corpus produces.
    4. Coverage is uneven. No indicator reaches all 34 provinces in any year. Every comparison
       prints its coverage; report it rather than presenting a partial ranking as national.

    This covers the Ministry layer only - provincial aggregate forms. Agency-level disclosures
    on province portals are not here, so no province is 'complete' on this data alone.
""")

mcp = MCPServer(name='nsnn', title='NSNN provincial budget warehouse',
                version='0.1.0', instructions=INSTRUCTIONS)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def expose(fn, name, description):
    """Register one tool: text out, refusals as ToolError so the model reads the reason.

    An uncaught exception reaches the client as the bare string "Error executing tool <name>"
    with everything else redacted, which teaches the model nothing and invites a retry loop.
    """
    def call(**kw):
        try:
            return fn(**kw)
        except (ValueError, LookupError, sqlguard.Denied) as exc:
            raise ToolError(str(exc)) from None
        except warehouse.WarehouseMissing as exc:
            raise ToolError(str(exc)) from None
        except Exception as exc:
            log.exception('%s failed', name)
            raise ToolError(f"{type(exc).__name__}: {exc}") from None
    call.__signature__ = inspect.signature(fn)
    call.__name__ = name
    call.__doc__ = description
    mcp.tool(name=name, description=inspect.cleandoc(description), annotations=READ_ONLY,
             structured_output=False)(call)


expose(tools.describe_corpus, 'nsnn_describe_corpus', """
    START HERE. What the warehouse contains and the four traps in it. No arguments.
""")

expose(tools.list_provinces, 'nsnn_list_provinces', """
    The 34 provinces with what each actually published: fact rows, reports, reports that
    produced nothing, and the year span. Read this before assuming a province is missing data
    rather than simply thin.
""")

expose(tools.list_forms, 'nsnn_list_forms', """
    Budget forms published widely enough that comparing provinces on them means something.
    Forms with huge row counts but few shared indicators (B51, B53, B67) list agencies and
    projects whose names differ per province and do not compare.
""")

expose(tools.find_indicators, 'nsnn_find_indicators', """
    Find exact indicator labels. Call this before any read tool - you will not guess one.

    There are 153,272 verbatim Vietnamese labels. Search is diacritic-insensitive, so 'giao
    duc' and 'giáo dục' are the same query; it does NOT translate English, so use Vietnamese
    terms (chi = spending, thu = revenue, dự toán = plan, quyết toán = settled accounts).

    Results are ranked by how many provinces publish the label, not by lexical closeness:
    93% of labels are one-province project lines, so the tightest text match is usually the
    least useful row. Prefer high nprov for anything cross-province.

    Copy the label EXACTLY, including its leading outline marker - matching downstream is
    literal, and the marker is part of the identity. '1.1 Chi giáo dục' is the investment line
    and '1 Chi giáo dục' the recurrent one; they differ by up to 6x.
""")

expose(tools.find_forms, 'nsnn_find_forms', """
    Find a form by its Vietnamese title rather than its code ('cân đối ngân sách' -> B46, B62).
    Codes are not unique across families: B62 and 62/CK-NSNN are different forms.
""")

expose(tools.read_timeseries, 'nsnn_read_timeseries', """
    One named cell of one named form, for one province, across every year it was published.

    Nothing is summed, so the double-count trap cannot fire here. If you do not name a series
    and the cell appears in more than one, the error lists the actual choices rather than
    picking one - a dự toán column and a quyết toán column are different numbers.
""")

expose(tools.compare_provinces, 'nsnn_compare_provinces', """
    The same named cell across provinces for one year, largest first.

    ALWAYS read the coverage line. No indicator reaches all 34 provinces in any year - form
    B46's total-spending cell covers 30 provinces in 2021 but only 6 in 2023 - so a ranking
    without coverage is a ranking of whoever happened to publish. Omit `year` and the tool
    picks the best-covered year and says which.

    A province absent from the result did not publish that cell; it is not a zero. Values more
    than 100x below the median province are marked MAGNITUDE? and are source defects, shown as
    published rather than corrected.
""")

expose(tools.read_balance_sheet, 'nsnn_read_balance_sheet', """
    Form B46, the provincial balance sheet: revenue, spending, own revenue, central transfer,
    investment, recurrent, borrowing, plus self-sufficiency and investment share.

    This is the one form where ranking provinces is defensible - every figure is a single
    published cell and the two ratios divide two cells, so nothing is summed. These are PLANS
    (dự toán); the outturn differs. With no arguments it returns each province's most recent
    year that carries both cells.
""")

expose(tools.break_down, 'nsnn_break_down', """
    Split a published parent row into its DIRECT children and reconcile against it. This is
    the tool to reach for instead of summing anything.

    Children are scoped by position in the source form, not by marker pattern: arabic
    numbering restarts under every roman section, so a flat filter mixes the children of
    section I with those of section II. Only rows exactly one level deeper are summed - dash
    rows beneath a numbered line are its parts, and adding them turned a 2-million residual
    into a 2.3-trillion overstatement in testing.

    Always returns the published parent, the children's sum, the residual, and whether it
    reconciles. If it says NO, report the published parent and call the split partial; do not
    present the sum as the total.
""")

expose(tools.read_revenue_mix, 'nsnn_read_revenue_mix', """
    Where a province's domestic revenue comes from - form B63 'I Thu nội địa' broken into its
    direct children, with the reconciliation. basis: 'quyết toán' (settled, default) or
    'dự toán' (plan).
""")

expose(tools.read_spending_by_sector, 'nsnn_read_spending_by_sector', """
    Recurrent spending by sector (education, health, economic activity, ...).

    PROVINCIAL TIER ONLY. Forms B50 and B65 are 'chi ngân sách CẤP TỈNH theo lĩnh vực': their
    section A is a block transfer to districts, and only section B is broken down by sector.
    In Nghệ An 2022 that is 11,842 tỷ transferred against 15,512 tỷ at the provincial tier, of
    a 31,060 tỷ total - so a sector share here is a share of roughly half the province's
    spending. The tool prints the A/B split; carry that caveat into any answer. The district
    tier's own sector split is not published anywhere in this corpus.
""")

expose(tools.compare_plan_vs_outturn, 'nsnn_compare_plan_vs_outturn', """
    Plan against settled accounts, read from the two columns of the SAME form B63 report - so
    there is no cross-form join and no year-alignment risk. Returns the ratio per province-year.
""")

expose(tools.list_data_quality, 'nsnn_list_data_quality', """
    What this corpus does NOT reliably tell you. Use it before reporting a figure as fact.

    blocks: 'pending' (527 reports that produced no rows, with the reason for each) ·
    'magnitude' (headline cells whose own published history spans 100x or more - usually a
    thousands separator read as a decimal point) · 'units' (units left unconverted rather than
    guessed at) · 'coverage' (how many provinces reported per year).
""")

expose(tools.trace_source, 'nsnn_trace_source', """
    The evidence chain for one number: the untouched source cell, the parsed value, the unit,
    and the report it came from.

    Cite as province + CKNS report_id + title. The warehouse stores no document number and no
    source URL for any of its 8,660 reports, so do not construct one.
""")

expose(tools.read_national_monthly, 'nsnn_read_national_monthly', """
    Vietnam's state budget revenue and spending BY MONTH, for the country as a whole.

    A DIFFERENT CORPUS from every other tool here, and the difference is the point. Every
    other tool reads the 34 provinces' LOCAL budgets, quarterly at finest, from the Ministry
    of Finance disclosure portal. This reads the whole country, monthly, from the statistics
    office's monthly socio-economic report. It includes the central budget; those do not.
    Never add the two together, and never compare one against the other as like for like.

    basis picks one of three series for the same month, which must never be summed together:
      'tháng'                  as the source published it in that month
      'tháng (suy từ luỹ kế)'  successive difference of the cumulative series, computed here
      'luỹ kế N tháng'         year to date, as published

    The published monthly REVENUE does not add up to the published cumulative - it falls 8-10%
    short at every checkpoint, because a month figure is an early estimate that is never
    revised while the cumulative one is re-estimated upward. Spending is additive. Use 'tháng'
    for when money moved and 'tháng (suy từ luỹ kế)' for how much.

    Everything here is an estimate ("ước đạt"), not a settled account.
""")

expose(tools.run_sql, 'nsnn_run_sql', """
    Escape hatch: ONE read-only SELECT over the warehouse. Prefer the curated tools - they
    encode the correctness rules this raw access does not.

    Tables: fact_row + dim_province/period/scope/table/indicator/series/unit/raw/report, and
    the view v_fact which joins them all. Also fact_national_monthly, which is a SEPARATE
    corpus - the whole country by month, not the 34 provinces by quarter - and has no join
    path to the rest on purpose. Never UNION or add the two: one includes the central budget
    and the other does not. Query v_fact unless you need speed: its columns are
    province, period, year, kind, scope, table_label, form_code, indicator_raw, indicator,
    depth, series, unit, unit_source, raw, value, vnd, report_id, id.

    `unit` is the canonical spelling - group by it. `unit_source` is what that workbook
    published; seven spellings map onto 'triệu đồng' alone, so grouping by unit_source
    splits one unit into several.

    vnd is the money column (value x unit factor, NULL when the unit is unknown or is a
    percentage). `raw` is the untouched source text. `indicator_raw` keeps the outline marker
    and is the correct key; `indicator` has it stripped and MERGES DIFFERENT LINES. `depth` is
    NOT the outline level - it is 1 for 87% of rows - so never filter on it to get top-level
    items.

    DO NOT SUM ACROSS A HIERARCHY. Parents and children are both rows; SUM(vnd) over one
    province-year of B46 is 3.95x-6.98x the published total. Aggregate only with COUNT, MIN,
    MAX, or inside a single named indicator.

    Guard rails: one statement, no writes/ATTACH/PRAGMA, 10s limit, 200 rows by default and 500
    maximum, 1 MB payload. Truncation is always reported and there is no cursor - narrow the
    query instead.
""")


@mcp.custom_route('/healthz', methods=['GET'])
async def healthz(request):
    from starlette.responses import JSONResponse
    return JSONResponse({'ok': warehouse.DB.exists(), 'db': str(warehouse.DB)})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--transport', choices=('stdio', 'http'), default='stdio')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8931)
    ap.add_argument('--path', default='/mcp')
    ap.add_argument('--json-response', action='store_true',
                    help='plain JSON instead of SSE, for curl and scripted callers')
    ap.add_argument('--stateless', action='store_true',
                    help='no session handshake; pairs with --json-response')
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format='%(levelname)s %(name)s: %(message)s')
    if not warehouse.DB.exists():                 # fail loudly at startup, not per call
        log.warning('%s is missing - every tool will refuse until you run '
                    '`.venv/bin/python dashboard/db.py restore`', warehouse.DB)

    if args.transport == 'stdio':
        mcp.run('stdio')
    else:
        mcp.run('streamable-http', host=args.host, port=args.port,
                streamable_http_path=args.path, json_response=args.json_response,
                stateless_http=args.stateless)


if __name__ == '__main__':
    main()
