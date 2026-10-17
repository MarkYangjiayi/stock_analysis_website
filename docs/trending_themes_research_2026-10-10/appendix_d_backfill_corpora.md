# Backfill corpora and legal access outside EODHD: Alpaca/Benzinga news, SEC 8-K press releases, wire RSS, Wikimedia dumps, X counts residuals

Yahoo RSS keeps no history, and EODHD news is roughly 90% Yahoo-syndicated, with sparse history from about 2016. Backtesting a news-based theme detector needs an independent historical corpus. Alpaca's free Benzinga archive (2015 onward) is the best candidate found, but eligibility for a non-US individual and the storage terms are unverified. SEC 8-K EX-99 press releases are a free, legal corpus whose size has not been estimated. The remaining X questions decide whether X counts are usable for theme queries: does the cashtag operator work on counts, and are Basic/Pro plans and non-US card payment available?

# Historical corpus backfill: Alpaca/Benzinga, SEC 8-K EX-99, wire feeds, Wikimedia, X counts (checked 2026-10-10)

**Method.** The WebSearch quota was already used up at the start of this run (the 200-call limit is shared across agents). Everything below comes from fetching known primary URLs directly: WebFetch, curl, the docs' `.md` and `llms.txt` exports, and the desktop browser pane for pages behind Akamai. I made about 120 SEC requests at 3.3 req/s or less, and a handful of Wikimedia requests. All of them used the User-Agent `QuantDashboard personal research quantdashboard-research@example.org`, which is a placeholder: I did not send the owner's email. No accounts or credentials were used, and no repository files were changed.

**Quoting.** Terms of service are copyrighted, so I paraphrase clauses and name the section and URL. There is one short verbatim quote, in the Alpaca row. Open the URL to read the exact wording.

## 1. Alpaca News API (Benzinga)

**Endpoint and depth.**
- Endpoint is `GET https://data.alpaca.markets/v1beta1/news`. `limit` is 1 to 50 (default 10). It takes `start`, `end`, `sort`, `symbols`, `include_content`, `exclude_contentless` and `page_token`, and returns `X-RateLimit-*` headers and 429 when limited ([reference](https://docs.alpaca.markets/us/reference/news-3.md)).
- History "dating back to 2015", an "average of 130+ news articles per day", all from Benzinga ([historical-news-data.md](https://docs.alpaca.markets/docs/historical-news-data.md), updated 2025-09-24).

**Is news in the free Basic plan?** Not stated today (unverified).
- The plan tables list Basic (free, 200 historical calls/min) and Algo Trader Plus ($99/mo, 10,000/min). They cover equities and options only and never mention news ([about-market-data-api.md](https://docs.alpaca.markets/docs/about-market-data-api.md), updated 2026-10-06).
- The rate-limit FAQ (Dec 2022) says 200 req/min per account, returns 429 above that, and offers no self-serve increase ([FAQ](https://alpaca.markets/support/usage-limit-api-calls)).
- Alpaca offers 1,000/min only if the account is converted to non-retail, which adds per-share routing fees ([FAQ](https://alpaca.markets/support/increase-api-rate-limit)).

**Eligibility for a non-US individual.** Contradictory.
- The paper-trading doc says anyone globally can open a Paper Only Account with just an email. Paper-only holders are entitled only to IEX market data ([paper-trading](https://docs.alpaca.markets/docs/paper-trading)).
- The country article (Feb 2026) gives no list and says to contact support ([countries](https://alpaca.markets/support/countries-alpaca-is-available)).
- An Oct 2025 FAQ says non-US users can fund live accounts after KYC ([outside US](https://alpaca.markets/support/is-alpaca-available-outside-the-us)).
- The Terms & Conditions have a "U.S. Residents Only" section saying the Content and Service are intended for US residents ([T&C PDF](https://files.alpaca.markets/disclosures/library/TermsAndConditions.pdf)). The PDF's metadata is dated 2021-08-31 and it still names "Alpaca Securities LLC", while the site footer now says "Alpaca Clearing LLC".
- No list of excluded countries is published anywhere.

**Storage and display** (same T&C PDF).
- The "Personal and Non-Commercial Usage" section limits use to personal, non-commercial purposes. It requires 30 days' written notice before making Content available to others through your own application.
- The "Content" section says: "Content is provided exclusively for personal and noncommercial access and use." It also bans copying, republishing, publicly displaying, or mirroring Content to another server or website for publication or distribution without written consent.
- A support FAQ (Nov 2022) says plainly that you cannot redistribute Alpaca API data ([FAQ](https://alpaca.markets/support/redistribute-alpaca-api)).
- Reading:
  - Storing for private use is arguably allowed.
  - Showing headlines on a site other people can reach is not.
  - Derived signals such as counts or scores are not addressed.

**Small-cap coverage.** No statement anywhere. Needs a test.

**Datacenter/VPN egress.** Nothing documented.

**Backfill estimate.** About 4,300 days × 130 ≈ 560k articles, or about 11.2k requests at 50 per page. That is about 1 hour at 200/min. Headlines and summaries come to about 0.3 GB; with full content about 1.5–2 GB. Both sizes are estimates.

## 2. SEC 8-K EX-99 press releases (measured)

**Policy.**
- Limit is 10 req/s "regardless of the number of machines", and the SEC reserves the right to block IPs that send excessive requests ([developer resources](https://www.sec.gov/about/developer-resources), 2025-03-10).
- The User-Agent must look like `Sample Company Name AdminContact@<domain>.com`. Unclassified bots and botnets are managed out ([accessing EDGAR data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data), 2024-06-26; [webmaster FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions)).
- No statement on VPN or datacenter IPs. My cloud-ASN egress got 0 errors across about 115 requests at 3.3 req/s or less.
- The submissions API needs no key and has no CORS. It holds the latest year or 1,000 filings, with older filings split into extra files. `submissions.zip` is rebuilt nightly and is 1.57 GB today ([EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces); size from a HEAD request).
- The submissions JSON includes an `items` field for each 8-K, e.g. `2.02,7.01,9.01`.

**Sample days** ([daily-index](https://www.sec.gov/Archives/edgar/daily-index/2026/QTR4/)):

| Day | All filings | 8-K + 8-K/A | 8-K by currently listed CIKs (Nasdaq/NYSE/CBOE/TXSE, via [company_tickers_exchange.json](https://www.sec.gov/files/company_tickers_exchange.json)) | Sampled 8-Ks with EX-99 |
|---|---|---|---|---|
| 2026-10-07 (quiet) | 3,159 | 141 | 109 | 19/40 (48%) |
| 2026-07-30 (peak earnings) | 5,667 | 466 | 386 | 35/40 (88%) |

**Exhibit size.**
- 1.2–1.4 EX-99 documents per filing.
- Raw HTML median: 22 KB on the quiet day, 223 KB on the earnings day.
- Plain text from 18 sampled exhibits: median 10.6 KB, mean 18.7 KB. Gzip shrinks it to about a third. HTML is about 10× the size of the text.

**Long-run volume** (quarterly `master.gz` files, about 3 MB each):
- Total 8-Ks per quarter: 17,046 (2016Q3), 16,449 (2019Q1), 19,320 (2022Q2), 16,149 (2025Q3). That is about 65–77k per year across all filers.
- 2025Q3 by currently listed CIKs: 11,911, about 190 per trading day.
- Russell 3000 estimate (about 60–65% of listed filers): roughly 110–125 per day on average, about 70 on quiet days and about 250 at the earnings peak.

**2016–2026 backfill for a Russell 3000 universe including delisted names** (estimate):

| Item | Estimate |
|---|---|
| 8-Ks | about 300–340k |
| 8-Ks with EX-99 | about 200–220k |
| EX-99 documents | about 260–300k |
| Stored plain text | about 5 GB raw, about 1.7 GB compressed |
| Raw HTML transfer | about 50 GB (much less with gzip transfer encoding) |
| Requests | 43 `master.gz` files (about 150 MB) or about 2,700 daily `form.idx`, plus about 320k filing-index pages, plus about 280k exhibits ≈ 600k |
| Time | about 33 h at 5 req/s, about 19 h at 9 req/s |

**Cheaper enumeration.**
- Pull `items` from the submissions JSON (or the zip). Skip any 8-K without Item 9.01, since 9.01 means it has exhibits.
- Then fetch only `{acc}-index.htm`, which lists each document's type, and the EX-99 files themselves.
- Avoid the full submission `.txt`. An earnings 8-K can be 6.2 MB because of embedded images and XBRL.
- The `Feed/` daily tar files run 0.7–1.4 GB per day. Too big for this server.

**edgartools** ([repo](https://github.com/dgunning/edgartools): 2,781 stars, MIT, pushed 2026-10-08, not archived):
- `EightK.items`, `has_press_release` and `press_releases` find EX-99, EX-99.1, EX-99.01, or exhibits described as "RELEASE" ([docs/eightk-filings.md](https://github.com/dgunning/edgartools/blob/main/docs/eightk-filings.md)).
- It defaults to 9 req/s. Set `EDGAR_RATE_LIMIT_PER_SEC=5` and `EDGAR_IDENTITY` ([docs/configuration.md](https://github.com/dgunning/edgartools/blob/main/docs/configuration.md)).

**Coverage caveat.** 8-K exhibits cover material events: earnings, M&A, guidance, financings, executive changes, and voluntary 7.01/8.01 releases. Many product and partnership releases, which are the theme-relevant ones, never reach an 8-K.

## 3. Press-release wires

**RSS gives no history for any of them.** How deep the website archives go was not verified.

**GlobeNewswire**
- 332 RSS feeds: 80 subject codes, 176 industries, 14 countries, 61 states/provinces, and one "Public Companies" feed. Each says "Contains the last 20 releases". Items carry stock ticker and ISIN categories ([rss/list](https://www.globenewswire.com/rss/list), read in the browser pane).
- `robots.txt` disallows `/api/`, `/search`, `/JSWidgetFeed/` and `/newsroom/rss/`, and allows `/news-release/` ([robots.txt](https://www.globenewswire.com/robots.txt)).
- I could not find reader terms of use. The footer "Legal" link goes to Notified's privacy statement ([notified.com/privacy](https://www.notified.com/privacy), effective 2025-10-06). The site footer reads "© 2026 Digital Media Innovations, LLC". **Unverified.**

**PR Newswire**
- 15 industry RSS feeds of 20 items each. The Financial Services feed covered about 20 hours ([rss](https://www.prnewswire.com/rss/)).
- Items carry industry and subject tags and a lead of about 300 characters, but no tickers. The old `news-releases-list.rss` returns 404.
- Terms of Use, last updated 2023-09-01 ([TOU](https://www.prnewswire.com/terms-of-use/)):
  - Personal, non-commercial use and a single copy only.
  - No robot, spider, or automated process to download, index, or data-mine content.
  - No redistribution, including "database storage and retrieval", to third parties.
  - No using content to train ML or AI systems.

**Business Wire**
- `businesswire.com` returned 403 to curl, WebFetch, and the browser pane alike.
- Legacy `feed.businesswire.com` channels either say the channel was "deactivated by the administrator", return errors, or return 0 items.
- Terms could not be read. **Unverified.**

**ACCESS Newswire** (accesswire.com now redirects to accessnewswire.com)
- The newsroom is HTML only, browsable by 29 industries.
- The only RSS feed found is the marketing blog (`/feed/rss2`).
- `robots.txt` disallows `/rss`, `/rss2` and `/api`, with `Crawl-delay: 5`.
- Its [Terms of Service](https://www.accessnewswire.com/terms-of-service) (updated 2026-08-26) are an issuer contract with no reader-use clauses.

## 4. Wikimedia

**Rate limits** ([Rate limits](https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits), new in 2026):
- Unidentified clients: 10/min. Compliant User-Agent: 200/min. Established editors: 2,000/min. Bot-flagged accounts and Wikimedia Cloud Services: exempt.
- Keep concurrency at 3 or less and honour `Retry-After`.
- The [robot policy](https://wikitech.wikimedia.org/wiki/Robot_policy) (last modified 2026-09-26) adds: unauthenticated REST calls under 5 req/s; prefer dumps; for high volume, publish a list of the IP ranges (CIDRs) you send from.

**Per-article REST API (verified).**
- One request returns the whole daily series from 2015-07-01. Nvidia came back with 4,119 points in 589 KB of JSON; Small_modular_reactor was 22 KB gzipped.
- 3,500 articles = 3,500 requests ≈ 18 min, about 80 MB transferred, about 14.4M rows.
- Discovery: the `top` endpoint returns the 1,000 most-viewed articles per day (16 KB). Backfilling 2015–2026 takes about 4,100 requests.
- Views are counted under the exact title requested (medium confidence). Sum redirect titles separately, and resolve renames by page ID.

**pageview_complete dumps** ([readme](https://dumps.wikimedia.org/other/pageview_complete/readme.html), CC0):
- Coverage from Dec 2007; page IDs from 2015.
- Files are bz2. Daily `user` files: 0.74–0.86 GB (Sep 2026), 0.44–0.46 GB (Sep 2016).
- Monthly `user` file: 5.02 GB (2026-08), 2.46 GB (2016-09). Monthly `automated` file: 6.3 GB.
- Monthly files do hold per-article daily counts, encoded as letter plus count (e.g. `A19B20C11…`). Rows are split by access method, so they must be summed. Verified on the first 3 MB.
- Decompression expands about 6.6×. Measured speed was about 16 MB/s of compressed input per core.
- 2016–2026 totals: about 490 GB to download and about 3.2 TB to scan, roughly 9 core-hours of decompression.
- Downloads are capped at 3 connections per IP and the User-Agent policy is enforced ([dumps](https://dumps.wikimedia.org/)).

**Verdict.** Use the API for the curated article list. Dumps are only worth it for full-wiki discovery or pre-2015 history.

## 5. X API (docs.x.com)

**Cashtag operator on counts endpoints: contradictory, unverified.**
- [Search Operators](https://docs.x.com/x-api/posts/search/integrate/operators.md) lists `$` and `has:cashtags` with no tier label.
- [Counts build-a-query](https://docs.x.com/x-api/posts/counts/integrate/build-a-query.md) says Advanced operators need Enterprise.
- The [counts migration table](https://docs.x.com/x-api/posts/counts/migrate/overview.md) marks both as Advanced (legacy "Academic Research" wording).
- The [search migration table](https://docs.x.com/x-api/posts/search/migrate/overview.md) marks both as Available for v2.
- Leaning: probably not available on pay-per-use counts. A single test request costs $0.005 and would settle it.

**Endpoints** ([counts intro](https://docs.x.com/x-api/posts/counts/introduction.md), [rate limits](https://docs.x.com/x-api/fundamentals/rate-limits.md)):
- `counts/recent` is open to all developers. `counts/all` is pay-per-use and Enterprise only.
- 31 days per page at day granularity. Each endpoint is limited to 300 requests per 15 minutes.
- Query length for self-serve: 512 characters (recent) and 1,024 (all).
- On 2026-05-04, counts moved to a new search index, and retweets no longer appear in keyword search results ([changelog](https://docs.x.com/changelog.md)).

**Pricing** ([pricing](https://docs.x.com/x-api/getting-started/pricing.md)):
- Counts: Recent $0.005 per request. Counts: All $0.010 per request.
- New accounts get $20 free when they save their first card and up to $50 matched on the first auto-recharge (rolling out since 2026-10-02). Prepaid cards don't qualify for the $20 ([free credits](https://docs.x.com/x-api/getting-started/free-credits.md)).
- Backfilling one query over 2016–2026 is about 127 pages ≈ $1.27 and about 6.4 minutes.
  - 50 theme queries ≈ $64.
  - 3,000 tickers ≈ $3.8k.

**Basic and Pro.**
- The pricing page now says pay-per-use with no subscriptions.
- The 2026-02-06 changelog said Basic and Pro "remain available" and that existing subscribers can opt in to pay-per-use.
- The [Developer Agreement](https://docs.x.com/developer-terms/agreement) (2026-04-27), §L, still names Pay-Per-Use, Basic and Pro.
- No documented way for a new developer to buy Basic or Pro. Probably not purchasable (low confidence).

**Payment and countries.**
- Agreement §C: payment options vary by geography. §G: you must live in a country X supports.
- No list of supported countries is published.
- Export-control section: US embargoed countries (Cuba, Iran, North Korea, Northern Sudan, Syria) and OFAC-listed parties are excluded.
- Whether non-US cards work is undocumented.

**Storing and display.**
- "X Content" is defined to include any data delivered through the API and its derivatives, so counts count as X Content.
- The licence lets you copy "a reasonable amount" and display it on your own services.
- The [Developer Policy](https://docs.x.com/developer-terms/policy) adds:
  - Stored content must be kept in sync with deletions.
  - Redistribution to third parties is limited to Post, DM and User IDs, so no downloadable counts dataset.
  - The "X performance benchmarking" section bans computing aggregate X post metrics for benchmarking, competitive, or commercial purposes. Personal per-theme counts are a gray area, low risk.
- §L: self-serve plans are meant for hobbyists and apps with limited end users.

**VPN/datacenter.** Nothing in the docs.

## 6. Datacenter/VPN egress (relevant to the WARP proxy)

- **SEC:** blocks by IP for excessive request rates; no VPN rule. Through WARP's shared Cloudflare IPs, other users' traffic could get that IP blocked.
- **Wikimedia:** identity-based limits (10/min without a User-Agent); dumps capped at 3 connections per IP; clients that evade limits may be blocked.
- **Alpaca and X:** nothing documented. X's agreement §II.D bans circumventing rate limits.
- **GlobeNewswire:** Akamai returned 403 to curl and WebFetch but the browser pane got through, which points to bot fingerprinting rather than IP blocking.
- **Business Wire:** 403 on every path I tried.
- **PR Newswire and ACCESS Newswire:** returned 200 to curl.

## Comparison

| Source | History depth | Volume/day | Backfill size | Non-US individual | Cost | Personal storage / display (clause, URL) | Rate limits | Confidence |
|---|---|---|---|---|---|---|---|---|
| Alpaca News (Benzinga) | 2015– | 130+ (documented) | ≈560k articles, ≈11k requests, 0.3–2 GB | Paper-only: "anyone globally" ([doc](https://docs.alpaca.markets/docs/paper-trading)); T&C "U.S. Residents Only" ([PDF](https://files.alpaca.markets/disclosures/library/TermsAndConditions.pdf)) | Free if news is in Basic (unverified) | Personal, non-commercial only; no public display or mirroring; no redistribution ([FAQ](https://alpaca.markets/support/redistribute-alpaca-api)) | 200/min | Medium on specs; low on eligibility and plan |
| SEC 8-K EX-99 | 2001– (backfill from 2016) | ≈110–125 R3000 8-Ks, ≈65% with EX-99 | ≈280k docs, ≈5 GB text (≈1.7 GB compressed), ≈600k requests | Open to anyone | Free | US government public records; no licence restriction found ([policy](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)) | 10 req/s (use 5) | High on volume; medium on R3000 share |
| GlobeNewswire RSS | Last 20 items | n/a | None (live only) | Open | Free | Terms not found | robots.txt; Akamai | Low |
| PR Newswire RSS | Last 20 items | n/a | None | Open | Free | No robots or data mining, no database redistribution, no AI training ([TOU](https://www.prnewswire.com/terms-of-use/)) | — | High on terms |
| Business Wire | — | — | — | — | — | Unreadable (403) | Blocked | Low |
| ACCESS Newswire | HTML only | n/a | — | Open | Free | No reader terms; robots.txt disallows `/rss` | Crawl-delay 5 | Medium |
| Wikimedia per-article API | 2015-07– | 1 point/article/day | 3,500 requests, ≈80 MB | Open | Free | CC0 analytics data | 200/min, concurrency ≤3 | High |
| Wikimedia dumps | 2007– | ≈0.8 GB/day bz2 | ≈490 GB (2016–2026) | Open | Free (bandwidth) | CC0 | 3 connections/IP | High |
| X Counts | 2006– | per query | $1.27/query | Country list unpublished | $0.005–0.010 per request | Display allowed, no dataset redistribution, benchmarking clause ([policy](https://docs.x.com/developer-terms/policy)) | 300/15 min | Low on `$` operator; medium otherwise |
| Massive News (prior finding) | 2 yrs on free tier | — | — | Unverified | Free, 5 calls/min | Not reviewed | 5/min | Low (pricing page doesn't list news on Basic) |

## Recommended backfill plan

1. **Backbone: SEC 8-K EX-99 text, 2016 to now.**
   - Use EODHD's historical index membership, including delisted names, as the universe.
   - Enumerate with `master.gz` or the submissions `items` field, filter on Item 9.01, then fetch the index page and EX-99 files at 5 req/s with gzip.
   - Store zstd-compressed plain text, about 2 GB. Expect about 1.5–2 days of runtime in the worker.
   - Keep it current from the daily `form.idx`.
2. **Attention signal: Wikimedia per-article API** for the 3,500 articles, plus the daily `top` endpoint for discovery. Skip dumps.
3. **Alpaca/Benzinga** only after the owner confirms eligibility. Store headline, summary, symbols, and timestamp; keep it private; never display it publicly.
4. **Wire RSS, forward-only.** Headline, ticker, and timestamp from GlobeNewswire (Public Companies, Earnings, and industry feeds) and PR Newswire industry feeds. No archive scraping, no AI training on PR Newswire content.
5. **X counts optional.**
   - Spend $5–10 first to test the `$` operator and card payment.
   - Use keyword theme queries, not cashtags.
   - Budget about $1.27 per query to backfill, and keep the counts private.

## Items only the owner can confirm

1. Country of residence. Whether Alpaca support confirms a Paper Only account there, and whether the "U.S. Residents Only" T&C clause is acceptable.
2. Whether a Basic key actually returns `/v1beta1/news` data, and how well it covers small caps. One test call per check.
3. Whether the site is private (login-gated, single user) or public. This decides display legality for Alpaca, PR Newswire, and X.
4. A real contact email for the SEC and Wikimedia User-Agent strings.
5. Whether SEC, Wikimedia, X, and GlobeNewswire accept the server's WARP egress (needs a test from the server).
6. Whether X's console accepts their card and country, and whether the `$` operator works on counts.
7. Disk budget, roughly 3–5 GB for the recommended plan.
8. Whether sending licensed Benzinga or PR Newswire text to DeepSeek counts as redistribution or AI use under those terms.

The raw evidence (index files, samples, terms text) was research scratch data and was not checked in.
