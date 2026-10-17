"""HN Algolia: all stories whose title/url contains the exact phrase (quoted, typoTolerance=false), 2014-01-01..2026-10-10.
Windows are split until nbHits <= 1000 so every hit is paged (exact count, not the nbHits estimate). ~1.5 req/s."""
import json, os, sys, time, requests
sys.path.insert(0, os.path.dirname(__file__)); from config import all_items
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
S = requests.Session(); S.headers["User-Agent"] = "QuantDashboard-research-pilot/0.1 (personal non-commercial research; low volume)"
BASE = "https://hn.algolia.com/api/v1/search_by_date"
T0, T1 = 1388534400, 1791590400  # 2014-01-01 .. 2026-10-10 UTC
def q(phrase, a, b, page=0, hpp=1000):
    p = {"query": f'"{phrase}"', "tags": "story", "typoTolerance": "false", "restrictSearchableAttributes": "title,url",
         "numericFilters": f"created_at_i>={a},created_at_i<{b}", "hitsPerPage": hpp, "page": page,
         "attributesToRetrieve": "created_at_i,objectID,title,points,num_comments", "attributesToHighlight": ""}
    for i in range(6):
        r = S.get(BASE, params=p, timeout=60); time.sleep(0.7)
        if r.status_code == 429: time.sleep(60); continue
        if r.ok: return r.json()
        time.sleep(5)
    raise RuntimeError(r.status_code)
def collect(phrase, a, b, acc):
    d = q(phrase, a, b, hpp=0)
    n = d["nbHits"]
    if n == 0: return
    if n > 1000 and b - a > 3600:
        m = (a + b) // 2; collect(phrase, a, m, acc); collect(phrase, m, b, acc); return
    page = 0
    while True:
        d = q(phrase, a, b, page=page)
        for h in d["hits"]: acc[h["objectID"]] = [h["created_at_i"], h.get("points"), h.get("num_comments")]
        page += 1
        if page >= d.get("nbPages", 0): break
for ph in all_items("hn"):
    fn = os.path.join(OUT, ph.replace("/", "_").replace(" ", "_") + ".json")
    if os.path.exists(fn): continue
    acc = {}
    collect(ph, T0, T1, acc)
    json.dump({"phrase": ph, "hits": acc}, open(fn, "w"))
    print(ph, len(acc), flush=True)
