"""Read-only access to data/nsnn.db, plus the conversions every tool shares.

The warehouse is built by dashboard/etl.py and unpacked by dashboard/db.py restore. This
module never writes to it: every connection is opened `mode=ro` with an authorizer, because
`mode=ro` alone still permits ATTACH and `PRAGMA writable_schema` (measured, not assumed).

Four things here are load-bearing and were each measured against the real corpus:

* `fold` - diacritic folding for search and for province names. The usual NFD recipe is
  WRONG for Vietnamese: đ is U+0111, a distinct letter with no canonical decomposition, so
  NFD leaves it alone and `unicodedata.combining` returns 0. đ appears in 43.6% of indicator
  labels, so without the explicit translation "dau tu" matches crude oil (dầu thô) and a
  pagoda (chùa Dâu) and nothing about investment.

* `outline` - the outline level of an indicator. `dim_indicator.depth` CANNOT be used for
  this: it counts how many markers the ETL stripped, and every label carries exactly one, so
  3.32M of 3.8M rows are depth=1. In B63 the section head `A TỔNG THU CÂN ĐỐI NSNN`, the
  roman `I Thu nội địa`, the arabic `6 Thuế bảo vệ môi trường` and the leaf `- Thuế BVMT thu
  từ hàng hóa nhập khẩu` are all depth=1. Filtering on depth to get "top-level items" returns
  the whole form and double counts it. The marker has to be parsed back out of the raw label.

* `money` - tỷ đồng, fixed point. Never %g: it renders 352919320000000.0 as 3.52919e+14 and
  the tỷ value as 352919, silently dropping .32.

* `render` - pipe-delimited text with constant columns hoisted into the header. Measured
  2,409 characters against 8,618 for the JSON-per-row shape the SDK produces by default, on
  the same 40 rows. `|` is safe (0 of 153,272 indicator and 0 of 22,811 series labels contain
  one); newlines are not (5.6% of indicator labels embed one), so labels are collapsed.
"""
import pathlib, re, sqlite3, unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / 'data' / 'nsnn.db'

# Everything an agent may read. Checked by name, so a table added later is denied by default.
READABLE = {'fact_row', 'dim_province', 'dim_period', 'dim_scope', 'dim_table', 'dim_indicator',
            'dim_series', 'dim_unit', 'dim_raw', 'dim_report', 'v_fact', 'sqlite_master'}


class WarehouseMissing(RuntimeError):
    """data/nsnn.db is not unpacked. Recoverable, and the message says how."""


def _readonly_authorizer(action, arg1, arg2, dbname, trigger):
    """Deny by default. SQLITE_DENY, never SQLITE_IGNORE.

    IGNORE would substitute NULL for a denied column and let the query succeed, manufacturing
    a blank where a real value existed - the exact failure CLAUDE.md's "never turn a blank
    into 0" and "never infer a value" rules exist to prevent. A denial has to be loud.
    """
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE, sqlite3.SQLITE_FUNCTION):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        return sqlite3.SQLITE_OK if (arg1 or '').lower() in READABLE else sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY


def connect(path=None, authorizer=_readonly_authorizer):
    """A read-only connection. `path=None` means the warehouse, and it must already exist."""
    path = pathlib.Path(path) if path else DB
    if not path.exists():
        raise WarehouseMissing(
            f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path} is not there. "
            "It is derived data and is not committed uncompressed; unpack it with\n"
            "    .venv/bin/python dashboard/db.py restore\n"
            "which takes about 18s. If data/nsnn.db.xz is missing too, rebuild from work/ "
            "with dashboard/etl.py (~6 min, and it needs the crawl output).")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None,
                          check_same_thread=False, timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("pragma query_only = ON")           # before the authorizer, or it denies this
    if authorizer:
        con.set_authorizer(authorizer)
    return con


# --- Vietnamese text -----------------------------------------------------------------------

_DJ = str.maketrans({'đ': 'd', 'Đ': 'd', 'Ð': 'd'})   # U+0111, U+0110, and the U+00D0 lookalike


def fold(s):
    """Lowercase, strip tone marks, and turn đ into d. NFC first: this corpus contains NFD."""
    s = unicodedata.normalize('NFC', str(s or '')).translate(_DJ)
    s = unicodedata.normalize('NFD', s)
    return ''.join(c for c in s if not unicodedata.combining(c)).lower()


_WS = re.compile(r'\s+')


def flat(s, limit=None):
    """Collapse whitespace so a label cannot break a line-oriented row. 5.6% embed a newline."""
    s = _WS.sub(' ', str(s or '')).strip()
    return s[:limit - 1] + '…' if limit and len(s) > limit else s


# --- outline markers -----------------------------------------------------------------------

# Vietnamese budget forms nest section letter > roman > arabic > dotted > lowercase > dash.
# I/V/X standing alone are roman, not section letters; Đ is a section letter (B46 uses it).
_ROMAN = re.compile(r'^[IVXLC]+$')
_MARK = re.compile(r'^\s*([-*+•]+|\(?[0-9]+(?:\.[0-9]+)*[.)]?|[IVXLC]{2,}[.)]?|[IVXLCĐA-Za-z][.)]?)(?=\s)\s+')
SECTION, ROMAN, ARABIC, LOWER, DASH = 0, 1, 2, 5, 6


def outline(label):
    """Return (marker, level) for an indicator label, or ('', None) when it carries no marker.

    Level orders the hierarchy for positional scoping; it is not a distance from the root.
    Deeper number means deeper in the outline, which is all `children` needs.
    """
    m = _MARK.match(unicodedata.normalize('NFC', str(label or '')))
    if not m:
        return '', None
    mark = m.group(1)
    bare = mark.rstrip('.)').lstrip('(')
    if mark[0] in '-*+•':
        return mark, DASH
    if bare.isdigit():
        return mark, ARABIC
    if bare.replace('.', '').isdigit():                      # 1.1, 2.3.4 - one level per dot
        return mark, ARABIC + bare.count('.')
    if len(bare) > 1 and _ROMAN.match(bare):
        return mark, ROMAN
    if bare in ('I', 'V', 'X'):                              # single roman digit, not a letter
        return mark, ROMAN
    if bare.isupper() or bare == 'Đ':
        return mark, SECTION
    return mark, LOWER


# --- numbers -------------------------------------------------------------------------------

TY = 1_000_000_000                      # tỷ đồng, the unit a Vietnamese reader recognises
IMPLAUSIBLE = 1e15                      # above this the source declared the wrong unit
MAX_EXACT = 2 ** 53


def money(vnd):
    """VND to tỷ đồng as fixed-point text. Never scientific, never thousands separators.

    No separators on purpose: Vietnamese sources write 146.068 for 146068 and use ',' as the
    decimal mark, so a formatted "146,068" invites a 1000x misreading against the source.
    """
    if vnd is None:
        return ''
    v = vnd / TY
    s = f"{v:.3f}".rstrip('0').rstrip('.')
    return ('0' if s in ('', '-0') else s) + ('!scale?' if abs(vnd) > IMPLAUSIBLE else '')


def confidence(vnd, series, unit):
    """Why a VND figure should not be trusted, or '' when nothing is wrong with it."""
    if vnd is None:
        return ''
    if '%' in (series or '') or '%' in (unit or ''):
        return 'not_currency'           # a ratio column that inherited the table's đồng unit
    if abs(vnd) > IMPLAUSIBLE:
        return 'implausible'            # 7,816 rows: the source states the wrong unit
    if abs(vnd) > MAX_EXACT:
        return 'imprecise'
    return ''


# --- rendering -----------------------------------------------------------------------------

def render(rows, columns=None, note='', hoist=True):
    """Pipe-delimited rows, with every column that is constant across them lifted into a header.

    Hoisting is the larger half of the token win: in a typical 40-row result the series and
    unit are identical on every row, so 80 repetitions collapse to one line.
    """
    rows = [dict(r) for r in rows]
    if not rows:
        return note or '(no rows)'
    columns = columns or list(rows[0].keys())
    const = {c: rows[0].get(c) for c in columns
             if hoist and len(rows) > 1 and all(r.get(c) == rows[0].get(c) for r in rows)}
    const = {c: v for c, v in const.items() if v not in (None, '')}
    varying = [c for c in columns if c not in const]
    out = []
    if const:
        out.append(' · '.join(f"{c}={flat(v, 60)}" for c, v in const.items()))
    out.append('|'.join(varying))
    for r in rows:
        out.append('|'.join('' if r.get(c) is None else flat(r.get(c), 90) for c in varying))
    if note:
        out.append(note)
    return '\n'.join(out)
