"""The read-only SQL escape hatch: one entry point, `run`.

An agent hands this arbitrary SQL, so the guard is five independent layers. Each was measured
against the real 352 MB warehouse and each catches something the others miss:

1. `mode=ro` + `PRAGMA query_only`   - no writes to the warehouse file.
2. a deny-by-default authorizer      - mode=ro is NOT a sandbox. On a read-only connection
   ATTACH still created a new writable database, and `VACUUM INTO` still wrote a full 352 MB
   copy. The authorizer is what stops ATTACH, PRAGMA writable_schema, load_extension, and
   reads of dbstat / sqlite_stmt (compiled into this build and absent from sqlite_master).
3. `execute`, never `executescript`  - executescript('SELECT 1; DROP TABLE fact_row') drops
   the table. execute refuses a second statement, and that refusal is the injection defence.
4. a progress-handler deadline       - stops multi-step bombs (recursive CTEs, huge joins).
5. a streamed row + byte cap and SQLITE_LIMIT_LENGTH - the deadline provably does NOT bound a
   single VDBE step: randomblob(1e9) ran 7.7s past a 3s deadline at 978 MB RSS. Only the
   function allowlist and the cell limit stop that. And `SELECT * FROM v_fact` materialised
   3,814,427 rows in 71s at 6.5 GB RSS under fetchall; streamed, its first 200 rows cost 5ms.

The cap is applied by streaming the cursor, never by appending LIMIT to the agent's SQL:
textual injection is a syntax error on already-limited, LIMIT..OFFSET, VALUES and CTE queries,
and - the dangerous case - is silently swallowed when the query ends in a `--` comment, so the
cap you believe you applied is simply gone.
"""
import sqlite3, time

from warehouse import READABLE, connect

# Eponymous virtual tables compiled into this build that are NOT schema objects, so the
# "unknown name is a CTE" rule below must never let them through.
# Eponymous virtual tables are authorized as SQLITE_READ on their NAME, not as
# SQLITE_FUNCTION, so the SAFE_FUNCS allowlist never sees them - json_each and json_tree ran
# despite being off the list. Anything compiled into this build that is not a schema object
# belongs here.
BLOCKED = {'dbstat', 'sqlite_stmt', 'sqlite_dbpage', 'fts3tokenize', 'fts4aux', 'fts5vocab',
           'rtree', 'rtree_i32', 'generate_series', 'carray', 'zipfile', 'fsdir',
           'json_each', 'json_tree', 'sqlite_dbdata', 'pragma_database_list'}
BLOCKED_PREFIX = ('sqlite_', 'pragma_', 'fts', 'rtree')

# Deliberately absent: randomblob, zeroblob, random, load_extension, readfile, writefile.
SAFE_FUNCS = {
    'abs', 'coalesce', 'ifnull', 'iif', 'instr', 'length', 'lower', 'upper', 'ltrim', 'rtrim',
    'trim', 'nullif', 'printf', 'format', 'replace', 'round', 'substr', 'substring', 'typeof',
    'char', 'unicode', 'concat', 'concat_ws', 'like', 'glob', 'likelihood', 'likely',
    'unlikely', 'sign', 'ceil', 'ceiling', 'floor', 'exp', 'ln', 'log', 'log2', 'log10', 'pow',
    'power', 'sqrt', 'mod', 'trunc', 'min', 'max', 'cast', 'hex', 'quote', 'sqlite_version',
    'avg', 'count', 'group_concat', 'string_agg', 'sum', 'total',
    'row_number', 'rank', 'dense_rank', 'ntile', 'lag', 'lead', 'first_value', 'last_value',
    'nth_value', 'cume_dist', 'percent_rank',
    'date', 'time', 'datetime', 'strftime', 'julianday', 'unixepoch',
    'json', 'json_array', 'json_array_length', 'json_extract', 'json_object', 'json_type',
    'json_valid', 'json_quote', 'json_group_array', 'json_group_object',
    # json_each / json_tree are deliberately absent: they are table-valued and are blocked
    # by name above, because the FUNCTION action never fires for them.
}

MAX_ROWS = 500                  # rows handed back
MAX_CELL = 1 << 20              # 1 MB per cell: kills blob and group_concat bombs
MAX_BYTES = 1 << 20             # 1 MB total payload
TIMEOUT_S = 10.0
TICK = 50_000                   # VM steps between deadline checks; +0.3%, vs +2.4% at 1_000

_SCHEMA = set()


class Denied(Exception):
    """The query asked for something the guard refuses. The message says what to change."""


def _schema():
    global _SCHEMA
    if not _SCHEMA:
        con = connect(authorizer=None)      # not a bare sqlite3.connect: this one explains
        _SCHEMA = {r[0].lower() for r in con.execute("select name from sqlite_master")}
        con.close()
    return _SCHEMA


def _authorizer(reason):
    def check(action, arg1, arg2, dbname, trigger):
        try:
            if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ:
                if dbname not in (None, 'main'):
                    reason.append(f"reads from database {dbname!r} are not allowed")
                    return sqlite3.SQLITE_DENY
                name = (arg1 or '').lower()
                if name in READABLE:
                    return sqlite3.SQLITE_OK
                # An unknown name is a CTE or a subquery alias, and must be allowed - SQLite
                # raises SQLITE_READ for those too, and agents write CTEs constantly. A CTE
                # that shadows a real table still reads nothing, so this is safe; but the
                # eponymous vtables above are not schema objects, so they need naming.
                if name in BLOCKED or name.startswith(BLOCKED_PREFIX) or name in _schema():
                    reason.append(f"{arg1!r} is not a readable table")
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION:
                if (arg2 or '').lower() in SAFE_FUNCS:
                    return sqlite3.SQLITE_OK
                reason.append(f"function {arg2!r} is not allowed")
                return sqlite3.SQLITE_DENY
            reason.append('only a single read-only SELECT is allowed')
            return sqlite3.SQLITE_DENY
        except Exception as exc:                    # a raising callback must not open a hole
            reason.append(f"authorizer error: {exc}")
            return sqlite3.SQLITE_DENY
    return check


def run(sql, max_rows=200, timeout_s=TIMEOUT_S):
    """Run one read-only SELECT. Returns (columns, rows, truncated, elapsed_ms)."""
    if not isinstance(sql, str) or not sql.strip():
        raise Denied('empty query')
    if len(sql) > 20_000:
        raise Denied('query text too long')
    max_rows = max(1, min(int(max_rows), MAX_ROWS))
    reason = []
    _schema()                                       # warm it before the authorizer is armed
    con = connect(authorizer=None)
    try:
        con.row_factory = None                      # plain tuples: the renderer names columns
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_CELL)
        con.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        con.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 50)
        con.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 200)
        con.setlimit(sqlite3.SQLITE_LIMIT_LIKE_PATTERN_LENGTH, 500)
        con.set_authorizer(_authorizer(reason))
        started = time.perf_counter()
        deadline = started + timeout_s
        con.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, TICK)
        try:
            cur = con.execute(sql)                  # never executescript: one statement only
            if cur.description is None:
                raise Denied('that statement returns no rows; only SELECT is supported')
            cols = [d[0] for d in cur.description]
            rows, budget, truncated = [], MAX_BYTES, False
            for row in cur:                         # streamed; never fetchall()
                if len(rows) >= max_rows:
                    truncated = True
                    break
                budget -= sum(len(v) if isinstance(v, (str, bytes)) else 8 for v in row) + 16
                if budget < 0:
                    truncated = True
                    break
                rows.append(row)
            cur.close()
        except sqlite3.DatabaseError as exc:        # the parent of OperationalError AND
            msg = str(exc)                          # DataError, which the cell limit raises
            if reason:
                raise Denied(f"refused: {reason[0]}") from None
            if 'interrupted' in msg:
                raise Denied(f"query exceeded the {timeout_s:g}s limit - add a WHERE filter, "
                             'or aggregate instead of scanning') from None
            if 'one statement' in msg:
                raise Denied("send exactly one statement (no ';'-separated batches)") from None
            raise Denied(f"sql error: {msg}") from None
        except sqlite3.Warning:
            raise Denied("send exactly one statement (no ';'-separated batches)") from None
        return cols, rows, truncated, round((time.perf_counter() - started) * 1000, 1)
    finally:
        con.set_progress_handler(None, 0)
        con.close()
