# Evaluation ground truth plus a pilot: do free attention series lead theme moves on known 2019–2026 episodes?

The research proposes heat scores, emergence flags and lifecycle stages, but there is no dated catalog of theme episodes to evaluate lead time or false-alarm rate against. There is also no evidence yet that the free, backfillable attention sources (Wikipedia pageviews, HN Algolia, EDGAR full-text search) lead theme price moves in this setup. Several open questions ask for exactly this pilot before money and time go into collectors such as X counts, archived Stocktwits or ApeWisdom snapshots, or Alpaca backfill. The result decides whether attention is worth building or whether price/breadth plus news should drive the detector.

# Attention-lead pilot: do free attention series lead theme moves? (2017–2026, anecdotal)

**Verdict.** The three free, backfillable attention sources don't lead theme price moves better than chance in this pilot.
- **Wikipedia pageviews and HN fired on every control.** All 10 control fads triggered both of them, so they can only nominate candidate themes.
- **EDGAR 8-K full-text counts separate real themes from fads.** A persistent z>3 fired in 10 of 14 episodes and 0 of 10 controls. But it usually comes late: the median is 12 weeks after the first catalyst. That makes it a confirmation signal, not a lead.
- **Recommendation.** Use price and breadth (EODHD theme baskets) plus news as the detector's backbone. Add EDGAR as an "investability" confirmation gate and Wikipedia/HN as cheap, low-weight nomination features. Don't pay for X counts, archived Stocktwits/ApeWisdom snapshots or Alpaca backfill yet.

Everything below is anecdotal: 14 episodes, only 7 with a usable price breakout, and phrases and windows were chosen with hindsight.

---

## 0. What was run (2026-10-10)

**Data collected**
- **Wikipedia:** 49 articles. Daily `user`-agent pageviews 2015-07-01 to 2026-10-09, plus each article's first revision from the MediaWiki API.
- **HN Algolia:** 49 phrases. All matching stories 2014–2026, using a quoted phrase, `typoTolerance=false` and searching `title,url` only. Date windows were split until each held ≤1,000 hits, then every hit was paged, so counts are exact rather than the `nbHits` estimate.
- **EDGAR full-text search:** 30 phrases. Every 8-K document hit 2015-01-01 to 2026-10-09. Date ranges that hit the 10,000-result cap were split recursively. Documents were deduplicated by accession number (`adsh`), and the filer CIK was kept.

**Series (weekly, weeks ending Friday)**
- Wikipedia: views summed over the episode's articles.
- HN: stories, deduplicated by objectID across phrases.
- EDGAR: 8-K filings, deduplicated by `adsh` across phrases.
- **New variant, "EDGAR new filers":** companies (CIKs) using the phrase for the first time since 2015.

**Rules**
- **z-score:** z = (x − median₅₂)/max(1.4826·MAD₅₂, floor), with the median and MAD taken over the trailing 52 weeks, excluding the current week.
  - Floor is 1 for counts. For Wikipedia it is max(1, 5% of the median).
  - HN and EDGAR fires also need at least 3 items in that week.
- **Persistent fire:** z>3 in at least 2 of 3 consecutive weeks. A real-time alert would arrive up to 2 weeks after the date shown.
- **Price breakout:** the first week a weekly NAV close beats the prior 52-week high and the 13-week return is above 20%. Breakouts in the first 4 weeks of a window are excluded as "already trending" (ESPO 2020-07-03, DAPP 2024-07-05).
- **Lead:** reference date minus fire date, in weeks. Positive means attention came first.

**Episode windows**
| Episode | Window |
|---|---|
| Cannabis | 2017-07–2019-06 |
| EV/clean energy | 2019-07–2021-06 |
| SPACs | 2019-07–2021-06 |
| Metaverse | 2020-07–2022-06 |
| GenAI | 2022-07–2024-06 |
| AI data-center power | 2023-07–2025-06 |
| Nuclear | 2023-07–2025-06 |
| Quantum | 2024-01–2025-12 |
| GLP-1 | 2022-07–2024-06 |
| Humanoid robotics | 2024-07–2026-06 |
| Stablecoins | 2024-07–2026-03 |
| Space | 2024-07–2026-06 |
| Drones | 2024-07–2026-06 |
| Memory/HBM | 2025-01–2026-09 |

---

## 1. Episode catalog

ETF dates are inception, or "began trading", from the issuer page or SEC filing. Wikipedia creation dates are the first revision from `action=query&prop=revisions&rvdir=newer&rvlimit=1`, retrieved 2026-10-10.

| Episode | First dedicated US ETF (date, source) | Key catalysts (date, source) | Wikipedia article (created) | Representative US tickers* |
|---|---|---|---|---|
| Cannabis 2018–19 | **MJ**: original fund 2015-12-03 ([amplifyetfs.com/mj](https://amplifyetfs.com/mj/)); became the ETFMG Alternative Harvest cannabis index fund "on or about **2017-12-26**" ([SEC 497, 2017-12-18](https://www.sec.gov/Archives/edgar/data/1467831/000161577417007401/s108461_497.htm)). YOLO 2019-04-17 ([AdvisorShares](https://advisorshares.com/etfs/yolo/)); CNBS 2019-07-22 ([Amplify](https://amplifyetfs.com/cnbs/)) | 2018-06-21 Cannabis Act royal assent ([parl.ca C-45](https://www.parl.ca/legisinfo/en/bill/42-1/c-45)); 2018-08-15 Constellation's C$5B Canopy deal ([STZ 8-K](https://www.sec.gov/Archives/edgar/data/16918/000001691818000054/stz_form8k.htm)); 2018-10-17 Canada legalization in force and 2018-12-20 Farm Bill signed (*not verified, from memory*) | Cannabis in Canada (2005-10-31); Cannabis Act (Canada) (2017-04-12; moved from "Cannabis Act" 2025-04-01); Legality of cannabis (2003-01-14) | TLRY, CGC, ACB, CRON, SNDL, IIPR, GRWG, SMG |
| EV/clean energy 2020–21 | **KARS** 2018-01-18 ([KraneShares](https://kraneshares.com/kars/)); LIT 2010-07-22 ([Global X](https://www.globalxetfs.com/funds/lit/)); PBW 2005-03-03 (*not verified*) | 2020-06-04 Nikola lists via VectoIQ ([8-K](https://www.sec.gov/Archives/edgar/data/1731289/000110465920070923/tm2021982d1_8k.htm)); 2020-11-07 election called; 2020-11-16 Tesla S&P 500 add announced (S&P DJI page returned 403; *from memory*) | Electric vehicle (2003-07-27); Electric car (2005-07-22); Renewable energy (2001-07-31) | TSLA, NIO, PLUG, ENPH, FSLR, SEDG, BLNK, QS |
| SPACs 2020–21 | **SPAK** commenced 2020-09-30 ([N-CSR](https://www.sec.gov/Archives/edgar/data/1540305/000139834421006168/fp0062952_ncsr.htm)); SPCX 2020-12-15 ([N-CSR](https://www.sec.gov/Archives/edgar/data/1719812/000116204421001528/so36spacformncsr.htm)); SPXZ 2021-01-25 ([N-CSRS](https://www.sec.gov/Archives/edgar/data/1683471/000139834421018365/fp0068102_ncsrs.htm)). All three are now delisted. | 2020-07-22 Pershing Square Tontine IPO ([424B4](https://www.sec.gov/Archives/edgar/data/1811882/000119312520197776/d930055d424b4.htm)); 2021-04-12 SEC staff statement on SPAC warrants, which marked the bust ([sec.gov](https://www.sec.gov/news/public-statement/accounting-reporting-warrants-issued-spacs)) | Special-purpose acquisition company (2006-04-26) | SPCE, DKNG, NKLA, OPEN, CLOV, QS, SOFI, LCID |
| Metaverse 2021–22 | **METV** 2021-06-30 ([Roundhill](https://www.roundhillinvestments.com/etf/metv/)) | 2021-03-10 Roblox direct listing (*from memory*); 2021-10-28 Facebook renamed Meta ([about.fb.com](https://about.fb.com/news/2021/10/facebook-company-is-now-meta/)) | Metaverse (2005-02-08) | RBLX, U, META, NVDA, MTTR, SNAP, TTWO |
| GenAI/semis 2023–24 | **CHAT** began trading 2023-05-18 ([Roundhill](https://www.roundhillinvestments.com/etf/chat/)) | 2022-11-30 ChatGPT ([openai.com](https://openai.com/index/chatgpt/), returned 403); 2023-05-24 NVIDIA guides Q2 FY24 revenue to $11.0B ([NVIDIA](https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-first-quarter-fiscal-2024)) | ChatGPT (2022-12-05); Large language model (2023-03-09); Generative AI (2023-03-14; the redirect "Generative artificial intelligence" dates from 2022-10-22) | NVDA, MSFT, AMD, AVGO, SMCI, PLTR, META, AI |
| AI data-center power 2024–25 | No verified "first" fund. **VOLT** 2024-12-03 ([Tema](https://temaetfs.com/volt)); ZAP 2024-12-17 ([Global X](https://www.globalxetfs.com/funds/zap/)) | 2024-03-04 Talen sells data-center campus to AWS (Talen IR returned 403; *from memory*); 2024-09-20 Constellation–Microsoft 20-year Crane (TMI-1) PPA ([Constellation](http://www.constellationenergy.com/news/2024/Constellation-to-Launch-Crane-Clean-Energy-Center-Restoring-Jobs-and-Carbon-Free-Power-to-The-Grid.html)); 2025-01-21 Stargate (*from memory*) | Environmental impact of AI (2024-07-03); Data center (2004-04-06). "Energy consumption of artificial intelligence" does not exist. | VST, CEG, NRG, TLN, GEV, ETN, VRT, PWR |
| Nuclear/SMR/uranium 2024–25 | NLR 2007-08-13 ([VanEck](https://www.vaneck.com/us/en/investments/uranium-nuclear-energy-etf-nlr/)); URA 2010-11-04 ([Global X](https://www.globalxetfs.com/funds/ura/)); **NUKZ** January 2024 (497 filed 2023-12-22, [SEC](https://www.sec.gov/Archives/edgar/data/1452937/000121390023097915/ea167169_497.htm); the exact day is *not verified*, 2024-01-23 used) | 2023-12-01 COP28 declaration to triple nuclear capacity ([DOE](https://www.energy.gov/articles/cop28-countries-launch-declaration-triple-nuclear-energy-capacity-2050-recognizing-key)); 2024-05-09 Oklo lists ([8-K](https://www.sec.gov/Archives/edgar/data/1849056/000110465924059381/tm2414009d1_8k.htm)); 2024-09-20 Microsoft TMI PPA; 2024-10-14 Google–Kairos ([Google](https://blog.google/outreach-initiatives/sustainability/google-kairos-power-nuclear-energy-agreement/)); 2024-10-16 Amazon–X-energy ([Amazon](https://www.aboutamazon.com/news/sustainability/amazon-nuclear-small-modular-reactor-net-carbon-zero); month verified, day from memory) | Small modular reactor (2010-06-25); Nuclear power (2002-01-23) | CCJ, OKLO, SMR, NNE, LEU, BWXT, UEC, CEG |
| Quantum 2024–25 | **QTUM** 2018-09-04 ([Defiance](https://www.defianceetfs.com/qtum/)) | 2024-12-09 Google Willow ([Google](https://blog.google/technology/research/google-willow-quantum-chip/)); 2025-02-19 Microsoft Majorana 1 ([Microsoft](https://news.microsoft.com/source/features/innovation/microsofts-majorana-1-chip-carves-new-path-for-quantum-computing/)) | Quantum computing (2001-10-21); Qubit (2001-10-15) | IONQ, RGTI, QBTS, QUBT, IBM, GOOGL, HON, ARQQ |
| GLP-1/obesity 2023–24 | **HRTS** 2023-11-20 ([Tema](https://temaetfs.com/hrts)); OZEM began trading 2024-05-21 and calls itself "the world's first GLP-1 ETF" ([Roundhill](https://www.roundhillinvestments.com/etf/ozem/)); THNR 2024-05-20 ([Amplify](https://amplifyetfs.com/thnr/)) | 2021-06-04 Wegovy approved (FDA URL returned 404; *from memory*); 2023-08-08 SELECT trial shows 20% MACE reduction ([Novo](https://www.novonordisk.com/content/nncorp/global/en/news-and-media/news-and-ir-materials/news-details.html?id=166301)); 2023-11-08 Zepbound approved ([FDA](https://www.fda.gov/news-events/press-announcements/fda-approves-new-medication-chronic-weight-management)) | Semaglutide (2016-04-11; the "Ozempic" and "Wegovy" redirects were added to its views); Tirzepatide (2021-10-28); GLP-1 receptor agonist (2009-01-13) | LLY, NVO, VKTX, AMGN, HIMS, ALT, GPCR, TERN |
| Humanoid robotics 2025–26 | **KOID** 2025-06-04 ([KraneShares](https://kraneshares.com/koid/)); HUMN 2025-06-26 ([Roundhill](https://www.roundhillinvestments.com/etf/humn/)). BOTT launched 2024-04-22 as Themes *Robotics & Automation* ([8-A12B](https://www.sec.gov/Archives/edgar/data/1976322/000182912624002483/themesetf_8a12b.htm)) and was renamed Humanoid in 2025 ([485APOS](https://www.sec.gov/Archives/edgar/data/1976322/000182912625003759/filename8.htm)), so it is not the first. | 2025-01-06 NVIDIA Cosmos launch: "The ChatGPT moment for robotics is coming" ([NVIDIA](https://nvidianews.nvidia.com/news/nvidia-launches-cosmos-world-foundation-model-platform-to-accelerate-physical-ai-development)); 2025-03-18 GR00T N1 ([NVIDIA](https://nvidianews.nvidia.com/news/nvidia-isaac-gr00t-n1-open-humanoid-robot-foundation-model-simulation-frameworks)) | Humanoid robot (2002-04-08); Optimus (robot) (2021-08-20, the day after Tesla AI Day); Figure AI (2024-03-15) | TSLA, NVDA, TER, SYM, RR, SERV |
| Stablecoins 2025 | **STBQ** 2025-12-22 ([Amplify](https://amplifyetfs.com/stbq/); "first" *not verified*) | 2024-10-21 Stripe–Bridge deal announced (seen in HN stories that day; closed 2025-02-04, [Stripe](https://stripe.com/newsroom/news/stripe-completes-bridge-acquisition)); 2025-06-05 CRCL starts trading ([Circle](https://www.circle.com/pressroom/circle-announces-pricing-of-upsized-initial-public-offering)); 2025-07-18 GENIUS Act signed ([White House](https://www.whitehouse.gov/fact-sheets/2025/07/fact-sheet-president-donald-j-trump-signs-genius-act-into-law/)) | Stablecoin (2018-05-17); GENIUS Act (2025-07-07) | CRCL, COIN, HOOD, PYPL, XYZ, GLXY |
| Space 2025–26 | **UFO** 2019-04-11 ([Procure](https://procureetfs.com/ufo/)); ROKT 2018-10-22 (start of SSGA NAV file); ARKX 2021-03-30 (*not verified*) | 2024-10-13 Starship booster catch (HN top story, 2,263 points); 2025-01-27 "Iron Dome for America" executive order ([White House](https://www.whitehouse.gov/presidential-actions/2025/01/the-iron-dome-for-america/)); 2025-08-07 Firefly IPO ([424B4](https://www.sec.gov/Archives/edgar/data/1860160/000119312525175679/d849748d424b4.htm)) | SpaceX Starship (2018-11-21); Rocket Lab (2009-11-17); Golden Dome (missile defense system) (2025-01-28) | RKLB, ASTS, LUNR, PL, RDW, FLY, SPCE, KRMN |
| Drones/defense tech 2025–26 | **DRNZ** 2025-10-29 ([REX](https://www.rexshares.com/drnz/)); SHLD 2023-09-11 ([Global X](https://www.globalxetfs.com/funds/shld/)) | 2025-06-01 Operation Spiderweb (*from memory*); 2025-06-06 EO 14307 "Unleashing American Drone Dominance" ([White House](https://www.whitehouse.gov/presidential-actions/2025/06/unleashing-american-drone-dominance/)); 2025-07-10 Defense Department drone memo (defense.gov returned 403) | Unmanned aerial vehicle (2002-06-22); Drone warfare (2013-01-13) | AVAV, KTOS, RCAT, UMAC, ONDS, DPRO, AIRO |
| Memory/HBM 2025–26 | **DRAM** began trading 2026-04-02 and calls itself "the first-ever memory stock ETF" ([Roundhill](https://www.roundhillinvestments.com/etf/dram/)) | 2025-10-01 Samsung and SK hynix join Stargate (fetch timed out; *from memory*); 2025-12-03 Micron exits its Crucial consumer business ([Micron](https://investors.micron.com/news-releases/news-release-details/micron-announces-exit-crucial-consumer-business)) | High Bandwidth Memory (2014-12-08); Dynamic random-access memory (2002-07-29) | MU, WDC, STX, SNDK, LRCX, AMAT |

\*Tickers are from domain knowledge and were not checked against ETF holdings files.

---

## 2. Price data: account-free, legitimate sources (checked 2026-10-10)

| Source | Result |
|---|---|
| **VanEck** | **Works.** The fund page's "NAV & Premium/Discount History" link returns an xlsx with daily NAV since inception (NLR from 2007-08-13). Pattern: `https://www.vaneck.com/us/en/investments/<slug>/downloads/fundhistoprices/`. Used for NLR, SMH, ESPO, SMOG, PPH, DAPP, IBOT. Their robots.txt sets `Crawl-delay: 25`. |
| **State Street SPDR** | **Works.** `https://www.ssga.com/library-content/products/fund-data/etfs/us/navhist-us-en-<ticker>.xlsx` gives daily NAV since inception. Used for XLU, XAR, ROKT, XSD, KOMP, CNRG. The files are *not split-adjusted*; my loader detected and fixed the XLU 2:1 split on 2025-12-05. |
| Global X | No. Pages offer only "Full Holdings (.csv)" (e.g. URA, ZAP). |
| ARK | No. The page is JS-rendered and even the inception field shows "--". |
| Amplify (MJ, STBQ, THNR, CNBS) | No. Documents and holdings only. |
| Roundhill (METV, CHAT, …) | No. METV's page has no download; CHAT's only xlsx is a proxy voting table. |
| Defiance (QTUM) | Premium/discount PDF only. |
| Nasdaq.com | **Not legitimate for automated use.** `api.nasdaq.com/robots.txt` is `Disallow: /`, and the [Terms](https://www.nasdaq.com/legal) (last updated May 11, 2026) forbid "any process, whether automated or manual, to capture data or content from the Service". I made about 75 requests before checking these terms, then **discarded the data and used none of it**. |

**Consequence.** No legitimate account-free history exists for most dedicated theme ETFs, and the SPAC ETFs are delisted. Price references are therefore VanEck/SSGA **proxies**: SMOG, ESPO, SMH, XLU, NLR, PPH, IBOT, DAPP, ROKT, XAR. There is none for cannabis, SPACs or quantum. Leads are also measured against ETF inception and catalyst dates. The EODHD join script in §6 replaces the proxies with real ETFs and equal-weight ticker baskets.

---

## 3. Pilot results (anecdotal)

### 3a. Lead/lag per episode

Each cell is the first z>3 week, then (lead vs breakout / vs ETF / vs first catalyst) in weeks. "pre" means the ETF predates the episode, so no lead is computed.

| Episode | Proxy breakout | ETF ref | 1st catalyst | Wikipedia | HN | EDGAR 8-K | EDGAR new filers |
|---|---|---|---|---|---|---|---|
| cannabis | none | 2017-12-26 | 2018-06-21 | 2018-01-05 (–/−1/+24) | 2018-04-13 (–/−15/+10) | 2017-11-17 (–/+6/+31) | never |
| ev_clean | SMOG 2020-01-03 | pre (2018) | 2020-11-07 | never | 2019-07-12 (+25/pre/+69) | 2019-07-26 (+23/pre/+67) | 2019-07-26 (+23/pre/+67) |
| spac | none | 2020-09-30 | 2020-06-04 | 2019-07-12 (–/+64/+47) | 2020-07-31 (–/+9/−8) | 2019-11-08 (–/+47/+30) | 2020-09-18 (–/+2/−15) |
| metaverse | ESPO, excluded | 2021-06-30 | 2021-03-10 | 2020-11-27 (–/+31/+15) | 2020-08-07 (–/+47/+31) | 2021-10-29 (–/−17/−33) | 2021-11-12 (–/−19/−35) |
| genai | SMH 2023-05-26 | 2023-05-18 | 2022-11-30 | 2022-10-28† (+30/+29/+5) | 2022-09-23 (+35/+34/+10) | 2023-03-03 (+12/+11/−13) | 2023-03-03 (+12/+11/−13) |
| ai_power | XLU: none | 2024-12-03 | 2024-03-04 | 2024-12-13 (–/−1/−41) | 2023-08-18 (–/+68/+28) | 2024-05-10 (–/+30/−10) | 2024-11-15 (–/+3/−37) |
| nuclear | NLR 2023-11-17 | 2024-01-23 | 2023-12-02 | 2024-10-18 (−48/−38/−46) | 2024-06-21 (−31/−21/−29) | 2024-05-10 (−25/−15/−23) | never |
| quantum | none | pre (2018) | 2024-12-09 | 2024-12-13 (–/pre/−1) | 2024-04-05 (–/pre/+35) | 2024-08-09 (–/pre/+17) | 2025-08-01 (–/pre/−34) |
| glp1 | PPH: none | 2023-11-20 | 2021-06-04 | 2022-07-01 (–/+72/−56) | 2023-02-24 (–/+38/−90) | 2023-08-04 (–/+15/−113) | 2023-08-04 (–/+15/−113) |
| humanoid | IBOT 2025-07-18 | 2025-06-04 | 2025-01-06 | 2024-10-11 (+40/+34/+12) | 2024-08-30 (+46/+40/+18) | 2026-02-13 (−30/−36/−58) | never |
| stablecoin | DAPP, excluded | 2025-12-22 | 2024-10-21 | 2024-10-25 (–/+60/−1) | 2024-10-25 (–/+60/−1) | 2025-05-16 (–/+31/−30) | 2025-07-25 (–/+21/−40) |
| space | ROKT 2024-11-29 | pre (2019) | 2025-01-27 | 2024-10-18 (+6/pre/+14) | 2024-10-18 (+6/pre/+14) | 2025-08-08 (−36/pre/−28) | never |
| drones | XAR 2025-06-06 | 2025-10-29 | 2025-06-01 | 2024-12-20 (+24/+45/+23) | 2024-12-13 (+25/+46/+24) | 2024-11-08 (+30/+51/+29) | 2026-06-19 (−54/−33/−55) |
| memory | SMH 2025-06-27 | 2026-04-02 | 2025-10-01 | 2025-12-05 (−23/+17/−9) | 2025-12-05 (−23/+17/−9) | 2025-11-07 (−19/+21/−5) | never |

† Artifact of the "Generative artificial intelligence" redirect being created on 2022-10-22: there was no prior baseline, so z jumps.

**What triggered the first HN fire** (top stories in the fire week):
- **Real signals:**
  - **GenAI, 2022-09-23:** Sequoia's "Generative AI: A Creative New World", about 10 weeks before ChatGPT.
  - **Stablecoins:** Stripe–Bridge, $1.1B.
  - **Space:** Starship Flight 5 booster catch.
  - **Humanoid, 2024-08-30:** 1X Neo Beta.
  - **Memory, 2025-12-05:** "Average DRAM price…" (523 points) and Micron's Crucial exit.
  - **Nuclear, 2024-06-21:** Congress passes the ADVANCE Act.
- **Spurious:**
  - **Drones, 2024-12-13:** the New Jersey drone sightings (458 points). That event was not investable.
  - **AI power, 2023-08:** four stories about lasers and the power grid.
  - **EV, 2019-07:** VW–Ford platform news.
  - **Metaverse and SPACs:** only 4 stories each.

### 3b. Summary per source

| Source | Fired z>3 in window | Fired before valid price breakout (n=7) | Expected by chance‡ | Fired before ETF launch in window (n=11) | Fired before 1st catalyst | Persistent z>3 before breakout | Median lift, 26w before ETF / before breakout | Controls fired z>3 / persistent (n=10) | Base rate z>3 (weeks/yr) |
|---|---|---|---|---|---|---|---|---|---|
| Wikipedia | 13/14 | 4/7 (median +15w) | 0.68 | 8/11 (median +31w) | 7/13 (+5w) | 4/7 | 1.8× / 0.95× | **10/10 / 9/10** | 6.9% (~3.6) |
| HN | 14/14 | 5/7 (+25w) | 0.76 | 9/11 (+38w) | 9/14 (+12w) | 3/7 | 2.3× / 2.3× | **10/10 / 8/10** | 4.7% (~2.4) |
| EDGAR 8-K (by accession) | 14/14 | 3/7 (median −19w) | 0.65 | 8/11 (+15w) | 5/14 (−12w) | 1/7 | 2.5× / 0× | **2/10 / 0/10** | 2.2% (~1.1) |
| EDGAR new filers (CIK) | 9/14 | 2/7 | 0.19 | 5/7 fired (+3w) | 1/9 | 1/7 | 2.3× / 2.8× (tiny n) | **0/10 / 0/10** | 0.2% |

‡ The share of episodes where at least one z>3 week would land before the breakout by chance, given each series' base rate. The observed shares (57%, 71%, 43%) are at or below chance. A single-source "first z>3" date is therefore not evidence of a lead.

**Two multi-source rules**
- **Rule A** (at least 2 of the 3 sources z>3 within 4 weeks): fires in 14/14 episodes and 9/10 controls. It doesn't separate themes from fads.
- **Rule B** (Wikipedia or HN z>3 in the trailing 4 weeks, *and* EDGAR z>2 in the trailing 8 weeks): fires in **14/14 episodes and 1/10 controls** (Web3, 2022-01-21).
  - It came before the valid breakout in 4/7 episodes, median +3 weeks: EV +23, genAI +13, space +3, drones +25. It came after in nuclear (−31), humanoid (−8) and memory (−23).
  - It came before the ETF launch in 6/11, median +12 weeks.

### 3c. Controls (topics that never became investable themes)

| Control (window) | Wikipedia first z>3 / weeks above 3 / max z | HN first z>3 / weeks above 3 | EDGAR first z>3 / weeks above 3 / max filings per week |
|---|---|---|---|
| NFT (2020-07–2022-06) | 2020-07-03 / 50 / 202 | 2021-02-19 / 26 | 2022-05-06 / 1 / 4 |
| Web3 (2021–22) | 2021-01-01 / 61 / 857 | 2021-09-17 / 22 | 2022-01-21 / 4 / 5 |
| LK-99 (2023-01–2024-06) | 2023-07-28 / 26 / ~10⁶ (zero baseline) | 2023-07-28 / 5 | "superconductor": never / 0 / 2 |
| Room-temperature superconductor | 2023-03-10 / 14 / 660 | 2023-03-10 / 10 | never / 0 / 2 |
| Metaverse after 2022 (2023-01–2026-09) | 2025-01-17 / 6 / 30 | 2025-12-05 / 6 (Meta cutting the metaverse budget) | never / 0 / 11 |
| Cultured meat (2019–24) | 2020-12-04 / 22 / 29 | 2020-12-04 / 5 | "cultivated meat": never / 0 / 1 |
| Vertical farming (2019–23) | 2019-06-14 / 4 / 3 | 2019-09-06 / 1 | never / 0 / 3 |
| Fusion (2021–24) | 2021-01-01 / 13 / 79 | 2021-08-20 / 14 (NIF) | "fusion energy": never / 0 / 2 |
| Apple Vision Pro (2023–24) | 2023-06-09 / 34 / 50,850 | 2023-06-09 / 23 | "spatial computing": never / 0 / 3 |
| Clubhouse (2020-07–2022-06) | 2021-01-15 / 28 / 147,048 | 2020-12-25 / 19 | "social audio": never / 0 / 1 |

Rough false-alarm rates: Wikipedia and HN 100%; EDGAR 20% (both cases crypto-adjacent, at most 5 filings a week); EDGAR persistent and new-filer 0%.

---

## 4. Caveats

**Wikipedia**
- **2025 bot reclassification.** Wikimedia reclassified March–August 2025 traffic (bots from Brazil in May–June) and reports human pageviews down "roughly 8%" year on year ([Diff, 2025-10-17](https://diff.wikimedia.org/2025/10/17/new-user-trends-on-wikipedia/)). That biases 2025 z-scores downward. Normalize by total en.wikipedia `user` views or by a reference-article basket.
- **Views follow titles, not pages.** "Cannabis Act" moved on 2025-04-01. Redirect views count under the redirect's own title (I added Ozempic, Wegovy and "Generative artificial intelligence" by hand).
- **Zero-baseline artifacts.** Newly created articles (ChatGPT, LK-99, GENIUS Act) jump z because there is no history. Treat article creation as a separate event.
- **Rate limits.** The 2026 policy allows 10 requests/minute for unidentified clients and 200/minute with a compliant User-Agent ([mediawiki.org](https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits)).

**HN**
- **Strong tech bias.** Over 2014–2026, "SPAC" matched only 403 stories, "semaglutide" 87 and "blank check" 34.
- **Small counts.** Fires trigger on 4–11 stories.
- **News, not investable themes.** The New Jersey drone sightings are an example.
- **Partial phrase matching.** Only title and URL are searched.
- The 10k requests/hour/IP limit comes from the task brief; https://hn.algolia.com/api is JS-rendered and I could not confirm it.

**EDGAR**
- Hits are documents (the 8-K and its exhibits), so I deduplicated by `adsh`.
- **Boilerplate.** Companies repeat forward-looking-statement and risk language. The new-filer variant removes repeats.
- **Generic terms collide.** "drone", "obesity" and "nuclear energy" all match unrelated filings.
- Each query caps at 10,000 results; I split ranges to stay under it.
- **Fair access.** SEC rules are 10 requests/second and a User-Agent with contact details ([sec.gov](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)). `efts.sec.gov` accepted my non-email User-Agent; `www.sec.gov/Archives` returned 403. Use your own contact User-Agent.
- Coverage starts in 2001 ([FAQ](https://www.sec.gov/edgar/search/efts-faq.html)).

**Price**
- The proxies are not the themes. ESPO is gaming, XLU and PPH never rise 20% in 13 weeks, and SMH is a generic semis fund rather than memory.
- NAV is price-only (distributions not reinvested).
- **Design bias.** Windows and phrases were chosen with hindsight, all episodes are survivors, and n=7 for valid breakouts.

---

## 5. Recommendation for the heat score

1. **Price and breadth are the backbone.** Use theme-basket relative strength and breakouts from EODHD, plus breadth within each basket. Add news (Yahoo RSS and DeepSeek) for attribution.
2. **EDGAR 8-K full-text search is a confirmation and investability gate.**
   - Use persistent z>3 on filings deduplicated by accession, plus a new-filer count.
   - It is cheap and backfillable.
   - In the pilot it was the only source that separated themes from fads: persistent 10/14 episodes vs 0/10 controls, new filers 0/10 controls.
   - It is lagging, so don't use it as a lead signal.
3. **Wikipedia is a nomination feature, low weight.**
   - Use persistent z plus article-creation events.
   - Apply the 2025 normalization.
   - It must not trigger alone: z>3 fires about 3.6 weeks per year per series and in 10/10 fads.
4. **HN is a nomination feature for tech themes only** (AI, quantum, memory, space, robotics), at low weight. Exclude it for bio, consumer and finance themes.
5. **Emergence flag = Rule B** (attention nomination plus EDGAR confirmation), shown next to the price state. Don't compute lifecycle stages from attention alone.
6. **Don't pay yet** for X counts, archived Stocktwits/ApeWisdom snapshots or Alpaca backfill. The free sources showed no lead beyond chance.
   - My inference: paid social counts would likely share the same fad problem.
   - Rerun §6 against EODHD baskets and widen to about 30 episodes and 30 controls before revisiting.

---

## 6. Python code (run with polite rate limits)

```python
# ---- config.py (abridged; full lists in §1 / §3c) ----
EPISODES = {
 "genai_2023": dict(wiki=["Generative artificial intelligence","Large language model","ChatGPT"], hn=["ChatGPT","LLM","generative AI"], edgar=["generative AI","large language model"]),
 "nuclear_2024": dict(wiki=["Small modular reactor","Nuclear power"], hn=["nuclear power","small modular reactor","SMR"], edgar=["small modular reactor","nuclear energy"]),
 # cannabis: [Cannabis in Canada, Cannabis Act, Legality of cannabis] / [cannabis, marijuana] / [cannabis]
 # ev_clean: [Electric vehicle, Electric car, Renewable energy] / [electric vehicle, electric car, clean energy] / [electric vehicle]
 # spac: [Special-purpose acquisition company] / [SPAC, SPACs, blank check] / [special purpose acquisition company]
 # metaverse: [Metaverse]/[metaverse]/[metaverse]; ai_power: [Environmental impact of artificial intelligence, Data center] / [data center power, datacenter power, power grid] / [AI data center, data center power]
 # quantum: [Quantum computing, Qubit]/[quantum computing, quantum computer]/[quantum computing]; glp1: [Semaglutide(+Ozempic,Wegovy), Tirzepatide, GLP-1 receptor agonist]/[Ozempic, semaglutide, GLP-1]/[GLP-1, obesity]
 # humanoid: [Humanoid robot, Optimus (robot), Figure AI]/[humanoid, humanoid robot]/[humanoid]; stablecoin: [Stablecoin, GENIUS Act]/[stablecoin, stablecoins]/[stablecoin]
 # space: [SpaceX Starship, Rocket Lab, Golden Dome (missile defense system)]/[Starship, Rocket Lab]/[space economy, low earth orbit]
 # drones: [Unmanned aerial vehicle, Drone warfare]/[drone, drones]/[unmanned aircraft, drone]; memory: [High Bandwidth Memory, Dynamic random-access memory]/[HBM, DRAM, high bandwidth memory]/[high bandwidth memory, HBM]
}
UA = "QuantDashboard-research-pilot/0.1 (YOUR CONTACT URL/EMAIL)"   # a compliant UA gets 200 req/min on Wikimedia

# ---- collect_wiki.py ----
import json, os, time, urllib.parse, requests
S = requests.Session(); S.headers["User-Agent"] = UA
def get(url, params=None, pause=6.5):            # 6.5 s pacing is for an unidentified UA (10/min); use ~0.4 s with a compliant UA
    for _ in range(8):
        r = S.get(url, params=params, timeout=60)
        if r.status_code == 429: time.sleep(int(r.headers.get("retry-after", "60")) + 2); continue
        time.sleep(pause); return r
def wiki(title, follow_redirects=True):
    p = {"action":"query","titles":title,"prop":"revisions","rvdir":"newer","rvlimit":1,"rvprop":"timestamp|size|comment","format":"json"}
    if follow_redirects: p["redirects"] = 1
    pg = list(get("https://en.wikipedia.org/w/api.php", p).json()["query"]["pages"].values())[0]
    rec = {"title": title, "canonical": pg.get("title"), "created": pg.get("revisions", [{}])[0].get("timestamp")}
    art = urllib.parse.quote(rec["canonical"].replace(" ", "_"), safe="")
    pv = get(f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{art}/daily/20150701/20261009")
    rec["daily"] = {i["timestamp"][:8]: i["views"] for i in pv.json().get("items", [])} if pv.ok else {}
    return rec

# ---- collect_hn.py ----
def hn_q(phrase, a, b, page=0, hpp=1000):
    p = {"query": f'"{phrase}"', "tags": "story", "typoTolerance": "false", "restrictSearchableAttributes": "title,url",
         "numericFilters": f"created_at_i>={a},created_at_i<{b}", "hitsPerPage": hpp, "page": page,
         "attributesToRetrieve": "created_at_i,objectID,title,points", "attributesToHighlight": ""}
    r = S.get("https://hn.algolia.com/api/v1/search_by_date", params=p, timeout=60); time.sleep(0.7); return r.json()
def hn_collect(phrase, a, b, acc):               # split windows until nbHits<=1000, then page every hit (exact counts)
    n = hn_q(phrase, a, b, hpp=0)["nbHits"]
    if n == 0: return
    if n > 1000 and b - a > 3600:
        m = (a + b) // 2; hn_collect(phrase, a, m, acc); hn_collect(phrase, m, b, acc); return
    page = 0
    while True:
        d = hn_q(phrase, a, b, page=page)
        for h in d["hits"]: acc[h["objectID"]] = h["created_at_i"]
        page += 1
        if page >= d.get("nbPages", 0): break

# ---- collect_edgar.py ----
import datetime as dt
def fts(phrase, a, b, frm=0):
    p = {"q": f'"{phrase}"', "forms": "8-K", "dateRange": "custom", "startdt": a.isoformat(), "enddt": b.isoformat(), "from": frm}
    for i in range(8):
        r = S.get("https://efts.sec.gov/LATEST/search-index", params=p, timeout=60); time.sleep(0.25)   # <=4 req/s
        if r.ok: return r.json()
        time.sleep(3 * (i + 1))
def edgar_collect(phrase, a, b, acc):            # acc: adsh -> [file_date, cik]; split ranges that hit the 10k cap
    d = fts(phrase, a, b); tot = d["hits"]["total"]
    if (tot["value"] >= 10000 or tot.get("relation") == "gte") and (b - a).days > 0:
        m = a + (b - a) // 2; edgar_collect(phrase, a, m, acc); edgar_collect(phrase, m + dt.timedelta(days=1), b, acc); return
    frm = 0
    while True:
        for h in d["hits"]["hits"]:
            s = h["_source"]; acc.setdefault(s["adsh"], [s["file_date"], (s.get("ciks") or [None])[0]])
        frm += 100
        if frm >= tot["value"] or not d["hits"]["hits"]: break
        d = fts(phrase, a, b, frm)
```

```python
# ---- analysis core (weekly W-FRI) ----
import numpy as np, pandas as pd
WK = "W-FRI"
def wiki_weekly(daily_dicts):        # list of {yyyymmdd: views}; missing day = 0
    idx = pd.date_range("2015-07-01", "2026-10-09", freq="D")
    s = sum(pd.Series({pd.Timestamp(k): v for k, v in d.items()}, dtype=float).reindex(idx).fillna(0) for d in daily_dicts)
    return s.resample(WK).sum()
def count_weekly(dates, start):      # HN created_at (unit='s') or EDGAR file_date, already deduped by objectID / adsh
    s = pd.Series(1.0, index=pd.to_datetime(dates)).sort_index()
    return s.resample(WK).sum().reindex(pd.date_range(start, "2026-10-09", freq=WK)).fillna(0.0)
def new_filer_weekly(filings):       # filings: adsh -> [file_date, cik]; first appearance of each CIK
    df = pd.DataFrame([(pd.Timestamp(d), c) for d, c in filings.values()], columns=["d", "cik"])
    return count_weekly(df.groupby("cik")["d"].min().values, "2015-01-02")
def robust_z(x, floor_abs=1.0, floor_rel=0.0, win=52):        # trailing 52w median/MAD, current week excluded
    v = x.values; med = np.full(len(x), np.nan); mad = np.full(len(x), np.nan)
    for i in range(win, len(x)):
        w = v[i-win:i]; m = np.median(w); med[i] = m; mad[i] = np.median(np.abs(w - m))
    scale = np.maximum(1.4826 * mad, np.maximum(floor_abs, floor_rel * med))
    return pd.Series((v - med) / scale, index=x.index), pd.Series(med, index=x.index)
# params: wiki floor_abs=1, floor_rel=0.05, min_level=0 ; hn/edgar floor_abs=1, min_level=3
def fire_mask(z, x, thr, min_level, persist=False):
    hit = (z > thr) & (x >= min_level)
    if persist: hit = hit & (hit.astype(int).rolling(3, min_periods=3).sum().shift(-2) >= 2)   # 2 of 3 weeks (look-ahead 2w)
    return hit
def first_fire(z, x, thr, start, end, min_level=0, persist=False):
    h = fire_mask(z, x, thr, min_level, persist); h = h[(h.index >= start) & (h.index <= end)]
    return h[h].index[0] if h.any() else None
def breakout(p, start, end):           # weekly close > prior-52w high AND 13w return > 20%
    hi = p.shift(1).rolling(52, min_periods=40).max(); r13 = p / p.shift(13) - 1
    ok = (p > hi) & (r13 > 0.20) & (p.index >= start) & (p.index <= end)
    return ok[ok].index[0] if ok.any() else None
def base_rate(z, x, thr, min_level, persist=False):
    h = fire_mask(z, x, thr, min_level, persist); h = h[(h.index >= "2017-01-01")][z.notna()]; return float(h.mean())
def chance_before(rate, start, ref):   # P(>=1 random fire between window start and ref)
    n = (pd.Timestamp(ref) - pd.Timestamp(start)).days / 7; return 1 - (1 - rate) ** n
def lift(z, x, thr, min_level, ref, rate, weeks=26):
    zz = z[(z.index < pd.Timestamp(ref)) & (z.index >= pd.Timestamp(ref) - pd.Timedelta(weeks=weeks))]
    return float(((zz > thr) & (x.reindex(zz.index) >= min_level)).mean()) / rate if rate else None
def lead_weeks(fire, ref): return None if fire is None or ref is None else round((pd.Timestamp(ref) - pd.Timestamp(fire)).days / 7)
def rule_b(m3_wiki, m3_hn, m2_edgar):  # boolean weekly masks -> emergence flag
    attn = pd.concat([m3_wiki, m3_hn], axis=1).fillna(0).rolling(4, min_periods=1).max().max(axis=1) > 0
    return attn & (m2_edgar.astype(int).rolling(8, min_periods=1).max() > 0)
# Issuer NAV loaders: VanEck xlsx (header row 'Date','NAV',...; dates mm/dd/yyyy) and SSGA navhist xlsx (dates dd-Mon-yyyy).
# Parse with any xlsx reader; back-adjust any 1-day NAV ratio <0.6 or >1.7 as an unadjusted split.
```

```python
# ---- eodhd_join.py — run from the QuantDashboard repo root; replaces the proxies with real ETFs + equal-weight baskets ----
import argparse, asyncio, pandas as pd
from services.eodhd_client import get_eod_historical_data
EPISODES = {  # (window), ETFs (first = dedicated), representative tickers
 "cannabis_2018": (("2017-07-01","2019-06-30"), ["MJ","YOLO","CNBS"], ["TLRY","CGC","ACB","CRON","SNDL","IIPR","GRWG","SMG"]),
 "ev_clean_2020": (("2019-07-01","2021-06-30"), ["KARS","DRIV","PBW","ICLN"], ["TSLA","NIO","PLUG","ENPH","FSLR","SEDG","BLNK","QS"]),
 "spac_2020": (("2019-07-01","2021-06-30"), ["SPAK","SPCX","SPXZ"], ["SPCE","DKNG","NKLA","OPEN","CLOV","QS","SOFI","LCID"]),
 "metaverse_2021": (("2020-07-01","2022-06-30"), ["METV"], ["RBLX","U","META","NVDA","MTTR","SNAP","TTWO"]),
 "genai_2023": (("2022-07-01","2024-06-30"), ["CHAT","SMH"], ["NVDA","MSFT","AMD","AVGO","SMCI","PLTR","META","AI"]),
 "ai_power_2024": (("2023-07-01","2025-06-30"), ["VOLT","ZAP"], ["VST","CEG","NRG","TLN","GEV","ETN","VRT","PWR"]),
 "nuclear_2024": (("2023-07-01","2025-06-30"), ["NUKZ","NLR","URA"], ["CCJ","OKLO","SMR","NNE","LEU","BWXT","UEC","CEG"]),
 "quantum_2024": (("2024-01-01","2025-12-31"), ["QTUM"], ["IONQ","RGTI","QBTS","QUBT","IBM","GOOGL","HON","ARQQ"]),
 "glp1_2023": (("2022-07-01","2024-06-30"), ["HRTS","OZEM","THNR"], ["LLY","NVO","VKTX","AMGN","HIMS","ALT","GPCR","TERN"]),
 "humanoid_2025": (("2024-07-01","2026-06-30"), ["KOID","HUMN","BOTT"], ["TSLA","NVDA","TER","SYM","RR","SERV"]),
 "stablecoin_2025": (("2024-07-01","2026-03-31"), ["STBQ"], ["CRCL","COIN","HOOD","PYPL","XYZ","GLXY"]),
 "space_2025": (("2024-07-01","2026-06-30"), ["UFO","ARKX","ROKT"], ["RKLB","ASTS","LUNR","PL","RDW","FLY","SPCE","KRMN"]),
 "drones_2025": (("2024-07-01","2026-06-30"), ["DRNZ","SHLD"], ["AVAV","KTOS","RCAT","UMAC","ONDS","DPRO","AIRO"]),
 "memory_2025": (("2025-01-01","2026-09-30"), ["DRAM"], ["MU","WDC","STX","SNDK","LRCX","AMAT"]),
}
async def weekly_adj(sym):
    rows = await get_eod_historical_data(f"{sym}.US", from_date="2015-01-01", to_date="2026-10-09")
    if not rows: return None
    df = pd.DataFrame(rows)
    return pd.Series(df["adjusted_close"].astype(float).values, index=pd.to_datetime(df["date"])).sort_index().resample("W-FRI").last().dropna()
def basket(series):
    r = pd.concat({k: v.pct_change() for k, v in series.items() if v is not None}, axis=1)
    return (1 + r.where(r.abs() < 1.0).mean(axis=1, skipna=True).fillna(0)).cumprod()
def breakout(p, s0, s1):
    hi = p.shift(1).rolling(52, min_periods=40).max(); r13 = p / p.shift(13) - 1
    ok = (p > hi) & (r13 > 0.20) & (p.index >= s0) & (p.index <= s1); return ok[ok].index[0] if ok.any() else None
def weeks(a, b): return None if a is None or b is None or pd.isna(a) or pd.isna(b) else round((pd.Timestamp(b) - pd.Timestamp(a)).days / 7)
async def main(pilot_dir, out):
    fires = pd.read_csv(f"{pilot_dir}/episode_results.csv"); recs = []
    for ep, ((s0, s1), etfs, ticks) in EPISODES.items():
        px = {t: await weekly_adj(t) for t in etfs + ticks}; refs = {}
        for t in etfs:
            if px[t] is not None: refs[f"etf:{t}"] = (breakout(px[t], s0, s1), px[t][s0:pd.Timestamp(s1) + pd.Timedelta(weeks=26)].idxmax())
        b = basket({t: px[t] for t in ticks}); refs["basket"] = (breakout(b, s0, s1), b[s0:pd.Timestamp(s1) + pd.Timedelta(weeks=26)].idxmax())
        for _, f in fires[fires.episode == ep].iterrows():
            for ref, (bo, pk) in refs.items():
                recs.append(dict(episode=ep, src=f.src, ref=ref, breakout=bo, peak=pk, z3=f.z3, z3persist=f.z3persist,
                                 lead_bo_z3=weeks(f.z3, bo), lead_bo_z3p=weeks(f.z3persist, bo), lead_peak_z3=weeks(f.z3, pk)))
    df = pd.DataFrame(recs); df.to_csv(out, index=False)
    print(df[df.ref == "basket"].dropna(subset=["lead_bo_z3"]).groupby("src")["lead_bo_z3"].agg(n="count", led=lambda v: int((v > 0).sum()), median="median"))
if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--pilot-dir", required=True); ap.add_argument("--out", default="theme_leads.csv")
    a = ap.parse_args(); asyncio.run(main(a.pilot_dir, a.out))
```

---

## 7. Process notes and unverified items

- **Web search ran out.** The shared WebSearch budget (200 calls per turn) was used up before this task started, so all verification came from fetching primary sources directly.
- **Catalyst dates marked "from memory" are low confidence.** Their pages returned 403, 404 or timed out: openai.com, congress.gov, S&P DJI, Talen, defense.gov, Samsung, and the FDA Wegovy page.
- **"First ETF" claims are not verified** for ai_power, stablecoins and drones.
- **NUKZ, ARKX and PBW exact inception days are not verified.**
- **Privacy slip.** One early test request to wikimedia.org carried your email address in its User-Agent. All later requests used a generic User-Agent with no personal data.
- **Scope kept.** No credentials were used, no accounts were created and no company systems were queried. `git status` on the repo is still clean.

The pilot code is checked in under [`pilot/code/`](pilot/code/) and the result tables under [`pilot/results/`](pilot/results/) (`episode_results.csv`, `control_results.csv`, `fire_rates.csv`, `combo.txt`). The raw Wikipedia/HN/EDGAR/NAV downloads were not checked in; rerun the `collect_*.py` scripts to rebuild them. The Nasdaq price collector was deliberately left out because Nasdaq's terms forbid automated capture, and that data was discarded.
