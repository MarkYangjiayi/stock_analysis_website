"""Wikipedia: canonical title, creation date (first revision) and daily 'user' pageviews 2015-07-01..2026-10-09.
Paced at ~1 request / 7 s because a UA without contact info is limited to 10 req/min/IP (Wikimedia 2026 policy)."""
import json, os, sys, time, urllib.parse, requests
sys.path.insert(0, os.path.dirname(__file__)); from config import all_items
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
UA = "QuantDashboard-research-pilot/0.1 (personal non-commercial research; low volume) python-requests"
S = requests.Session(); S.headers["User-Agent"] = UA
def get(url, params=None):
    for i in range(8):
        r = S.get(url, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("retry-after", "60")) + 2); continue
        time.sleep(6.5)
        return r
    raise RuntimeError("rate limited")
for t in sys.argv[2:]:
    fn = os.path.join(OUT, t.replace("/", "_") + ".json")
    if os.path.exists(fn): continue
    r = get("https://en.wikipedia.org/w/api.php", {"action":"query","titles":t,"prop":"revisions","rvdir":"newer","rvlimit":1,"rvprop":"timestamp|size","format":"json"}).json()
    pg = list(r["query"]["pages"].values())[0]
    rec = {"title": t, "canonical": pg.get("title"), "missing": "missing" in pg,
           "redirects": r["query"].get("redirects", []),
           "created": pg["revisions"][0]["timestamp"] if "revisions" in pg else None}
    if not rec["missing"]:
        art = urllib.parse.quote(rec["canonical"].replace(" ", "_"), safe="")
        u = f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{art}/daily/20150701/20261009"
        pv = get(u)
        rec["pv_status"] = pv.status_code
        rec["daily"] = {it["timestamp"][:8]: it["views"] for it in pv.json().get("items", [])} if pv.ok else {}
    json.dump(rec, open(fn, "w"))
    print(t, rec.get("canonical"), rec.get("created"), rec.get("pv_status"), len(rec.get("daily", {})), flush=True)
