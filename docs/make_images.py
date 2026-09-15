"""Regenerate every screenshot under docs/images/ from live data.

    .venv/bin/python docs/make_images.py            # all of them
    .venv/bin/python docs/make_images.py mcp        # one group: dash | mcp | cli | workbook

Why this exists as a script rather than a folder of hand-taken screenshots: a README image is
a claim about what the software does, and a stale one is a false claim that nobody can check.
Everything here is captured by calling the real function and rendering its output unedited -
the tool text is never retyped, only coloured - so any figure in the images can be reproduced
by running the call printed on the image's first line.

Needs: the workbooks in output/, the warehouse (`dashboard/db.py restore`), and Chromium.
Takes about a minute. Rerun it after anything that changes tool output or the dashboard.

Two rendering traps, both of which cost an hour once:

* **DejaVu Sans Mono has no glyph for ế, ồ, ữ.** It is the default `monospace` here, and it
  renders those as a base letter plus a spacing accent - `Thuế` comes out as `Thuê´`, and
  `tỷ đồng` as `tỷ đôǹg`. It looks like a data corruption and is not one. Liberation Mono
  covers Vietnamese, and it is embedded as base64 because a file:// page will not fetch a
  file:// font.
* **`<pre>` does not inherit the body font.** The UA stylesheet sets `pre{font-family:monospace}`,
  which beats inheritance, so the face has to be named on the `pre` rule itself. Without that
  the font loads, reports `loaded`, and is never used.
"""
import base64, html, pathlib, re, subprocess, sys, textwrap, unicodedata

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / 'docs' / 'images'
CHROME = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome'
FONTS = pathlib.Path('/usr/share/fonts/truetype')
sys.path.insert(0, str(ROOT / 'mcp_server'))

TMP = pathlib.Path('/tmp/nsnn-shot.html')


def _b64(p):
    return base64.b64encode(pathlib.Path(p).read_bytes()).decode()


def _browser(pw):
    return pw.chromium.launch(executable_path=CHROME) if pathlib.Path(CHROME).exists() \
        else pw.chromium.launch()


def _shot(doc, selector, path, width, clip=None):
    TMP.write_text(doc, encoding='utf-8')
    with sync_playwright() as pw:
        b = _browser(pw)
        pg = b.new_page(viewport={'width': width, 'height': 700}, device_scale_factor=2)
        pg.goto(TMP.as_uri())
        pg.wait_for_function("document.fonts.status === 'loaded'")
        pg.wait_for_timeout(250)
        pg.locator(selector).screenshot(path=str(path), omit_background=True)
        b.close()
    print('wrote', path.relative_to(ROOT))


# --------------------------------------------------------------------------- terminal frames

TERM_CSS = """
@font-face{font-family:VMono;src:url("data:font/ttf;base64,__REG__")}
:root{--bg:#14161a;--bar:#1d2026;--ink:#d8dee9;--dim:#7c8797;
      --ok:#7fd88f;--warn:#f0b072;--bad:#f07c7c;--hdr:#c9a0ff;--str:#8fd6c4}
*{box-sizing:border-box}
body{margin:0;background:transparent;font:14px/1.55 VMono,monospace}
.win{width:__W__px;background:var(--bg);border-radius:10px;overflow:hidden;
     box-shadow:0 10px 34px rgba(0,0,0,.45)}
.bar{background:var(--bar);padding:9px 13px;display:flex;align-items:center;gap:7px}
.dot{width:11px;height:11px;border-radius:50%}
.t{color:var(--dim);font-size:12px;margin-left:9px}
pre{font:14px/1.55 VMono,monospace;margin:0;padding:15px 17px 17px;color:var(--ink);
    white-space:pre;overflow:hidden}
.p{color:var(--ok)} .c{color:var(--dim)} .h{color:var(--hdr)}
.b{color:var(--bad)} .w{color:var(--warn)} .s{color:var(--str)}
"""


def esc(s):
    # Some labels arrive in NFD; compose them so one glyph is asked for, not a base + mark.
    return html.escape(unicodedata.normalize('NFC', s))


def wrap(t, w=104):
    """Hard-wrap prose so the frame stays a sane width. Data lines (with `|`) are never cut."""
    out = []
    for ln in t.split('\n'):
        if len(ln) <= w or '|' in ln:
            out.append(ln)
        else:
            out += textwrap.wrap(ln, w, subsequent_indent='  ', break_long_words=False)
    return '\n'.join(out)


def term(name, blocks, width=1010, title='nsnn'):
    """blocks: [(call shown, output text)]. Colour is decided by line shape, never by editing."""
    parts = []
    for call, out in blocks:
        parts.append(f'<span class="p">&gt;&gt;&gt; {esc(call)}</span>')
        for ln in out.split('\n'):
            e = esc(ln)
            if re.match(r'^[a-z_]+\|[a-z_]', ln):
                e = f'<span class="h">{e}</span>'
            elif ln.startswith(('Denied:', 'ValueError:', 'refused')):
                e = f'<span class="b">{e}</span>'
            elif 'MAGNITUDE?' in ln or '!scale?' in ln:
                e = f'<span class="w">{e}</span>'
            elif re.match(r'^(published parent|children sum|residual|reconciles)', ln):
                e = f'<span class="s">{e}</span>'
            elif ln and not re.match(r'^[\dA-ZĐ]', ln) and '|' not in ln:
                e = f'<span class="c">{e}</span>'
            parts.append(e)
        parts.append('')
    css = TERM_CSS.replace('__W__', str(width)).replace(
        '__REG__', _b64(FONTS / 'liberation' / 'LiberationMono-Regular.ttf'))
    doc = (f'<meta charset="utf-8"><style>{css}</style>'
           '<div class="win"><div class="bar">'
           '<span class="dot" style="background:#ff5f57"></span>'
           '<span class="dot" style="background:#febc2e"></span>'
           '<span class="dot" style="background:#28c840"></span>'
           f'<span class="t">{esc(title)}</span></div>'
           f'<pre>{chr(10).join(parts).rstrip()}</pre></div>')
    _shot(doc, '.win', OUT / f'{name}.png', width + 60)


# ------------------------------------------------------------------------------------- groups

def group_mcp():
    """Real calls into mcp_server/tools.py. Failures are captured too - they are the point."""
    import tools

    bd = tools.break_down(province='Nghệ An', year=2020, form_code='B63',
                          indicator='I Thu nội địa', series='QUYẾT TOÁN/TỔNG THU NSNN')
    term('mcp-break-down', [(
        "nsnn_break_down(province='Nghệ An', year=2020, form_code='B63',\n"
        "                indicator='I Thu nội địa', series='QUYẾT TOÁN/TỔNG THU NSNN')",
        wrap(bd))],
        title='nsnn_break_down — a published parent, its direct children, the residual')

    term('mcp-find-indicators', [(
        "nsnn_find_indicators(query='chi giao duc', form_code='B50')",
        wrap(tools.find_indicators(query='chi giao duc', form_code='B50', limit=8)))],
        title='nsnn_find_indicators — diacritics optional, the label comes back exact')

    bs = tools.read_balance_sheet(year=2022).split('\n')
    term('mcp-balance-sheet', [("nsnn_read_balance_sheet(year=2022)",
                                wrap('\n'.join(bs[:16] + ['…'] + bs[-2:])))],
         title='nsnn_read_balance_sheet — form B46, with the magnitude flag left visible')

    refusals = []
    try:
        tools.trace_source(province='Nghệ An', year=2020, form_code='B63',
                           indicator='I Thu nội địa')
    except Exception as e:
        refusals.append(("nsnn_trace_source(province='Nghệ An', year=2020, form_code='B63',\n"
                         "                  indicator='I Thu nội địa')",
                         wrap(f'{type(e).__name__}: {e}')))
    try:
        tools.run_sql(sql="ATTACH DATABASE '/tmp/x.db' AS x")
    except Exception as e:
        refusals.append(("nsnn_run_sql(sql=\"ATTACH DATABASE '/tmp/x.db' AS x\")", wrap(str(e))))
    sql = ("SELECT province, COUNT(*) AS rows FROM v_fact WHERE form_code='B46' "
           "GROUP BY province ORDER BY rows DESC LIMIT 5")
    refusals.append(('nsnn_run_sql(sql="SELECT province, COUNT(*) AS rows FROM v_fact"\n'
                     '                 " WHERE form_code=\'B46\'"\n'
                     '                 " GROUP BY province ORDER BY rows DESC LIMIT 5")',
                     wrap(tools.run_sql(sql=sql))))
    term('mcp-refusals', refusals,
         title='it names the ambiguity instead of picking; the SQL hatch is deny-by-default')

    term('mcp-data-quality', [("nsnn_list_data_quality(block='magnitude')",
                               wrap(tools.list_data_quality(block='magnitude')))],
         title='nsnn_list_data_quality — flagged, shown as published, never corrected')

    term('mcp-timeseries-trace', [
        ("nsnn_read_timeseries(province='Quảng Ninh', form_code='B46',\n"
         "                     indicator='A TỔNG NGUỒN THU NSĐP')",
         wrap(tools.read_timeseries(province='Quảng Ninh', form_code='B46',
                                    indicator='A TỔNG NGUỒN THU NSĐP'))),
        ("nsnn_trace_source(province='Nghệ An', year=2020, form_code='B63',\n"
         "                  indicator='I Thu nội địa', series='QUYẾT TOÁN/TỔNG THU NSNN')",
         wrap(tools.trace_source(province='Nghệ An', year=2020, form_code='B63',
                                 indicator='I Thu nội địa',
                                 series='QUYẾT TOÁN/TỔNG THU NSNN'))),
    ], title='one named cell per year — and the untouched source cell behind a number')


def group_cli():
    out = subprocess.run(['./nsnn', '--status'], cwd=ROOT, capture_output=True, text=True)
    lines = out.stdout.rstrip('\n').split('\n')
    term('nsnn-status', [('./nsnn --status', '\n'.join(lines[:14] + ['  …'] + lines[-3:]))],
         width=760, title='./nsnn --status — 34/34 tỉnh đã dựng xong')

    # Slow: it opens all 34 workbooks, which is this group's whole runtime (~10 min).
    out = subprocess.run(['./nsnn', '--units'], cwd=ROOT, capture_output=True, text=True)
    lines = out.stdout.rstrip('\n').split('\n')
    head = next(i for i, l in enumerate(lines) if 'distinct ĐVT' in l)
    term('nsnn-units', [('./nsnn --units', '\n'.join(['  …'] + lines[head:]))],
         width=760, title='./nsnn --units — mọi cách viết ĐVT còn lại trong output/')


def group_workbook():
    """A contiguous slice of a real workbook, copied cell for cell."""
    from openpyxl import load_workbook
    f = sorted((ROOT / 'output').glob('Nghe_An_budget_CKNS_*.xlsx'))[-1]
    cols = ('Chỉ tiêu', 'Loại số liệu', 'Giá trị gốc', 'Giá trị chuẩn hóa', 'ĐVT',
            'Quy đổi VND', 'Ghi chú')
    num = {'Giá trị chuẩn hóa', 'Quy đổi VND'}

    wb = load_workbook(f, read_only=True)
    it = wb['Data'].iter_rows(values_only=True)
    hdr = next(r for r in it if r and 'ĐVT' in r)
    I = {n: list(hdr).index(n) for n in hdr if n}
    rows, started = [], False
    for r in it:
        if not started and str(r[I['Chỉ tiêu']] or '') == 'I Thu nội địa' \
                and 'Quyết toán thu' in str(r[I['Nội dung/Bảng']] or ''):
            started = True
        if started:
            rows.append(r)
            if len(rows) == 13:
                break
    meta = [rows[0][I[c]] for c in ('Tỉnh/TP hiện hành', 'Kỳ dữ liệu', 'Nội dung/Bảng')]
    wb.close()

    def cell(r, c):
        v = r[I[c]]
        if v is None:
            return ''
        if c in num and isinstance(v, (int, float)):
            return f'{v:,.0f}'.replace(',', '.')     # Vietnamese thousands separator
        return str(v)

    body = ''.join('<tr>' + ''.join(
        f'<td class="{"n" if c in num else ""}">{html.escape(cell(r, c))}</td>'
        for c in cols) + '</tr>' for r in rows)
    doc = f"""<meta charset="utf-8"><style>
@font-face{{font-family:VS;src:url("data:font/ttf;base64,{_b64(FONTS / 'dejavu' / 'DejaVuSans.ttf')}")}}
body{{margin:0;background:transparent;font-family:VS,sans-serif}}
.win{{width:1240px;background:#fff;border-radius:10px;overflow:hidden;
      box-shadow:0 10px 30px rgba(0,0,0,.18);border:1px solid #d7dbe0}}
.cap{{padding:11px 14px;background:#f6f7f9;border-bottom:1px solid #e2e6ea;
      font-size:12.5px;color:#4a5259}}
.cap b{{color:#1d2329}}
table{{border-collapse:collapse;width:100%;font-size:12px}}
th{{background:#eef1f4;text-align:left;padding:7px 9px;border-bottom:1px solid #d7dbe0;
    font-weight:700;color:#2b3239;white-space:nowrap}}
td{{padding:6px 9px;border-bottom:1px solid #eef0f2;color:#242a30;white-space:nowrap}}
td.n{{text-align:right;font-variant-numeric:tabular-nums}}
td:nth-child(7){{white-space:normal;width:250px;color:#5a636b;font-size:11px;line-height:1.35}}
tr:nth-child(even) td{{background:#fbfcfd}}
</style>
<div class="win"><div class="cap">Sheet <b>Data</b> · <b>{html.escape(str(meta[0]))}</b> ·
{html.escape(str(meta[1]))} · {html.escape(str(meta[2]))} — 7 trong 16 cột</div>
<table><thead><tr>{''.join(f'<th>{html.escape(c)}</th>' for c in cols)}</tr></thead>
<tbody>{body}</tbody></table></div>"""
    _shot(doc, '.win', OUT / 'workbook-data-sheet.png', 1320)


#: Every chart on the page, in page order. The stem is the element id the section hangs off,
#: so adding a chart to index.html means adding one line here and one image to its README.
DASH_CHARTS = ['self', 'scat', 'rev', 'land', 'sect', 'edu', 'pa', 'debt', 'size', 'trend',
               'heat', 'tl', 'meth', 'unit']


def group_dash():
    """dashboard/index.html, in both themes for the hero and light for the rest."""
    url = (ROOT / 'dashboard' / 'index.html').as_uri()
    charts = [(c, c) for c in DASH_CHARTS]
    with sync_playwright() as pw:
        b = _browser(pw)
        for theme in ('light', 'dark'):
            pg = b.new_page(viewport={'width': 1280, 'height': 900}, device_scale_factor=2,
                            color_scheme=theme)
            pg.goto(url)
            pg.wait_for_timeout(2500)
            pg.evaluate(f"document.documentElement.setAttribute('data-theme','{theme}')")
            pg.wait_for_timeout(400)
            sfx = '' if theme == 'light' else '-dark'
            pg.screenshot(path=str(OUT / f'dashboard-hero{sfx}.png'),
                          clip={'x': 0, 'y': 0, 'width': 1280, 'height': 760})
            print('wrote', (OUT / f'dashboard-hero{sfx}.png').relative_to(ROOT))
            if theme == 'dark':          # only the hero ships a dark variant
                pg.close()
                continue
            for stem, cid in charts:
                el = pg.locator(f'#{cid}').locator('xpath=ancestor::section[1]')
                el.scroll_into_view_if_needed()
                pg.wait_for_timeout(250)
                el.screenshot(path=str(OUT / f'dashboard-{stem}.png'))
                print('wrote', (OUT / f'dashboard-{stem}.png').relative_to(ROOT))
            pg.close()
        b.close()


GROUPS = {'dash': group_dash, 'mcp': group_mcp, 'cli': group_cli, 'workbook': group_workbook}

if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    want = sys.argv[1:] or list(GROUPS)
    unknown = [w for w in want if w not in GROUPS]
    if unknown:
        sys.exit(f"unknown group(s) {unknown} - pick from {list(GROUPS)}")
    for g in want:
        GROUPS[g]()
