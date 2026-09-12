"""Inline dashboard/data.json into template.html to produce a single self-contained page.

The dashboard must open from a file:// path, from GitHub Pages and inside a sandboxed
artifact frame, none of which can rely on a fetch succeeding — so the data travels
inside the HTML rather than beside it.
"""
import json, pathlib

HERE = pathlib.Path(__file__).resolve().parent
data = json.loads((HERE / 'data.json').read_text())
html = (HERE / 'template.html').read_text()
assert '/*__DATA__*/' in html, 'template lost its data placeholder'
out = html.replace('/*__DATA__*/', json.dumps(data, ensure_ascii=False, separators=(',', ':')))
dest = HERE / 'index.html'
dest.write_text(out)
print(f"{dest}  {dest.stat().st_size/1024:.0f} KB")
