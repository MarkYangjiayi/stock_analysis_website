# EODHD News/Sentiment/ETF-holdings: capabilities, true API-call cost, ingestion budget and licence terms

EODHD is already paid for and is the most likely backbone for the news corpus and attention signals. A demo-key probe on 2026-10-10 found that /api/news takes only one ticker per request. A per-ticker Russell 3000 pull would cost about 31k calls/day, against a plan the code suggests is around 100k/day. Whether a cheaper market-wide or tag-based route exists decides whether the owner needs to build a different corpus. Cost formulas, the general feed, t= queries, small-cap coverage and storage/display terms are all unresolved or contradictory.

# EODHD News, Sentiment and ETF Holdings: real cost, budget and licence (researched 2026-10-10)

**Method and limits.** I used EODHD's own pages: the docs, `api-limits`, pricing, ToS, the licence page, and blog posts found through EODHD's sitemaps. I also read the official GitHub repos (`EODHD-openapi`, the Python library, `EODHD-MCP-Server`, `eodhd-claude-skills`). Live probes used only `api_token=demo`: about 240 requests at 1.5–2.5 s spacing. The WebSearch budget for this run ran out on my first query, so I did not search Reddit or other forums. Anything that depends on them is marked unknown. Some text is paraphrased because of a quoting limit; each item names the URL and section so the exact wording can be checked. No repo files were changed and no company systems were queried.

## Bottom line
1. **Budget is not the blocker.** Four official sources say one `/api/news` request costs **5 calls**. A per-ticker daily pull of 3,100 tickers is therefore about 15.5k calls/day. The 31k figure comes from a formula in EODHD's MCP docs that contradicts itself. Even at 31k it fits inside the ~85k/day of spare quota.
2. **`/api/sentiments` `count` is the same number as the per-ticker news article count.** Over 30 days the demo totals matched almost exactly: AAPL 1080 vs 1081, AMZN 1222/1222, MSFT 1355/1356, TSLA 783/783, VTI 36/36. So it is a ready-made attention series. It is priced per ticker, not per date range, so the full history (back to 2012) for every ticker should cost about one batch of ~15.5k calls (inferred).
3. **Whether a general feed exists is still open.** The docs (updated 2026-07-15) say it exists. The OpenAPI spec, the Python library, the MCP tool and the Academy all say `s` or `t` is required. Only the owner's key can settle it, and it decides the design.
4. **The data is mostly Yahoo Finance.** 93–96% of links in the last 30 days go to finance.yahoo.com, which largely overlaps the existing Yahoo RSS feature. The series also has artefacts: a near-outage in Nov–Dec 2024 and a mass mis-tagging burst on 2025-08-12..21. AI topic tags only became common from about mid-2025.
5. **Licence: owner-only use.** The personal-plan ToS forbids displaying the data to anyone else, even in repackaged form. Showing news or derived scores to other viewers needs a commercial licence.

## (a) Facts from EODHD's own sources

| # | Fact | Source | Date / confidence |
|---|---|---|---|
| 1 | News: one request costs 5 calls. The cost table lists News API = 5. Where an endpoint takes several symbols in one request, the cost is multiplied by the number of symbols. | https://eodhd.com/financial-apis/api-limits (cost table; page modified 2026-09-16) | fetched 10-10; High |
| 2 | The news docs say 5 calls per request plus 5 per ticker, with the example "10 for one request with two tickers". That example only works out as 5 × n. | https://eodhd.com/financial-apis/stock-market-financial-news-api ("About the API" block, modified 2026-07-15) | High that the text says this; the formula itself is ambiguous |
| 3 | The pricing page also says news costs 5 per request. | https://eodhd.com/pricing | High |
| 4 | Blog (2021-09-30): each news request costs 5 calls. | https://eodhd.com/financial-apis-blog/financial-news-api-cryptocurrencies-forex-support | High (but old) |
| 5 | `limit=1000` should cost the same as `limit=50`. The limits page says you pay for the query, not the size of the response. That remark was made about the `filter` parameter; applying it to `limit` is my inference. | api-limits page | Medium-High; owner to confirm |
| 6 | Cost of a `t=` query or a request with no `s`/`t`: no official statement. By the per-request table it should be 5. | — | Low-Medium |
| 7 | Sentiments: `s` takes several comma-separated tickers. Cost is 5 per ticker under the docs example, or 5 + 5n under the MCP reference. | news docs page; https://github.com/EodHistoricalData/EODHD-openapi/blob/main/paths/sentiments.yaml | Medium |
| 8 | Sentiments: at most 100 tickers per request. This appears only in the MCP reference, not in the official docs. | https://github.com/EodHistoricalData/EODHD-MCP-Server/blob/main/app/resources/references/endpoints/sentiment-data.md | Low-Medium |
| 9 | Word weights: one ticker; same "5 + 5 per ticker" block as the news page. OpenAPI: `page[limit]` max 500, default 10. | news docs page; `paths/news-word-weights.yaml` | Medium |
| 10 | ETF fundamentals cost 10 calls per request, and using `filter` does not lower the cost. | https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds (modified 2026-10-10); api-limits | High |
| 11 | Bulk fundamentals does **not** cover ETFs: the page says it covers common stocks only, not ETFs or mutual funds. It also needs the separate Extended Fundamentals plan. Cost is 100, or 100+N with `symbols`, max 500 symbols. | https://eodhd.com/financial-apis/bulk-fundamentals-api (modified 2026-08-19) | High |
| 12 | News `limit`: default 50, range 1–1000. `offset`: default 0, no maximum stated (OpenAPI only sets minimum 0). | docs page; `paths/news.yaml` (last changed 2026-06-16) | High |
| 13 | General feed: the docs say that if both `s` and `t` are omitted, the latest general feed is returned. The OpenAPI spec says each is required if the other is missing. The Python library raises an error if both are missing. | docs page; news.yaml; https://github.com/EodHistoricalData/EODHD-APIs-Python-Financial-Library/blob/main/eodhd/APIs/FinancialNewsAPI.py | **Open** |
| 14 | The "50 standard tags" list is published, but it actually has **53** entries. The AI-detected tag vocabulary is **not** published; the Python library says there is no fixed list. | docs page; Python library (commit 2025-12-23, "Remove static tags list") | High |
| 15 | Daily limit is 100,000 on every paid personal plan; 1,000 requests/min. A request refused with 402 costs nothing. A 404 for a non-existent ticker is still charged. `/api/user` costs 0. | api-limits; pricing | High |
| 16 | Personal plans and prices: EOD $19.99, EOD+Intraday $29.99, Fundamentals $59.99, All-in-One $99.99, all 100k/day. The calendar data the owner uses only comes with Fundamentals or All-in-One, so the owner is probably on one of those (inference). | https://eodhd.com/pricing | High / inference |

## (b) Demo-key measurements, 2026-10-10

- **Which tickers work.** AAPL, TSLA, AMZN, MSFT and VTI all returned data. MSFT works even though it is not in the documented demo list. NVDA returned 403.
- **What the demo cannot do.** `t=` alone, no `s`/`t`, and sentiments that include a non-demo ticker all returned 403.
- **The demo cannot measure cost.** The shared demo counter rose by about 35–40 calls every 2.5 s because other people use the key. It reports `dailyRateLimit` 10,000,000 and `subscriptionType` "test".

| Ticker / window | Articles per day | Link domains | `symbols[]` size: median; % with exactly 50 | Title mentions name/ticker | Title or body mentions | Exact / near-duplicate titles |
|---|---|---|---|---|---|---|
| AAPL last 30 d | 36.0 | yahoo 94%, seekingalpha 5%, cnbc 1% | 11; 4% | 32% | 88% | 1.9% / 3.1% |
| TSLA last 30 d | 26.1 | yahoo 93%, SA 6% | 8; 4% | 40% | 92% | 1.9% / 3.3% |
| AMZN last 30 d | 40.7 | yahoo 94%, SA 5% | 11; 5% | 29% | 87% | 1.9% / 3.0% |
| MSFT last 30 d | 45.2 | yahoo 96%, SA 3% | 11; 5% | 25% | 87% | 1.9% / 3.5% |
| VTI last 30 d | 1.2 | yahoo 94% | 6; 0% | 8% | 81% | 0 / 0 |
| 2023-06 (AAPL/TSLA/AMZN/MSFT) | 24.4 / 33.8 / 22.8 / 22.4 (VTI 1 article) | yahoo 94–97%, reuters 2–3% | 7–10; 0% | 39–58% | 81–87% | 2.0–3.8% / 5.6–8.1% |
| 2020-06 | 0.1–0.6 | yahoo ~90% | 7–12 | 0–29% | 50–100% | ~0 |
| 2017-06 | 0 for all five | — | — | — | — | — |
| AAPL 2025-08-13 (burst day) | **1,500** | yahoo 57%, investing 19%, globenewswire 15% | 5; 1% | **1%** | **8%** | 3.4% / 8.9% |

Notes on the table:
- Near-duplicate means word-set Jaccard similarity of at least 0.8. No two articles shared the same link.
- Body text: the last 30 days have a median of 3.6k–4.8k characters, with 8–13% under 300 characters. In 2023-06 the median was 431–1,773 characters, with 27–41% under 300 (stubs).

**Earliest article dates (binary search, confirmed by sentiment counts):** AAPL 2012-05-04, MSFT 2015-07-14, TSLA 2015-07-16, AMZN 2015-10-22, VTI 2017-07-24.

**Volume over time.** Data is very sparse before 2020-06. Full volume starts around 2021-03/04, when the API launched. AAPL monthly counts:
- about 400–900 a month in 2021–2024;
- 63 in Nov 2024 and 42 in Dec 2024 (near-outage);
- 16,577 in Aug 2025 (burst);
- about 1,200–2,400 a month in 2026.

**`offset`.** No cap was observed. `offset=74,609` still returned a row; 75,000 returned none because AAPL's history ends at about 74.7k articles.

**How `t=` matches (tested with `s=AAPL.US`, 30 days, local counts matched exactly):**
- `s` and `t` combine as AND.
- Matching is exact and case-insensitive: `AI` = `ai` = 301 articles.
- There is no substring match: 357 articles have tags starting with "AI", but `t=AI` returned 301.
- There is no hyphen/space normalisation: `price target` returned 71 and `price-target` returned 107.
- `artificial-intelligence` returned 30; `artificial intelligence` returned 0.

**Tag vocabulary.**
- Tags are in uppercase and open-ended: 12,314 distinct tags across 7,928 articles, 77% seen only once.
- The same idea appears under several spellings: `AI` (1,479), `ARTIFICIAL-INTELLIGENCE` (170), `ARTIFICIAL INTELLIGENCE` (48), and a misspelt variant.
- Theme tags that do show up: `AI-INFRASTRUCTURE` 119, `DATA-CENTERS` 163, `QUANTUM-COMPUTING` 29, `NUCLEAR-ENERGY` 28, `STABLECOINS` 26, `HUMANOID-ROBOTS` 3, `GLP-1` 1.
- Share of AAPL articles with any tag: 0% in samples from 2023-06 to 2025-01, 4% in Apr 2025, 18% in Jun 2025, 89% in Oct 2025, and 88–100% in the last 30 days. So tag-based history only reaches back to about mid-2025.

**`symbols[]`.**
- Always sorted alphabetically and capped at 50 entries.
- Only 35% of entries are `.US` tickers; the rest are foreign cross-listings such as `1AAPL.MI` or `2RR.F`.
- When a list hits the cap, US tickers late in the alphabet get cut off: 2–3% of TSLA/MSFT results did not contain `TSLA.US` / `MSFT.US` at all.

**Word weights.** For AAPL over 30 days, `news_found` was 6,312 but `news_processed` was 300, so the word list is computed from a 300-article sample.

**ETF holdings.** `/fundamentals/VTI.US` with filter `ETF_Data::Holdings_Count` returned 3,468, and `Top_10_Holdings` came back keyed by ticker.

## Contradictions
**Resolved:**
- *Cost of a single-ticker news request:* 5. The api-limits page (Sept 2026), pricing page, 2021 blog and the docs example all agree. The MCP/skills `rate-limits.md` says 10, but its own summary table says 5.
- *Multi-ticker news:* not allowed (HTTP 422). The MCP docs show an example that does not work.
- *Is the tag list published?* The 53 standard tags are; the AI-detected vocabulary is not.
- *Max offset:* not documented, and no cap was observed up to ~74.6k.
- *ETF fundamentals cost:* 10 calls; bulk fundamentals excludes ETFs.

**Still open:**
- whether the general feed exists;
- cost of `t=` and of a no-`s`/no-`t` request;
- sentiments pricing: 5n, 5+5n, or flat per request;
- sentiments ticker cap (100?);
- whether failed requests are charged (the official page says 404s are; the MCP reference says HTTP errors cost 0);
- word-weights cost;
- the offset cap on the general feed.

## (c) Small caps and source list
- **Source list:** not published. The docs only mention "major sources" and the 2021 blog "dozens public sources". The de facto list from my samples:
  - Yahoo Finance at 57–97%;
  - Seeking Alpha, CNBC, Reuters (2023), Motley Fool and InvestorPlace (2024–25);
  - Investing.com, GlobeNewswire and Nasdaq.com in 2025.
- **Small-cap coverage:** no official statement found, and forums were not checked. Weak evidence: 3,230 distinct `.US` symbols appeared in the 7.9k sampled articles, including small caps such as KOPN, AMBA, LPCN and FRGT, plus junk symbols like `SOL/USD.US` and `A030200.US`. Per-ticker depth for Russell 2000 names is **unknown**. The `--coverage-file` option in the script below measures it in one request.

## (d) Terms and licence
- **ToS** (https://eodhd.com/financial-apis/terms-conditions, modified 2026-09-04), section "Personal and Commercial Use of Information":
  - A non-professional user may store, manipulate and analyse the data for private, non-commercial purposes (paraphrase).
  - Such a user must not share account access, and must not engage in "Selling, reselling, retransmitting, redistributing, displaying, or granting access to the Information", which the ToS extends to repackaged forms.
  - Anyone using the data for a business or on behalf of others is a professional (commercial) user.
  - Under "Access to the Services", one API key allows 100,000 requests per day.
- **Licence page** (https://eodhd.com/financial-apis/commercial-vs-personal-license-use): the plans on the pricing page are for personal use only.
- **Commercial pricing** (https://eodhd.com/commercial-pricing):
  - Internal use is $399/mo. Its FAQ says data may only be used inside the company, and displaying or sharing it outside is not allowed.
  - Enterprise is $2,499/mo; Custom starts at $399.
  - Letting users or clients partially download data needs the Custom plan.
- **What this means:** storing full article text and derived scores for yourself is allowed. Showing news, headlines or derived scores to anyone else on the self-hosted site is not allowed on a personal plan; that needs a commercial or Custom deal, so ask sales@.
- **Not covered by the ToS:** how long data may be kept, especially after cancelling, and the third-party copyright in article bodies (mostly Yahoo). Treat both as unknown and keep article text private.

## (e) API-call budget
The plan is 100k/day with about 15k already used, so about 85k/day is spare. "5 / 10" means the official per-request cost / the MCP formula.

| Strategy | Daily calls | 1-year backfill | 3-year backfill | Notes |
|---|---|---|---|---|
| A: per-ticker news, 3,100 tickers × 1 page | 15,500 / 31,000 | ~3.8k requests → 19k / 38k | ~6.8k requests → 34k / 68k | Assumes ~10 mega caps need 15–45 pages; heavy duplication (median 8–11 symbols per article) |
| A, tiered: top 1,000 daily + the rest weekly (7-day window, same price) | ~6.5k / 13k | same as A | same as A | Cost depends on how often you poll, not how far back you go |
| B: general feed, `limit=1000` per page | 10–30 (assumes 2–6k articles/day market-wide; low confidence; the 1.5k burst day is a lower bound) | 3.7k–11k | 6k–20k | Only if the feed exists. Raw text ~4–11 GB/yr |
| C: `t=` for 50–200 tags (×2–3 spelling variants) | 250–1,000 (500–3,000 with variants) | 5k–25k | ≈ same as 1 year (no tags before mid-2025) | Needs your own alias table |
| D: sentiments for 3,100 tickers (31 requests) + news for top 50–100 | 15.5k–15.7k + 250–1,000 ≈ 15.8k–16.7k (only 0.4k–1.2k if sentiments are flat per request) | ~15.5k (one batch; history length should not matter, inferred) | 15.5k–46.5k (if you split by year) | Has the same counts as A but no article text |
| E: weekly `ETF_Data::Holdings`, 60 ETFs | 600/week ≈ 86/day | n/a (snapshot only; no historical holdings found) | n/a | Bulk fundamentals cannot be used for ETFs |

## Recommended ingestion strategy
1. **Today:** pause the worker and run the script below. It costs about 600–1,200 calls.
2. **If the general feed works:**
   - Use B as the corpus: ~30 calls/day plus a backfill of ≤20k calls, starting 2021-03.
   - Build per-ticker attention locally by counting `symbols[]` per day. Correct for the 50-entry cap: for capped articles, also match the ticker or name in the title or body.
   - Detect themes with your own taxonomy across titles, bodies and EODHD tags, merging tag spelling variants. Use DeepSeek to cluster new tags and phrases into emerging themes.
3. **If it does not work:**
   - Use tiered A (~6.5k/day) and C (~1–3k/day) as the corpus.
   - Use D only if sentiments are flat-priced, or once to backfill attention history.
   - If news turns out to cost 10 per request and sentiments 5 per ticker, switch the daily attention series to D.
4. **In all cases:**
   - add E (~86/day);
   - check spare quota with `/api/user` before each batch (it costs 0);
   - de-duplicate articles by link, then by near-duplicate title;
   - score attention with robust z-scores against a rolling baseline, normalised by the market-wide daily total, with the Nov–Dec 2024 and 2025-08-12..21 windows masked;
   - keep the site owner-only.
5. **Expected total:** about 23–33k/day including existing usage.

## Only the owner's key can confirm
- Whether the general feed exists, how deep it goes, its offset cap and its daily volume.
- Exact costs: `s=`, `limit=1000`, `t=`, the 422 and 404 cases, sentiments with 1/10/50/100/101 tickers and whether the date range changes the price, word weights, and `URA.US` fundamentals.
- How `t=` matching behaves without `s`.
- How many Russell 2000 names get zero articles.
- The plan name and `dailyRateLimit`.
- Whether display or retention rights are available (an email to sales@, not the key).

## (f) Cost-measurement script (run with your own key)
It reads the key from `EODHD_API_KEY`, never prints it, and strips it from any error text. I tested it with `demo`: it ran end to end and correctly flagged every reading as noisy because of other demo users.
```python
#!/usr/bin/env python3
"""Measure real API-call cost of EODHD news/sentiment/word-weights/ETF endpoints.
Usage: EODHD_API_KEY=... python3 eodhd_cost_probe.py [--sentiment-max] [--coverage-file tickers.txt]
Pause the QuantDashboard worker first (the counter is account-wide). Budget ~600-1,200 calls; /api/user costs 0."""
import argparse, datetime as dt, json, os, sys, time, urllib.error, urllib.parse, urllib.request

KEY = os.environ.get("EODHD_API_KEY", "")
if not KEY:
    sys.exit("Set EODHD_API_KEY in the environment.")
BASE = "https://eodhd.com/api/"
PAUSE = 1.5

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
        if "data" in data and isinstance(data["data"], dict):
            return f"{len(data['data'])} words, meta={data.get('meta')}"
        if "ETF_Data" in data or "General" in data:
            h = (data.get("ETF_Data") or {}).get("Holdings") or {}
            return f"sections={list(data)[:6]}, holdings={len(h)}"
        if data and all(isinstance(v, list) for v in data.values()):
            return f"{len(data)} tickers, {sum(len(v) for v in data.values())} day-rows"
        if len(data) and all(isinstance(v, dict) for v in data.values()):
            return f"{len(data)} keys"
    return redact(str(data)[:120])

RESULTS = []

def measure(label, path, **params):
    before, _, _ = usage()
    st, data = call(path, **params)
    time.sleep(1.0)
    after, _, _ = usage()
    time.sleep(2.0)
    drift, _, _ = usage()
    noisy = drift != after
    RESULTS.append({"test": label, "path": path, "params": params, "http": st,
                    "cost": after - before, "noisy": noisy, "rows": rows(data)})
    print(f"{label:<44} HTTP {st:<4} cost={after-before:<6}{' (NOISY)' if noisy else '':<9} rows={rows(data)}", flush=True)
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
        print("Counter date is not today (UTC); running a 1-call warm-up to reset it.")
        call("exchanges-list")
    a1, _, _ = usage(); time.sleep(3); a2, _, _ = usage()
    if a1 != a2:
        print("WARNING: counter moved with no requests from this script. Stop other jobs.")

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
        total, off = 0, 0
        for _ in range(20):
            st, page = call("news", limit=1000, offset=off, **{"from": y, "to": y})
            n = len(page) if isinstance(page, list) else 0
            total += n
            if n < 1000:
                break
            off += 1000
        print(f"general feed articles on {y}: {total}{' (>=20 pages, truncated)' if total >= 20000 else ''}")
    measure("news s=INVALIDXX.US (404 charged?)", "news", s="INVALIDXX.US", limit=1)

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

    measure("word-weights s=AAPL.US 30d", "news-word-weights", s="AAPL.US",
            **{"filter[date_from]": m30, "filter[date_to]": y, "page[limit]": 50})
    measure("fundamentals URA.US (full)", "fundamentals/URA.US")
    measure("fundamentals URA.US filter=ETF_Data::Holdings", "fundamentals/URA.US", filter="ETF_Data::Holdings")

    if args.coverage_file:
        ticks = [l.strip() for l in open(args.coverage_file) if l.strip()][:100]
        ticks = [t if "." in t else t + ".US" for t in ticks]
        st, d = measure(f"coverage sentiments {len(ticks)} tickers", "sentiments", s=",".join(ticks), **{"from": m30, "to": y})
        if isinstance(d, dict):
            counts = sorted(sum(r.get("count", 0) for r in d.get(t.upper(), d.get(t, []))) for t in ticks)
            print(f"  30-day article counts: zero={sum(c == 0 for c in counts)}/{len(counts)}, "
                  f"median={counts[len(counts)//2]}, max={counts[-1]}")

    out = f"eodhd_cost_probe_{today.isoformat()}.json"
    json.dump(RESULTS, open(out, "w"), indent=1)
    print(f"saved {out} (contains no key)")

if __name__ == "__main__":
    main()
```

The script is checked in as [`tools/eodhd_cost_probe.py`](tools/eodhd_cost_probe.py). The raw demo-key probe samples were not checked in.


## (g) Owner-key results, 2026-10-10 (run after this appendix was written)

The probe was run with the owner's key on Saturday 2026-10-10 around 13:30 ET, when the production worker has no scheduled EODHD jobs. The drift check reported every reading as clean (not noisy). `dailyRateLimit` = 100,000; 2,003 calls had been used that day before the run.

| Test | HTTP | Cost |
|---|---|---|
| news `s=AAPL.US`, limit 50 / 1000 | 200 / 200 | 5 / 5 |
| news two tickers | 422 | 0 |
| news `t=artificial-intelligence` / `t=artificial intelligence` / `t=AI` / `t=earnings report` | 200 | 5 each (17 / 0 / 181 / 66 rows) |
| **news with no `s` and no `t`**, limit 50 / 1000 | **200** | **5 / 5** |
| general feed, all of 2026-10-09 (paged) | — | **1,897 articles** |
| news `s=INVALIDXX.US` | 200 | 5 (empty, still charged) |
| max offset: `s=AAPL.US` / general feed | — | 70,000 / ≥200,000 |
| sentiments 1 / 10 / 50 / 100 tickers (30 days) | 200 | 5 / 50 / 245 / 500 |
| sentiments 10 tickers (1 day) | 200 | 50 |
| sentiments 101 tickers | 422 ("Too many symbols. Max 100") | 0 |
| word weights AAPL 30 d | 200 | 5 (300 of 6,314 processed) |
| fundamentals URA.US, full / `filter=ETF_Data::Holdings` | 200 | 10 / 10 (48 holdings) |
| coverage: sentiments for 40 small/mid caps, 30 d | 200 | 200 (zero articles: 2/40; median 12; max 158) |

**Resolved:**
- The general feed exists, and a 1,000-row page costs 5 calls.
- Sentiments cost 5 per ticker, regardless of the date range, with at most 100 tickers per request.
- Requests for a non-existent ticker are charged.

**Decision:** use strategy B. Pull the general feed daily (about 10 calls), count per-ticker articles locally from `symbols[]`, and backfill baselines from the same feed (about 3,700 calls for 2025-09-01 onward). Do not use `/api/sentiments`.
