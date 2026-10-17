import json, sys, time, requests
UA = "QuantDashboard-research-pilot/0.1 (personal non-commercial research; low volume)"
S = requests.Session(); S.headers["User-Agent"] = UA
titles = sys.argv[1].split("|")
out = {}
r = S.get("https://en.wikipedia.org/w/api.php", params={"action":"query","titles":"|".join(titles),"redirects":1,"format":"json","prop":"info"}).json()
redir = {d["from"]: d["to"] for d in r["query"].get("redirects", [])}
norm = {d["from"]: d["to"] for d in r["query"].get("normalized", [])}
pages = {p["title"]: p for p in r["query"]["pages"].values()}
for t in titles:
    t1 = norm.get(t, t); t2 = redir.get(t1, t1)
    p = pages.get(t2, {})
    if "missing" in p or not p:
        out[t] = {"canonical": t2, "missing": True}; continue
    rv = S.get("https://en.wikipedia.org/w/api.php", params={"action":"query","titles":t2,"prop":"revisions","rvdir":"newer","rvlimit":1,"rvprop":"timestamp|user|size","format":"json"}).json()
    pg = list(rv["query"]["pages"].values())[0]
    out[t] = {"canonical": t2, "created": pg["revisions"][0]["timestamp"], "first_rev_size": pg["revisions"][0].get("size"), "redirected": t1 != t2}
    time.sleep(0.3)
print(json.dumps(out, indent=1))
