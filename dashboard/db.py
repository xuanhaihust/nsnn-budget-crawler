"""Pack the warehouse for git, and restore it afterwards.

    .venv/bin/python dashboard/db.py pack      # data/nsnn.db    -> data/nsnn.db.xz
    .venv/bin/python dashboard/db.py restore   # data/nsnn.db.xz -> data/nsnn.db

The point of packing is that the database is worth committing - rebuilding it costs a
full re-crawl (~18 min) plus the ETL (~6 min) - but 352 MB is not.

Two things make it fit. The four query indexes are 172 MB of those 352, more than the
data they index, and SQLite rebuilds them in seconds, so `pack` drops them and `restore`
recreates them. What is left compresses to about 33 MB, which is under GitHub's 100 MB
per-file limit and therefore goes in as an ordinary git object - it does not touch the
Git LFS quota, which the workbooks in output/ already lean on.

`restore` verifies the file before handing it over: PRAGMA integrity_check, then the row
counts the ETL recorded when it packed.
"""
import lzma, pathlib, shutil, sqlite3, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / 'data' / 'nsnn.db'
XZ = ROOT / 'data' / 'nsnn.db.xz'
sys.path.insert(0, str(ROOT / 'dashboard'))

# Indexes are rebuildable, so they are not shipped. Keep this list in step with etl.py.
REBUILDABLE = ('ix_fact_prov', 'ix_fact_period', 'ix_fact_ind', 'ix_fact_pps', 'ix_report_prov')


def _counts(con):
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ('fact_row', 'dim_province', 'dim_report', 'dim_indicator')}


def pack():
    if not DB.exists():
        sys.exit(f"{DB} not found - run dashboard/etl.py first")
    tmp = DB.with_suffix('.packing')
    shutil.copy2(DB, tmp)
    con = sqlite3.connect(tmp)
    have = [n for (n,) in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
    for n in REBUILDABLE:
        if n in have:
            con.execute(f"DROP INDEX {n}")
    con.commit()
    con.execute("VACUUM")
    counts = _counts(con)
    con.close()

    t0 = time.time()
    with open(tmp, 'rb') as src, lzma.open(XZ, 'wb', preset=6) as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    stripped = tmp.stat().st_size
    tmp.unlink()
    print(f"{DB.stat().st_size/1e6:.0f} MB  ->  {stripped/1e6:.0f} MB without indexes"
          f"  ->  {XZ.stat().st_size/1e6:.0f} MB packed   ({time.time()-t0:.0f}s)")
    for k, v in counts.items():
        print(f"  {k:<14} {v:,}")


def restore():
    if not XZ.exists():
        sys.exit(f"{XZ} not found")
    DB.parent.mkdir(exist_ok=True)
    t0 = time.time()
    with lzma.open(XZ, 'rb') as src, open(DB, 'wb') as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    con = sqlite3.connect(DB)
    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != 'ok':
        sys.exit(f"integrity_check failed: {ok}")
    import etl
    con.executescript(etl.INDEX)
    con.commit()
    counts = _counts(con)
    con.close()
    print(f"{XZ.stat().st_size/1e6:.0f} MB  ->  {DB.stat().st_size/1e6:.0f} MB restored, "
          f"indexes rebuilt, integrity ok   ({time.time()-t0:.0f}s)")
    for k, v in counts.items():
        print(f"  {k:<14} {v:,}")


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'pack':
        pack()
    elif cmd == 'restore':
        restore()
    else:
        sys.exit(__doc__)
