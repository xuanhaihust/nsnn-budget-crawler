"""Download every CKNS attachment for one province into work/<slug>/raw."""
import sys, json, pathlib, unicodedata, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import ckns

def slug(s):
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn').replace('đ','d').replace('Đ','D')
    return re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_')

def run(province, dept_id, root=pathlib.Path('work')):
    out = root / slug(province)
    raw = out / 'raw'; raw.mkdir(parents=True, exist_ok=True)
    items, total = ckns.catalog(dept_id)
    print(f"{province}: {len(items)} reports (server claims {total})")

    jobs = []
    for it in items:
        rid = it.get('ID')
        for a in it.get('Attachments') or []:
            jobs.append((rid, a['FileName'], a['Url']))
    print(f"attachments: {len(jobs)}")

    recs, fail = [], []
    def one(j):
        rid, name, url = j
        dest = raw / str(rid) / name
        h, n = ckns.fetch(url, dest)
        return dict(report_id=rid, filename=name, url=url,
                    path=str(dest.relative_to(out)), sha256=h, bytes=n)

    t = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(one, j): j for j in jobs}
        for f in as_completed(futs):
            try: recs.append(f.result())
            except Exception as e: fail.append({'job': futs[f], 'err': str(e)})
    print(f"downloaded {len(recs)}, failed {len(fail)} in {time.time()-t:.1f}s")

    (out / 'catalog.json').write_text(json.dumps(items, ensure_ascii=False, indent=1))
    (out / 'files.json').write_text(json.dumps(recs, ensure_ascii=False, indent=1))
    if fail: (out / 'failed.json').write_text(json.dumps(fail, ensure_ascii=False, indent=1))
    return out, recs, fail

if __name__ == '__main__':
    run(sys.argv[1], int(sys.argv[2]))
