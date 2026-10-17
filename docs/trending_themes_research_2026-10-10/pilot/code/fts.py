import json, sys, time, requests
S = requests.Session(); S.headers["User-Agent"] = "QuantDashboard-research-pilot/0.1 (personal non-commercial research; low volume)"
def fts(q, forms=None, start=None, end=None, n=10):
    p = {"q": q}
    if forms: p["forms"] = forms
    if start: p.update(dateRange="custom", startdt=start, enddt=end)
    r = S.get("https://efts.sec.gov/LATEST/search-index", params=p, timeout=60); time.sleep(0.4)
    d = r.json(); out = []
    for h in d["hits"]["hits"][:n]:
        s = h["_source"]; adsh = s["adsh"]; cik = s["ciks"][0].lstrip("0"); fn = h["_id"].split(":", 1)[1]
        out.append((s["file_date"], s["form"], s["display_names"][0][:70], f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-','')}/{fn}"))
    return d["hits"]["total"]["value"], out
if __name__ == "__main__":
    q = sys.argv[1]; forms = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "-" else None
    start = sys.argv[3] if len(sys.argv) > 3 else None; end = sys.argv[4] if len(sys.argv) > 4 else None
    tot, out = fts(q, forms, start, end)
    print(q, "| total", tot)
    for o in sorted(out): print("  ", *o)
