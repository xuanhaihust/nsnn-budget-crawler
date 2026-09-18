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

# Everything an agent may read. Checked by name, so a table added later is denied by default -
# which is why `fact_national_monthly` had to be named here explicitly when it was added.
# It is a SEPARATE corpus (whole country, monthly) with no join path to fact_row (34 provinces,
# quarterly at finest); being readable does not make the two addable.
READABLE = {'fact_row', 'dim_province', 'dim_period', 'dim_scope', 'dim_table', 'dim_indicator',
            'dim_series', 'dim_unit', 'dim_raw', 'dim_report', 'v_fact', 'sqlite_master',
            'fact_national_monthly'}


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

# Vietnamese budget forms nest: section letter > roman > parenthesised > arabic > lowercase
# > dash. Levels are spaced so compound markers ("I.1", "A.2") slot between their parents and
# the next rank. The number is an ordering, not a distance from the root - `children` only ever
# compares it against the parent's.
SECTION, ROMAN, PAREN, ARABIC, LOWER, DASH = 0, 2, 4, 6, 10, 12

_ROMAN = re.compile(r'^[IVXLC]+$')
# Single-letter markers that are section letters rather than roman digits. C/D/L/M are roman
# digits in principle (100/500/50/1000) but no form here numbers a section 100, so as markers
# they are always letters. V and X are the opposite: a roman sequence reaching V or X is
# ordinary, and the Vietnamese section alphabet (A B C D Đ E G H I K L M N) effectively never
# gets that far, so they stay roman.
_LETTER_ALWAYS = set('ABĐEGHKNOPQRSTUYCDLM')
_MARK = re.compile(r'^\s*(\(\s*[0-9]+\s*\)[.)]?'
                   r'|[-*+•]+'
                   r'|[0-9]+(?:\.[0-9]+)*[.)]?'
                   r'|[IVXLC]+\.[0-9]+(?:\.[0-9]+)*[.)]?'
                   r'|[A-ZĐ]\.[0-9]+(?:\.[0-9]+)*[.)]?'
                   r'|[IVXLC]{2,}[.)]?'
                   r'|[IVXLCĐA-Za-z][.)]?)(?=\s)\s+')


def marker(label):
    """The raw outline marker of a label, or '' when it carries none."""
    m = _MARK.match(unicodedata.normalize('NFC', str(label or '')))
    return m.group(1) if m else ''


def block_levels(labels):
    """Outline level for every row of one block, resolving the bare-`I` ambiguity by lookahead.

    A block-wide flag cannot do this. Forms 45/CK-NSNN and 58/CK-NSNN letter their sections
    A, B, C ... H, I, K AND use romans I, II, III as agency headings under every one of them,
    so the same block needs `I` read both ways. Deciding it once per block made every section
    letter in 1,259 blocks across 20 provinces report as a childless leaf.

    The lookahead: from a bare `I`, scan forward. Hitting `II` first means this `I` opened a
    roman run. Hitting another bare `I`, or a different single section letter, first means the
    roman run belongs to somebody else and this `I` continues the letter run.
    """
    labels = list(labels)
    marks = [marker(l) for l in labels]
    bare = [m.rstrip('.)').lstrip('(').strip() for m in marks]
    out = []
    for i, b in enumerate(bare):
        if b != 'I':
            out.append(outline(labels[i])[1])
            continue
        roman = False
        for nxt in bare[i + 1:]:
            if nxt == 'II':
                roman = True
                break
            if nxt == 'I' or (len(nxt) == 1 and nxt in _LETTER_ALWAYS):
                break
        out.append(ROMAN if roman else SECTION)
    return out


def outline(label, lettered=False):
    """Return (marker, level), or ('', None) when the label carries no parseable marker.

    A None level means "unknown", and callers must treat it as a hard stop rather than as a
    wildcard: 19,433 distinct labels carry no marker at all, including every headline total
    (TỔNG CHI NSĐP, TỔNG THU NGÂN SÁCH NHÀ NƯỚC), and treating those as "shallower than
    everything" makes a breakdown swallow the whole form.

    `lettered` is a fallback for a lone label; within a block use block_levels(), which
    resolves `I` by lookahead instead of by a single flag for the whole block.
    """
    mk = marker(label)
    if not mk:
        return '', None
    bare = mk.rstrip('.)').strip()
    if mk[0] in '-*+•':
        return mk, DASH
    if bare.startswith('('):                                  # (1), (2) - a group heading
        return mk, PAREN
    head = bare.split('.')[0]
    dots = bare.count('.')
    if bare.replace('.', '').isdigit():
        return mk, ARABIC + dots
    if _ROMAN.match(head) and dots:                           # I.1, II.3
        return mk, ROMAN + dots
    if len(head) == 1 and (head.isupper() or head == 'Đ') and dots:   # A.2
        return mk, SECTION + dots
    if len(bare) > 1 and _ROMAN.match(bare):                  # II, IV, VIII
        return mk, ROMAN
    if len(bare) == 1 and bare in _LETTER_ALWAYS:
        return mk, SECTION
    if bare == 'I':
        return mk, SECTION if lettered else ROMAN
    if bare in ('V', 'X'):
        return mk, ROMAN
    if bare.isupper() or bare == 'Đ':
        return mk, SECTION
    return mk, LOWER


# --- numbers -------------------------------------------------------------------------------

TY = 1_000_000_000                      # tỷ đồng, the unit a Vietnamese reader recognises
#: Above this a PROVINCIAL figure means the source declared the wrong unit - the largest real
#: provincial budget in the corpus is Hà Nội 2021 at 1.96e14. It is NOT a universal ceiling:
#: the national budget really is ~2.6e15, so money() takes `cap=None` for that corpus rather
#: than stamping "!scale?" on correct figures and teaching the reader to ignore the flag.
IMPLAUSIBLE = 1e15
MAX_EXACT = 2 ** 53


def money(vnd, cap=IMPLAUSIBLE):
    """VND to tỷ đồng as fixed-point text. Never scientific, never thousands separators.

    No separators on purpose: Vietnamese sources write 146.068 for 146068 and use ',' as the
    decimal mark, so a formatted "146,068" invites a 1000x misreading against the source.

    A non-zero figure must never render as "0". 34,306 rows carry a real VND value below
    500,000, which three decimals of tỷ đồng rounds away - printing those as "0" would make a
    published value indistinguishable from a published zero, in a corpus whose first rule is
    that a blank is not a zero.

    `cap` is the implausibility ceiling, provincial by default. Pass None for a corpus whose
    real figures exceed it - the national series does - so a correct number is not stamped
    "!scale?". A flag that fires on good data is worse than no flag: it trains the reader to
    ignore it on the bad data it was built for.
    """
    if vnd is None:
        return ''
    v = vnd / TY
    s = f"{v:.3f}".rstrip('0').rstrip('.')
    if s in ('', '-0', '0') and vnd != 0:
        s = f"{v:.9f}".rstrip('0').rstrip('.') or f"{v:.2e}"
    elif s in ('', '-0'):
        s = '0'
    return s + ('!scale?' if cap is not None and abs(vnd) > cap else '')


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
