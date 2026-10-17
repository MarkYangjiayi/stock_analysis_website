#!/usr/bin/env python3
"""Measure the real API-call cost of EODHD news/sentiment/word-weights/ETF endpoints.

Usage:  EODHD_API_KEY=... python3 eodhd_cost_probe.py [--sentiment-max] [--coverage-file tickers.txt]
Pause the QuantDashboard worker first: the counter is account-wide.
Budget: roughly 600-1,200 API calls. /api/user itself costs 0 calls.
"""
import argparse, datetime as dt, json, os, sys, time, urllib.error, urllib.parse, urllib.request

KEY = os.environ.get("EODHD_API_KEY", "")
if not KEY:
    sys.exit("Set EODHD_API_KEY in the environment.")
BASE = "https://eodhd.com/api/"
PAUSE = 1.5  # seconds between requests (well under 1 req/s)


def redact(s):
    return str(s).replace(KEY, "***") if KEY else str(s)


def call(path, **params):
    q = dict(params, api_token=KEY, fmt="json")
    url = BASE + path + "?" + urllib.parse.urlencode(q, safe=",[]")
    req = urllib.request.Request(url, headers={"User-Agent": "eodhd-cost-probe"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            status, body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # never echo the URL: it contains the key
        status, body = -1, redact(type(e).__name__ + ": " + str(e))
    time.sleep(PAUSE)
    try:
        return status, json.loads(body)
    except ValueError:
        return status, redact(body[:200])


def usage():
    st, u = call("user")
    if st != 200 or not isinstance(u, dict):
        sys.exit(f"/api/user failed: HTTP {st} {redact(u)}")
    return int(u.get("apiRequests") or 0), u.get("apiRequestsDate"), u.get("dailyRateLimit")


def rows(data):
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        if "errors" in data or "error" in data:
            return redact(str(data)[:120])
        if "data" in data and isinstance(data["data"], dict):  # word weights
            return f"{len(data['data'])} words, meta={data.get('meta')}"
        if "ETF_Data" in data or "General" in data:  # fundamentals
            h = (data.get("ETF_Data") or {}).get("Holdings") or {}
            return f"sections={list(data)[:6]}, holdings={len(h)}"
        if data and all(isinstance(v, list) for v in data.values()):  # sentiments
            return f"{len(data)} tickers, {sum(len(v) for v in data.values())} day-rows"
        if len(data) and all(isinstance(v, dict) for v in data.values()):  # filtered holdings
            return f"{len(data)} keys"
    return redact(str(data)[:120])


RESULTS = []


def measure(label, path, **params):
    before, day, _ = usage()
    st, data = call(path, **params)
    time.sleep(1.0)
    after, _, _ = usage()
    time.sleep(2.0)
    drift, _, _ = usage()  # should equal `after` if nothing else is spending calls
    noisy = drift != after
    shown = {k: v for k, v in params.items()}
    RESULTS.append({"test": label, "path": path, "params": shown, "http": st,
                    "cost": after - before, "noisy": noisy, "rows": rows(data)})
    print(f"{label:<44} HTTP {st:<4} cost={after - before:<6}{' (NOISY)' if noisy else '':<9} rows={rows(data)}", flush=True)
    return st, data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sentiment-max", action="store_true", help="also try 100 and 101 tickers")
    ap.add_argument("--coverage-file", help="one ticker per line, e.g. a Russell 2000 sample (<=100)")
    args = ap.parse_args()

    today = dt.datetime.now(dt.timezone.utc).date()
    y = (today - dt.timedelta(days=1)).isoformat()
    m30 = (today - dt.timedelta(days=30)).isoformat()
    a, day, limit = usage()
    print(f"apiRequests={a} date={day} dailyRateLimit={limit}")
    if day != today.isoformat():
        print("Counter date is not today (UTC); the first paid request will reset it. Running a 1-call warm-up.")
        call("exchanges-list")
    a1, _, _ = usage(); time.sleep(3); a2, _, _ = usage()
    if a1 != a2:
        print("WARNING: counter moved with no requests from this script. Stop other jobs, or treat results as noisy.")

    # --- /api/news ---
    measure("news s=AAPL.US limit=50", "news", s="AAPL.US", limit=50, **{"from": y, "to": y})
    measure("news s=AAPL.US limit=1000", "news", s="AAPL.US", limit=1000, **{"from": m30, "to": y})
    measure("news s=AAPL.US,TSLA.US (2 tickers)", "news", s="AAPL.US,TSLA.US", limit=10)
    measure("news t=artificial-intelligence", "news", t="artificial-intelligence", limit=1000, **{"from": y, "to": y})
    measure("news t=artificial intelligence (docs prose)", "news", t="artificial intelligence", limit=1000, **{"from": y, "to": y})
    measure("news t=AI", "news", t="AI", limit=1000, **{"from": y, "to": y})
    measure("news t=earnings report (standard tag)", "news", t="earnings report", limit=1000, **{"from": y, "to": y})
    st, gen = measure("news no s/no t limit=50", "news", limit=50)
    general_ok = st == 200 and isinstance(gen, list) and len(gen) > 0
    if general_ok:
        measure("news no s/no t limit=1000 (yesterday)", "news", limit=1000, **{"from": y, "to": y})
        # Count yesterday's market-wide volume by paging (max 20 pages).
        total, off = 0, 0
        for _ in range(20):
            st, page = call("news", limit=1000, offset=off, **{"from": y, "to": y})
            n = len(page) if isinstance(page, list) else 0
            total += n
            if n < 1000:
                break
            off += 1000
        print(f"general feed articles on {y}: {total}{' (>= 20 pages, truncated)' if total >= 20000 else ''}")
    measure("news s=INVALIDXX.US (404 charged?)", "news", s="INVALIDXX.US", limit=1)

    # --- max offset accepted ---
    def max_offset(**base):
        ok = 0
        for off in (1000, 10000, 50000, 70000, 100000, 200000):
            st, d = call("news", limit=1, offset=off, **base)
            n = len(d) if isinstance(d, list) else redact(str(d)[:60])
            print(f"  offset={off:<7} HTTP {st} rows={n}")
            if st == 200 and isinstance(d, list) and d:
                ok = off
            elif st != 200:
                break
        return ok
    print("max offset, s=AAPL.US:", max_offset(s="AAPL.US"))
    if general_ok:
        print("max offset, general feed:", max_offset())

    # --- /api/sentiments ---
    tick50 = ("AAPL MSFT AMZN TSLA NVDA GOOGL META AVGO JPM V MA UNH XOM LLY JNJ PG HD COST ABBV MRK "
              "PEP KO ADBE CRM NFLX AMD INTC QCOM TXN ORCL CSCO IBM MU AMAT LRCX KLAC SMCI PLTR "
              "CEG VST OKLO SMR IONQ RGTI NVO HOOD COIN MSTR ARM").split()
    tick50 = [t + ".US" for t in tick50]
    for n in (1, 10, 50):
        measure(f"sentiments {n} tickers (30d)", "sentiments", s=",".join(tick50[:n]), **{"from": m30, "to": y})
    measure("sentiments 10 tickers (1 day)", "sentiments", s=",".join(tick50[:10]), **{"from": y, "to": y})
    if args.sentiment_max:
        extra = [f"{t}.US" for t in "BAC WFC C GS MS SCHW BLK SPGI ICE CME".split()]
        big = (tick50 + extra * 6)[:101]
        measure("sentiments 100 tickers", "sentiments", s=",".join(big[:100]), **{"from": y, "to": y})
        measure("sentiments 101 tickers", "sentiments", s=",".join(big[:101]), **{"from": y, "to": y})

    # --- /api/news-word-weights ---
    measure("word-weights s=AAPL.US 30d", "news-word-weights", s="AAPL.US",
            **{"filter[date_from]": m30, "filter[date_to]": y, "page[limit]": 50})

    # --- ETF fundamentals ---
    measure("fundamentals URA.US (full)", "fundamentals/URA.US")
    measure("fundamentals URA.US filter=ETF_Data::Holdings", "fundamentals/URA.US", filter="ETF_Data::Holdings")

    # --- optional small-cap coverage check (one sentiments request) ---
    if args.coverage_file:
        ticks = [l.strip() for l in open(args.coverage_file) if l.strip()][:100]
        ticks = [t if "." in t else t + ".US" for t in ticks]
        st, d = measure(f"coverage sentiments {len(ticks)} tickers", "sentiments", s=",".join(ticks), **{"from": m30, "to": y})
        if isinstance(d, dict):
            counts = sorted(sum(r.get("count", 0) for r in d.get(t.upper(), d.get(t, []))) for t in ticks)
            zero = sum(c == 0 for c in counts)
            print(f"  30-day article counts: zero={zero}/{len(counts)}, median={counts[len(counts)//2]}, max={counts[-1]}")

    out = f"eodhd_cost_probe_{today.isoformat()}.json"
    json.dump(RESULTS, open(out, "w"), indent=1)
    print(f"saved {out} (contains no key)")


if __name__ == "__main__":
    main()
