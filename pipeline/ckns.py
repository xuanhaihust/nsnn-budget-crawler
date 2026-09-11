"""CKNS (Bo Tai chinh) catalog client + downloader."""
import json, urllib.request, urllib.parse, hashlib, pathlib, time, re

API = "https://ckns.mof.gov.vn/_vti_bin/DeptService.svc/"
BASE = {"SiteId": "e9e24430-ec5e-4b44-8a1d-a3d06c1e6ed2",
        "WebId":  "60d972cc-567c-448d-b0a3-81ae171a2fe1",
        "ListId": "dde649ff-8645-4e57-b9bc-c3f640ecd468"}
UA = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
PAGE = 50  # server mis-pages above ~50

def post(ep, **kw):
    d = dict(BASE, KeySearch="", Year=0, PeriodTypeId=0, ReportTypeId=0,
             DeparmentId=0, PageIndex=1, PageSize=PAGE, DepartmentTypeId=0)
    d.update(kw)
    req = urllib.request.Request(API + ep, json.dumps(d).encode(), UA)
    for attempt in range(4):
        try:
            raw = urllib.request.urlopen(req, timeout=60).read().decode()
            return json.loads(json.loads(raw))
        except Exception:
            if attempt == 3: raise
            time.sleep(2 * (attempt + 1))

def departments():
    return {d["Title"]: d["ID"] for d in post("GetDeparment")}

def catalog(dept_id):
    """All reports for one department, every year."""
    items, page = [], 1
    while True:
        r = post("SearchReport", DeparmentId=dept_id, PageIndex=page, PageSize=PAGE)
        batch = r.get("Items") or []
        items += batch
        total = r.get("TotalItems", 0)
        if not batch or len(items) >= total: break
        page += 1
    return items, total

DATE = re.compile(r"/Date\((\d+)")
def ms(v):
    m = DATE.search(v or "")
    return time.strftime("%d/%m/%Y", time.localtime(int(m.group(1)) / 1000)) if m else ""

def fetch(url, dest: pathlib.Path):
    """Download to dest, return (sha256, size). Skip if present."""
    if dest.exists():
        b = dest.read_bytes()
        return hashlib.sha256(b).hexdigest(), len(b)
    sp = urllib.parse.urlsplit(url)
    url = urllib.parse.urlunsplit(sp._replace(path=urllib.parse.quote(sp.path, safe="/%")))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(3):
        try:
            b = urllib.request.urlopen(req, timeout=90).read()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b)
            return hashlib.sha256(b).hexdigest(), len(b)
        except Exception:
            if attempt == 2: raise
            time.sleep(2 * (attempt + 1))
