"""EDGAR full-text search: 8-K documents containing the exact phrase, 2015-01-01..2026-10-09.
Each hit is a document (main 8-K or exhibit); we keep (adsh -> file_date, cik) so counts can be deduplicated per filing.
Ranges whose total reaches the 10,000 cap are split recursively. <= ~4 req/s."""
import json, os, sys, time, datetime as dt, requests
sys.path.insert(0, os.path.dirname(__file__)); from config import all_items
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
S = requests.Session(); S.headers["User-Agent"] = "QuantDashboard-research-pilot/0.1 (personal non-commercial research; low volume)"
BASE = "https://efts.sec.gov/LATEST/search-index"
def q(phrase, a, b, frm=0):
    p = {"q": f'"{phrase}"', "forms": "8-K", "dateRange": "custom", "startdt": a.isoformat(), "enddt": b.isoformat(), "from": frm}
    for i in range(8):
        try:
            r = S.get(BASE, params=p, timeout=60)
        except requests.RequestException:
            time.sleep(5); continue
        time.sleep(0.25)
        if r.ok: return r.json()
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"{r.status_code} {phrase} {a} {b} {frm}")
def collect(phrase, a, b, acc, stats):
    d = q(phrase, a, b)
    tot = d["hits"]["total"]
    if (tot["value"] >= 10000 or tot.get("relation") == "gte") and (b - a).days > 0:
        m = a + (b - a) // 2
        collect(phrase, a, m, acc, stats); collect(phrase, m + dt.timedelta(days=1), b, acc, stats); return
    n = tot["value"]; frm = 0
    while True:
        for h in d["hits"]["hits"]:
            s = h["_source"]; stats["docs"] += 1
            acc.setdefault(s["adsh"], [s["file_date"], (s.get("ciks") or [None])[0]])
        frm += 100
        if frm >= n or not d["hits"]["hits"]: break
        d = q(phrase, a, b, frm)
for ph in all_items("edgar"):
    fn = os.path.join(OUT, ph.replace("/", "_").replace(" ", "_") + ".json")
    if os.path.exists(fn): continue
    acc, stats = {}, {"docs": 0}
    collect(ph, dt.date(2015, 1, 1), dt.date(2026, 10, 9), acc, stats)
    json.dump({"phrase": ph, "docs": stats["docs"], "filings": acc}, open(fn, "w"))
    print(ph, "docs", stats["docs"], "filings", len(acc), flush=True)
