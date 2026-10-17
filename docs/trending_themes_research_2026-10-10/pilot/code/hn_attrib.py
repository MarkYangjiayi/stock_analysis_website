"""Top HN stories (by points) in a given fire week, to attribute what drove the spike."""
import sys, os, time, datetime as dt, requests, pandas as pd
sys.path.insert(0, os.path.dirname(__file__)); from config import EPISODES, CONTROLS
S = requests.Session(); S.headers["User-Agent"] = "QuantDashboard-research-pilot/0.1 (personal non-commercial research; low volume)"
R = pd.concat([pd.read_csv(os.path.join(sys.argv[1], "episode_results.csv")).rename(columns={"episode": "name"}),
               pd.read_csv(os.path.join(sys.argv[1], "control_results.csv")).rename(columns={"control": "name"})])
for _, r in R[R.src == "hn"].iterrows():
    if pd.isna(r.z3): continue
    cfg = EPISODES.get(r["name"]) or CONTROLS.get(r["name"])
    end = pd.Timestamp(r.z3) + pd.Timedelta(days=1); start = end - pd.Timedelta(days=7)
    a, b = int(start.timestamp()), int(end.timestamp()); hits = []
    for ph in cfg["hn"]:
        d = S.get("https://hn.algolia.com/api/v1/search_by_date", params={"query": f'"{ph}"', "tags": "story", "typoTolerance": "false",
              "restrictSearchableAttributes": "title,url", "numericFilters": f"created_at_i>={a},created_at_i<{b}", "hitsPerPage": 200,
              "attributesToRetrieve": "title,points,created_at", "attributesToHighlight": ""}, timeout=60).json(); time.sleep(0.8)
        hits += [(h.get("points") or 0, h["created_at"][:10], h["title"][:85]) for h in d["hits"]]
    top = sorted(set(hits), reverse=True)[:3]
    print(f"{r['name']} week {r.z3}: n={len(set(hits))} :: " + " || ".join(f"{t} ({p}pts {c})" for p, c, t in top))
