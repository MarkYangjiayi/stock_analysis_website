import sys, time, json, requests
S = requests.Session(); S.headers["User-Agent"] = "QuantDashboard-research-pilot/0.1 (personal non-commercial research; low volume) python-requests"
for t in sys.argv[1:]:
    for i in range(6):
        r = S.get("https://en.wikipedia.org/w/api.php", params={"action":"query","titles":t,"prop":"revisions","rvdir":"newer","rvlimit":2,"rvprop":"timestamp|size|comment","format":"json"}, timeout=60)
        if r.status_code == 429: time.sleep(int(r.headers.get("retry-after", "60")) + 2); continue
        break
    pg = list(r.json()["query"]["pages"].values())[0]
    print(t, "|", [(x["timestamp"], x.get("size"), x.get("comment", "")[:90]) for x in pg.get("revisions", [])], flush=True)
    time.sleep(6.5)
