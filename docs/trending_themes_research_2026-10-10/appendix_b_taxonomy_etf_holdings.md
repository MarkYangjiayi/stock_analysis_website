# Theme taxonomy seeds and point-in-time membership sources: Finviz Elite API, thematic-ETF holdings history (issuer CSVs, N-PORT, EODHD) and licence terms

Mapping themes to companies is the hardest part of the build, and every method needs a seed taxonomy. Those methods are embedding recall plus DeepSeek verification, BERTopic zero-shot, and theme baskets for breadth and RRG. The two cheapest seeds have not been confirmed. Finviz's 40 themes and 268 sub-themes come with a new official API (blog dated 2026-10-07), but its export format and terms are unknown. Thematic-ETF holdings, from EODHD, issuer files or SEC N-PORT, have unverified coverage and history. Without point-in-time membership history, any backtest of theme heat carries look-ahead bias.

# Theme taxonomy seeds and point-in-time membership sources: Finviz, thematic-ETF holdings, N-PORT and EODHD

All facts below were fetched on 2026-10-10. WebSearch was not available: the shared per-turn search budget was already used up. Everything here comes from direct fetches of primary pages, files, the SEC and Federal Register APIs, and `gh api`. SEC requests used a placeholder contact in the User-Agent; I did not send the owner's email address. I made no logins, used no credentials, and changed no repository files.

## Bottom line

1. **Finviz gives the best seed taxonomy, but it has no history.**
   - **Taxonomy:** 40 themes and 268 sub-themes, verified from the screener HTML.
   - **No theme columns:** the 130-column list served to anonymous users has no Theme or Sub-theme column, so you need one export per filter.
   - **No history:** the documented export endpoints include nothing for past theme membership.
   - **Terms of Use:** I could not find a public ToU page.
2. **The only free point-in-time history for backtests is SEC N-PORT.** It covers every thematic ETF from late 2019 onward. Public snapshots are quarterly (the quarter-end month), filed up to 60 days after quarter end.
3. **Four issuers publish dated daily files you can backfill:**

   | Issuer | Daily files since |
   |---|---|
   | Roundhill | about 2023-12 |
   | KraneShares | about 2024-01 |
   | Global X | 2024-08-06 |
   | Tema | 2026-08-18 |

   Every other issuer serves current holdings only, so you would have to start archiving them yourself now.
4. **EODHD `ETF_Data.Holdings` is current-only.** Historical snapshots are documented for indices only. The holdings carry no as-of date, and coverage of small thematic ETFs is unverified.

---

## 1. Finviz

**Taxonomy (verified).** The screener `<select data-filter="theme">` has 42 options: 40 themes plus "Any" and "Custom (Elite only)". The `subtheme` select has 270 options: 268 sub-themes plus the same two. Source: https://finviz.com/screener?v=111.
- **Slug correction:** `aicompute`, `aienergy` and the other examples are **sub-theme** slugs and need `f=subtheme_<slug>`. Theme slugs are names like `artificialintelligence`, `semiconductors` or `quantumcomputing`.
- **Silent failure:** `f=theme_aicompute` is invalid. Finviz ignored it without an error and returned all 11,725 tickers, so a scraper needs a guard against this.
- **Sample result:** `subtheme_quantumhardware` returned 7 names: GOOGL, HON, IBM, IONQ, QBTS, QUBT and RGTI (https://finviz.com/screener?v=151&f=subtheme_quantumhardware).

**Endpoint and authentication.** The docs page https://finviz.com/api-and-exports redirects (302) to `/elite` for anonymous users, so the API docs need an Elite login. The details below come from the publicly served docs JavaScript bundle (https://finviz.com/assets/dist/4927.v1.e257f03c.js), not from the rendered page:
- **Base URL:** `https://elite.finviz.com`.
- **Screener export:** `/export/screener?[filters]&auth=<token>`. Example from the docs: `/export/screener?v=152&c=0,1,2,3&f=fa_div_pos,sec_technology`. Columns are chosen with `&c=`.
- **Legacy URLs:** `.ashx` URLs return a 301 redirect. curl returns an empty body unless it follows redirects.
- **Token:** you generate it under Account > API and Exports.
- **Other exports:** portfolio, stock (OHLCV), groups, options, latest-filings, news (up to 100 rows), insiders (up to 200 rows, last 7 days), managers and `/managers/[slug]/holdings` (13F), funds and `/funds/[slug]/holdings`, calendars, and futures/forex/crypto.

**Theme columns.** The anonymous column map (`route-init-data`, 130 columns) has no Theme or Sub-theme column; the only tag-like field is the ETF column `e.Tags`. Membership therefore needs one export per filter: 308 requests, or 268 if you only pull sub-themes. The Elite column list comes from the server and was not visible to me (unverified).

**Rate limits** (blog post dated 2026-10-07, https://finviz.com/blog/our-upgraded-api-higher-limits-clearer-data-and-more-ways-to-connect/, and the docs bundle `{requests:10,seconds:5}`):
- 10 requests per rolling 5-second window, shared across every export/API endpoint on the account.
- Going over returns HTTP 429 with `Retry-After: 5`, and rejected requests do not count.
- A full sweep of 308 filters takes about 2.6 minutes.

**Price** (https://finviz.com/elite): $39.50/month or $299.50/year ($24.96/month). The free tier is listed as having no export, and the page advertises export "to setup your own Automated Workflow / AI Agents".

**Membership history.** None of the documented exports offers dated theme membership, and the filters take no date parameter. You can only build history by taking your own periodic snapshots.

**Terms of Use (not verified).** The footer has no Terms link. `/terms`, `/terms-of-service`, `/terms_of_service`, `/terms-of-use`, `/tos` and `/help/terms` all return 404. The help center at `/knowledge-base` renders with JavaScript. I could not confirm the clauses on automated access, storing data over time, or displaying derived data on a personal site. Check them from the account area before you rely on Finviz.

**Footer link left untested.** Every page carries a hidden `rel="nofollow"` footer link, "Export All Screener Data (CSV)", pointing to `/api/v1/screener-export-csv`. It is undocumented and may be a scraper trap. I did not call it, and I recommend not using it.

## 2. Issuer holdings files

| Issuer | URL pattern / format | Dated history (probed) | Notes |
|---|---|---|---|
| **Global X** | `assets.globalxetfs.com/funds/holdings/{t}_full-holdings_YYYYMMDD.csv` (title row, as-of row, then `% of Net Assets,Ticker,Name,SEDOL,...`) | Daily from **2024-08-06**. 2024-08-05, 2024-08-02, 2024-07-01 and 2024-06-03 return 404. Spot checks from 2024-09 to 2026-10 all returned 200 (BOTZ; URA checked for 2024-09-03). | CSV footer: "may not be reproduced, redistributed or used to create any derivative works." |
| **Roundhill** | One combined file for all Roundhill funds: `roundhillinvestments.com/assets/data/filepointroundhill.40ru.ru_holdings_MMDDYYYY.csv` (lowercase; mixed case redirects 301). Columns: `Date,Account,StockTicker,CUSIP,SecurityName,Shares,Price,MarketValue,Weightings,...` | From about **2023-12-01** (200). 2023-11-27 and earlier return 302 to not found. 2024-07-01, 2025-01-02, 2025-07-01 and 2026-10-09 all exist. | The `Date` column is the next trading day: the 10/09 file contains 10/12. DRAM, LYTE and NCLD hold total-return swaps whose underlying CUSIP is embedded in `StockTicker` (e.g. `595112103 TRS…` is Micron), so swaps must be mapped to their underlying. |
| **KraneShares** | `kraneshares.com/csv/MM_DD_YYYY_{t}_holdings.csv` | KOID from launch (2025-07-01 exists). KWEB: 2024-01-02 exists; 2023-12-01, 2023-10-02 and 2023-07-03 return 404. | No licence footer. |
| **Tema** | `temaetfs.com/hubfs/Website/Holdings/{T}-holdings-MMDDYYYY.csv` (plus `.xlsx`) | Only from **2026-08-18** (2026-08-17 returns 404). | Columns include `holdings_date` and `is_cash`. |
| **ARK** | `assets.ark-funds.com/fund-documents/funds-etf-csv/ARK_..._{T}_HOLDINGS.csv` | Current only; the file is overwritten. | The old ARKX file name still returns 200 but is frozen at 01/02/2026 (the fund was renamed Space & Defense Innovation), so always check the date column. Footer: "No part of this material may be reproduced…without written permission." |
| **VanEck** | `/us/en/investments/{slug}/downloads/holdings/` (xlsx) and `/Main/HoldingsBlock/GetDataset/?blockId=…&ticker=NLR` (JSON with `AsOfDate`) | Current only; no date control found. | |
| **iShares** | `ishares.com/us/products/239705/ishares-phlx-semiconductor-etf/latest-holdings.csv` works (text/plain CSV dated Oct 08, 2026). The legacy `.ajax?fileType=csv` URL still returns HTML. | `asOfDate` is ignored on the new endpoint, so current only. | Licence: "solely for your personal, non-commercial use… you may not copy, distribute, modify, post, frame or deep link". |
| **Defiance** | `defianceetfs.com/qtum-full-holdings/` (HTML table, 90 rows) and a WordPress admin-ajax POST download | Current only, plus quarterly holdings PDFs. | |
| **Themes ETFs** | `themesetfs.com/storage/holdings/Holdings-{T}.csv` | Current only, plus quarterly PDFs. | The `date` column holds the T+1 basket date (2026-10-12). |
| **Amplify** | Firestore-backed widget (`funds/{T}/holdings/{asOfId}`) | Not tested. The data model is keyed by date, but there is no static file. | |
| **Procure (UFO)** | Fund page plus quarterly schedule-of-investments files (2021–2024 links on the page) | Quarterly only. | |
| **Range (NUKZ), REX (DRNZ)** | HTML pages only | No file found. | |
| **First Trust** | Connection reset (curl and WebFetch) | **Not verified** | |
| **Sprott** | Cloudflare challenge, HTTP 403 | **Not verified** | |

## 3. SEC Form N-PORT

**What is public today.** The 2024 amendments are not yet in effect. Funds file quarterly, within 60 days of fiscal quarter end. Each filing contains all three month-end portfolios, but only the third month's holdings are made public; the first two months stay confidential. Source: SEC proposal IC-35962 (https://www.sec.gov/files/rules/proposed/2026/ic-35962.pdf; Federal Register 2026-03460).
- Because fiscal quarters differ by fund, NPORT-P filings appear every month across the industry. For example, Global X periods end in May, Aug, Nov and Feb.

**History of the 2024 amendments:**
- **Adopted** in IC-35308 (Federal Register 2024-19819, 2024-09-11). Monthly filing within 30 days, and every month made public 60 days after month end. Original effective dates: 2025-11-17 for larger fund groups and 2026-05-18 for smaller ones.
- **Delayed** in IC-35538 (issued 2025-04-16, Federal Register 2025-06861) to **2027-11-17** for larger groups and **2028-05-18** for smaller ones. https://www.sec.gov/rules-regulations/2025/04/s7-26-22
- **Proposal of 2026-02-18** (IC-35962, file S7-2026-05, comments closed 2026-04-24) would keep monthly filing with a 45-day deadline but **restore public disclosure of the quarter-end month only**, 60 days after month end. As of the Federal Register API on 2026-10-10 there is no final rule. https://www.sec.gov/rules-regulations/2026/02/s7-2026-05
- **Related:** the Names-rule N-PORT compliance dates were extended to the same 2027/2028 dates by IC-35963 (Federal Register 2026-03459).
- **Litigation:** a Fifth Circuit challenge is stayed while the SEC reviews the rule (stated in the proposal).

**Thematic ETFs file full holdings (verified).** The HUMN NPORT-P for period 2026-06-30 lists 46 positions with `pctVal` and identifiers: https://www.sec.gov/Archives/edgar/data/1976517/000089418926023604/primary_doc.xml. QTUM has quarterly public filings back to 2022-02 and earlier. Series IDs for each ticker are in https://www.sec.gov/files/company_tickers_mf.json. That file does not include DRAM, LYTE, NCLD or URNM; URNM's series is S000075060.

**edgartools (verified).** MIT licence, 2,781 stars, last push 2026-10-08, latest release v5.61.1 (2026-10-06).
- `edgar/funds/reports.py` defines `NPORT_FORMS = ["NPORT-P","NPORT-EX",...]` and `class FundReport`.
- The guide (https://github.com/dgunning/edgartools/blob/main/docs/guides/nport-data-object-guide.md) documents `filing.obj()`, `investment_data()` (CUSIP, resolved ticker, `pct_value`, `is_derivative`), `derivatives_data()`, and `Fund("TICKER").get_portfolio()`.
- One sentence in that guide says the non-public months are "released after 60 days". That is wrong under the current rule; treat those months as confidential.

**SEC quarterly N-PORT data sets.** https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets
- 28 zip files from **2019 Q4 to 2026 Q3**, about 400–480 MB each.
- "Updated quarterly", with quarters grouped by **filing date**. The page was last updated 2026-09-30.
- EDGAR fair access allows 10 requests/second with a declared User-Agent (https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).

## 4. EODHD `ETF_Data.Holdings`

Docs: https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds

**Verified:**
- `ETF_Data` contains `Holdings` (the full list, keyed by EODHD ticker such as `NVDA.US`), `Holdings_Count`, `Inception_Date`, and `filter=ETF_Data::Holdings`.
- `historical=1` (HistoricalComponents) is documented for "Indices only".
- Coverage is stated as "More than 10,000 ETFs".
- Cost is 10 calls per request; paid plans allow 100,000 calls/day and 1,000 requests/minute. The Fundamentals plan is $59.99/month and All-In-One is $99.99/month (https://eodhd.com/pricing).
- The demo key on VTI returns `Holdings_Count` 3,468, which matches `len(Holdings)`. There is **no holdings as-of field**; `General.UpdatedAt` was 2026-10-10.
- Terms (https://eodhd.com/financial-apis/terms-conditions): non-professional users may store and analyze the data privately, but may not redistribute, display or grant access to it.

**Unverified:** refresh cadence, completeness for small, swap-heavy or foreign-heavy thematic ETFs (DRAM, LYTE, NCLD, HUMN), and whether swaps appear at all. None of this can be tested with the demo key.

## 5. Coverage matrix

Inception dates come from issuer pages unless marked otherwise. Holdings counts are from issuer files on 2026-10-09 (non-cash rows) or from the latest NPORT-P (marked N-PORT).

| Theme | Finviz slugs | Dedicated US ETFs (inception · holdings · files) |
|---|---|---|
| AI compute / semis | theme `semiconductors`, `artificialintelligence`; sub `aicompute`, `semiscompute`, `semisfoundries`, `semislithography`, `semispackaging`, `semisdesigntools` | SMH (VanEck) 12/20/2011 · 26 · current xlsx/JSON. SOXX (iShares) 7/10/2001 · 30 · current CSV. AIQ (Global X) 05/11/18 · 89 · daily since 2024-08. CHAT (Roundhill) 05/18/2023 · 44 · daily since about 2023-12. AGIX (KraneShares) · 67 · daily (inception not checked). |
| AI data-center power | sub `aienergy`, `clouddatacenters`, `hardwaredatacenters`, `energycleansmartgrid`, `energybaseutilities` | **No dedicated ETF verified.** Proxy: DTCR (Global X data centers) 10/27/20 · 26 · daily since 2024-08. |
| HBM / memory | sub `semismemory`, `hardwarestorage` | DRAM (Roundhill Memory) launched 04/02/2026 · 26 rows, about 11 issuers including swaps · daily. |
| Optical / photonics / CPO | **no dedicated slug**; nearest `ainetworking`, `hardwarenetworking`, `telecominfrastructure` | LYTE (Roundhill Photonics & Optics) 8/6/2026 · 22 rows, about 14 issuers · daily. |
| Neocloud | **no dedicated slug**; nearest `aicloud`, `clouddatacenters` | NCLD (Roundhill Neocloud) 8/6/2026 · 23 rows, about 16 issuers · daily. |
| GLP-1 / obesity | sub `healthcaremetabolic` | OZEM (Roundhill) 5/21/24 · 24 · daily. HRTS (Tema, now "Heart & Health", so broader than GLP-1) 11/20/23 · 45 · dated files since 2026-08-18. |
| Nuclear / SMR | sub `energybasenuclear` | NLR (VanEck) 08/13/2007 · 28 · current only. NUKZ (Range) · 46 (N-PORT, 2026-05-31) · HTML only; first NPORT filed 2024-04-29. NUKX is an options-income fund, so excluded. |
| Uranium | sub `commenergyuranium` | URA (Global X) 11/04/10 · 62 · daily since at least 2024-09. URNM (Sprott) · 28 (N-PORT) · site returned 403. |
| Quantum | theme `quantumcomputing`; sub `quantumhardware`, `quantumsoftware`, `quantumcloud`, `quantumnetworking`, `quantumenablingtech`, `quantumapplications` | QTUM (Defiance) 09/04/2018 · 90 · current only plus quarterly PDFs. |
| Humanoid robotics | **no dedicated slug**; nearest `roboticsconsumer`, `airobotics`, `automationrobotics` | HUMN (Roundhill) 06/26/2025 · 42 · daily. KOID (KraneShares) 6/4/2025 · 50 · daily. BOTT (Themes, "Humanoid Robotics" in N-PORT) Apr 22, 2024 · 32 · current only. Broader: BOTZ 09/12/16 · 80 · daily; ARKQ 09/30/2014 · 36 · current only. |
| Stablecoins / crypto infrastructure | theme `cryptoblockchain`; sub `blockchaintokenization`, `blockchainpayments`, `blockchaininfrastructure`, `fintechblockchain` | BLOK (Amplify) 01/16/2018 · 56 (N-PORT) · Firestore widget. BKCH (Global X) 07/12/21 · 34 · daily. **No stablecoin-specific ETF verified.** |
| Space | theme `spacetech`; sub `spacelaunch`, `spacesatellites`, `spaceinfrastructure`, `defensespacetech` | UFO (Procure) 4/11/2019 · 67 · quarterly schedules. ARKX (ARK) 03/30/2021 · 33 · current only. |
| Drones / defense tech | sub `defensedrones`, `autonomousdefense`, `defensemissiles`, `defensecyberdefense` | DRNZ (REX) 10/29/2025 · 48 · no file found. SHLD (Global X) 09/11/23 · 53 · daily. |
| Cybersecurity | theme `cybersecurity` (8 `cybersecurity*` subs) plus `aisecurity`, `cloudsecurity` | CIBR (First Trust) · 44 (N-PORT) · site unreachable. BUG (Global X) 10/25/19 · 31 · daily. |
| EV / batteries | theme `electricvehicles`; sub `evsbatteries`, `energycleanbatteries`, `commmetalsbattery` | LIT (Global X) 07/22/10 · 43 · daily. IBAT (iShares) · 95 (N-PORT) · current CSV; first NPORT filed 2024-06-27. |
| Solar | sub `energycleansolar` | TAN (Invesco) · 34 (N-PORT) · issuer site not probed. |

Every ETF above also has quarterly NPORT-P history on EDGAR from its first filing; for the pre-2020 funds that starts around late 2019 or 2020.

## 6. Point-in-time membership sources, ranked

1. **SEC NPORT-P (EDGAR XML or the quarterly data sets).**
   - History: late 2019 onward for every fund. Before that, N-Q filings exist but are unstructured.
   - Cadence: quarterly snapshots, available up to 60 days after the period ends.
   - Cost and licence: free; US-government data under the fair-access policy.
   - Use it for backtests: key each record on the **filing date** so you only use what was known at the time. It includes swaps.
2. **Issuer dated daily files.** Roundhill (about 2023-12), KraneShares (about 2024-01), Global X (2024-08-06), Tema (2026-08-18). Daily and free, but the licences are restrictive: Global X forbids derivative works and iShares/BlackRock allows personal non-commercial use only.
3. **Your own daily archive of current-only files** (ARK, VanEck, iShares, Defiance, Themes, and EODHD `ETF_Data.Holdings`). History only accumulates from the day you start. EODHD costs 10 calls per ETF, so 100 ETFs is about 1,000 calls/day; its licence covers private use only.
4. **Finviz theme and sub-theme snapshots.** Current only. Requires Elite ($299.50/year) for the API, at 308 requests per sweep. Terms of Use not verified.
5. **EODHD HistoricalComponents.** Covers indices only, not themes.

## 7. Recommended taxonomy bootstrap

1. **Backbone.** Start from Finviz's 40 themes and 268 sub-themes, storing slug and label. Add owner-defined themes where Finviz has no matching slug: HBM, photonics/CPO, neocloud, humanoid robotics, stablecoins and AI power. Use the dedicated ETFs (DRAM, LYTE, NCLD, HUMN/KOID/BOTT, OZEM) as their definitions.
2. **Seed membership.**
   - (a) Run a weekly Finviz export per sub-theme. Only do this if the Terms of Use allow it, which I could not confirm.
   - (b) Take the union of thematic-ETF holdings above a weight threshold. Map them to your Russell 3000 universe by CUSIP or ticker, and resolve swap legs to their underlying CUSIP.
3. **Long tail.** Use embedding recall over EODHD descriptions and 10-K Item 1 text (via edgartools) to find candidates. Then have DeepSeek verify each one with a quoted piece of evidence and a confidence score.
4. **Evaluation.** Hold out US-listed ETF constituents (for example HUMN ∪ KOID ∪ BOTT for humanoid robotics) as the recall reference. Measure precision with a manual audit sample, and use Finviz agreement as a second reference.
5. **Point-in-time storage.** Store memberships with `valid_from`, `valid_to`, source and as-of date. Backtests should use N-PORT for 2019–2023 and add issuer daily files from late 2023 or August 2024. LLM-derived labels leak future information unless you classify from the 10-K that had been filed before each date.
6. **Refresh schedule:**
   - Daily: issuer snapshots.
   - Weekly: Finviz.
   - Quarterly: N-PORT ingestion and LLM re-classification after earnings season.
   - Ongoing: watch new thematic ETF launches. Roundhill launching DRAM in April 2026 and LYTE/NCLD in August 2026 is itself an early signal of an emerging theme. This is my inference, not something I tested.

## 8. What I could not verify

- Finviz Terms of Use, the Elite column list, and whether multi-value filters work.
- File availability for Sprott, First Trust, Invesco TAN, Range, REX and Amplify Firestore.
- Inception dates for AGIX, NUKZ, URNM, CIBR, IBAT and TAN.
- EODHD refresh cadence and thematic-ETF coverage.
- A Themes `URAN` file. I assumed it follows the BOTT URL pattern but did not check it.

The downloaded issuer files, Finviz slug list and N-PORT XML samples were research scratch data and were not checked in.
