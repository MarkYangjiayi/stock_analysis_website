# Trending themes MVP — implementation spec

Status: contract for the v1 build (2026-10-11). Research and rationale:
`docs/trending_themes_research_2026-10-10/` (README, design_lean_mvp.md). This
file is the source of truth when it differs from the design docs.

## 0. Ground rules

- Python runs with the repository's `.venv` (3.11 locally, 3.12 in CI and Docker — no 3.12-only syntax).
  Backend tests: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/<file>.py`; full suite ~90 s.
- Frontend: `npm --prefix frontend test -- <path>`; `frontend/node_modules` is a symlink
  to the main checkout. Lint: `npm --prefix frontend run lint`. Type check:
  `frontend/node_modules/.bin/tsc --noEmit -p frontend`.
- Never print or log secrets. EODHD and DeepSeek keys come from settings; never log
  `params`, `str(httpx exception)` or request URLs from EODHD calls (they contain the token);
  log `type(exc).__name__` only.
- No external calls on page views: every GET reads local tables only.
- Missing values stay `None`/NaN and render "—"; never zero-fill.
- Point-in-time honesty: rows computed with today's membership for past dates carry `pit=False`.
- Tests: the autouse fixture rebuilds `Base.metadata` per test. Seed through `db_session`,
  commit, then call services. Patch names in the consuming module's namespace. Never hit the
  network in tests (EODHD defaults to "demo"; DeepSeek has no key).
- `THEMES_ENABLED` defaults to True in production but conftest sets it to false;
  `PIPELINE_MIN_RANKABLE_THEMES` is 1 in tests.
- Copy (English UI / Chinese reports): UI copy is English sentence case; Feishu/report text is
  Chinese with full-width punctuation; stage enums are English lowercase in data and are mapped
  to 萌芽/加速/主线/拥挤/退潮/中性/成员不足 only when rendering Chinese.

## 1. Already provided by the foundation (do not redo)

| Piece | Location |
|---|---|
| ORM models (13 tables) | `models.py` tail: Theme, ThemeMember, ThemeMemberVerification, EtfHoldingSnapshot, NewsIngestDay, NewsItem, TickerAttentionDaily, ThemeDaily, ThemeMemberDaily, CompanyTrendDaily, ThemeBrief, ThemeAlert, ThemeProposal |
| Migration + drift test | `migrations/versions/0023_theme_trends.py`, `tests/test_theme_migration.py` |
| Settings | `core/config.py` "Trending themes" block (THEME_*) |
| EODHD wrappers | `eodhd_client.get_news(...)` (market feed when no ticker/tag; 5 calls/page), `eodhd_client.get_etf_data(etf)` → `{"holdings": [...], "inception_date"}` (10 calls) |
| DeepSeek JSON | `deepseek_client.generate_deepseek_json(..., thinking: bool|None, max_tokens: int|None)` |
| Taxonomy | `services/themes/taxonomy/themes_v1.yaml`, `services/themes/taxonomy.py` (`load_taxonomy()`, `sync_theme_taxonomy()`, `Taxonomy.proxy_etfs()`) |
| Universe | `services/themes/universe.py` `load_theme_universe(db, as_of) -> (snapshot_date, {ticker: CompanyProfile})` |
| Shared types | `services/themes/types.py` (MemberSpan, ThemeMeta, ScoringInputs, ScoringResult, STAGES) |
| API schemas | `api/schemas.py` Theme* / TrendingCompan* models (the JSON contract) |
| Stubs | every module in `services/themes/` with the public signatures below |

Measured provider facts (2026-10-10, owner key): market-wide `/api/news` (no `s`/`t`) works;
`limit=1000` costs 5 calls; ~1,900 articles on a weekday; offset ≥ 200,000 works.
`fundamentals/{ETF}?filter=ETF_Data` = 10 calls, holdings keyed by provider row with
`Code`, `Exchange` ("US" for US listings), `Name`, `Assets_%`; swap-based funds (DRAM)
omit swap legs (weights sum < 100%) — seeds cover this. Money-market rows (FGXXX) and
non-US listings must be dropped by intersecting with the universe.

## 2. Data semantics

- Tickers: canonical `CODE.US` everywhere. ETF holding `Code` + `Exchange == "US"` → `f"{Code}.US"`;
  class shares use a hyphen (`BRK-B.US`); map `.`/`/` class separators to `-` before canonicalizing.
- `theme_members`: intervals; active on `d` iff `valid_from <= d < valid_to` (None = open).
  Tier weights: exposure 3 → core 1.0; exposure 2 → core 0.75; exposure 1 → peripheral 0.4;
  pins → core 1.0 (`review_status="pinned"`); bans never members. ETF-only candidates that the LLM
  did not verify (status error/rejected is NOT etf_only) — only when verification is unavailable
  (no DeepSeek key / provider failure) may an ETF holding with weight ≥ 2% become
  `peripheral` 0.4 with `review_status="etf_only"`. Seeds that fail verification are still kept as
  `peripheral` with `review_status="seed_unverified"` (owner curated them).
- `news_ingest_days.day` and `ticker_attention_daily.day` are **UTC calendar days** of the article
  timestamp. A day is `complete` only after the whole UTC day has passed and every page was fetched
  (no None page, not truncated). `ticker_attention_daily` stores only non-zero rows; for complete
  days, absent rows mean zero.
- `article_count` counts deduplicated articles whose US-linked tickers include the ticker;
  `focused_count` counts only articles with ≤ `THEME_NEWS_FOCUSED_MAX_SYMBOLS` US tickers.
  Dedupe: sha256 of the normalized link (lowercase scheme/host, strip query/fragment);
  then drop exact duplicate normalized titles within the same day.
- Publications (dataset names): `theme_membership` (as_of = membership refresh session),
  `theme_etf_prices` (target session), `news_attention` (target session, published when every
  UTC day from target−6 through target is complete), `theme_trends` (target session).
  One `theme_trends` publication per session; rows carry `source_run_id`; readers join
  `DataPublication(dataset, as_of_date) .pipeline_run_id == ThemeDaily.source_run_id` and
  `PipelineRun.status == "published"`. Re-running the same date sweeps superseded rows for that
  date. Backfill runs write many dates under one run and publish each date.

## 3. Module contracts

### 3.1 `services/themes/news_ingest.py` (owner: news agent)
- `ingest_market_news(target: date, *, max_backfill_days=None) -> dict`
  - Runs as pipeline `news_attention` (begin/finish run; record counts in quality_report).
  - Days to fetch (UTC): (a) the last `THEME_NEWS_RECENT_REFRESH_DAYS` complete UTC days up to
    `min(target + 1 day, utc_today - 1 day)` (always refetched to absorb late articles);
    (b) older missing/incomplete days back to `THEME_NEWS_BACKFILL_START`, newest first.
    Missing days of the attention window (`target-6 ..`) behind the recent range are always
    planned (at most 5 days with the default 3-day refresh; they fall between two recent ranges after a skipped run, e.g. the
    Tuesday after a Monday holiday, or a worker outage longer than the refresh span);
    `max_backfill_days if not None else THEME_NEWS_BACKFILL_DAYS_PER_RUN` caps only the days
    older than the window, so a budget of 0 stops history backfill but still publishes.
  - Per day: page `get_news(from_date=day, to_date=day, limit=1000, offset=k*1000)` until a page
    has < 1000 rows or `THEME_NEWS_MAX_PAGES_PER_DAY` (then `truncated=True`, status partial).
    Any None page → status `failed` for that day (keep previous complete data). Keep a dedicated
    `httpx.AsyncClient(timeout=settings.THEME_NEWS_TIMEOUT_SECONDS)` via `eodhd_client`'s client
    factory semantics (trust_env). Catch `EODHDQuotaExceeded` explicitly: stop, mark run failed,
    re-raise.
  - Normalize each article: `published_at` naive UTC from the ISO `date`; `day` = its UTC date
    (articles whose UTC date ≠ requested day are counted on their own day only if that day is
    being ingested in this run; otherwise ignored); US tickers = symbols ending `.US`
    (canonicalized, uppercase); title stripped; link via `news_fetcher._safe_article_link`.
  - Per day, in one transaction: persist a trimmed raw snapshot
    (`persist_snapshot(db, "EODHD", "news_market_feed", rows, as_of_date=day, details={...})`,
    rows = {date, title, link, symbols, tags} sorted by (date, link), no content);
    replace `ticker_attention_daily` rows for the day; upsert `news_items` (only if the day is within
    `THEME_NEWS_RETENTION_DAYS` of utc_today); upsert `news_ingest_days`.
  - Delete `news_items` older than retention.
  - Publish `news_attention` for `target` when all UTC days `target-6 .. target` are complete.
  - Return `{"status": "published"|"partial"|"failed", "days_fetched", "days_complete", "calls_estimate", ...}`.
- `load_attention(db, start_day, end_day, tickers=None) -> AttentionData` — counts frame indexed by
  every UTC day in range (ascending `date`), columns = requested tickers (or all seen), zero-filled
  for complete days, NaN rows for non-complete days; `totals` = `articles_us` for complete days
  (NaN otherwise); `complete_days` set.
- Tests `tests/test_theme_news.py`: paging stop/truncation, None page → failed day not published,
  dedupe (link + title), UTC day assignment, retention delete, counts replaced on re-ingest,
  publication only when 7 days complete, quota error propagation, token never logged
  (`caplog` with a sentinel `eodhd_client.EODHD_API_KEY`), `load_attention` NaN vs zero semantics.

### 3.2 `services/themes/membership.py` + `services/themes/etf_prices.py` (owner: membership agent)
- `refresh_theme_membership(as_of: date) -> dict` (pipeline `theme_membership`):
  1. `sync_theme_taxonomy()`; load active themes from DB.
  2. Universe via `load_theme_universe(db, as_of)`; if empty → return `{"status":"deferred","reason":"no-screener"}`.
  3. Holdings: for every distinct proxy ETF, `get_etf_data` (shared client, semaphore 2); persist raw
     snapshot ("EODHD","etf_holdings", details {etf}); write `etf_holdings_snapshots`
     (`as_of_date=as_of`, `source="eodhd"`, `holding_key` = `f"{Code}.{Exchange}"` or name,
     `holding_ticker` canonical when Exchange=="US", `weight_pct` float, `inception_date`).
     A failed ETF only drops that ETF's candidates (warning), never the run.
  4. Candidates per theme (only universe tickers, never bans): seeds ∪ pins ∪ ETF holdings with
     weight ≥ `THEME_MEMBERSHIP_MIN_ETF_WEIGHT_PCT` ∪ keyword hits (description has ≥ 2 distinct
     keywords matched with word boundaries, case-insensitive, and no negative keyword), keyword
     channel capped at `THEME_MEMBERSHIP_MAX_KEYWORD_CANDIDATES` by hit count then market cap.
  5. Verification (DeepSeek JSON, `thinking=False`, batches of `THEME_MEMBERSHIP_BATCH_SIZE`,
     `asyncio.Semaphore(THEME_MEMBERSHIP_CONCURRENCY)`, `asyncio.wait_for(...,
     THEME_MEMBERSHIP_LLM_TIMEOUT_SECONDS)`, ≤ 3 attempts on Timeout/ValueError/ValidationError).
     System prompt is byte-identical per theme (prefix caching): instructions + theme JSON
     (slug, name_en, definition, keywords). User prompt: candidates [{ticker, name, description}].
     Output `{"results":[{"ticker","exposure":0-3,"role":"pure_play|supplier|adopter|tangential",
     "evidence_quote","confidence"}]}` validated with pydantic. Hard checks: ticker in batch, no
     duplicates, `evidence_quote` (whitespace-normalized, lowercased) is a substring of the
     normalized description, else status `invalid_quote`; exposure 0 → `rejected`.
     Cache in `theme_member_verifications` keyed by `input_hash = sha256(prompt_version, model,
     theme slug, definition, ticker, description)`; reuse cached rows, never re-ask.
     If `DEEPSEEK_API_KEY` is empty, skip LLM (use the etf_only/seed fallbacks from §2) and warn.
  6. Diff against open intervals: close removed members (`valid_to = as_of`), insert new
     (`valid_from = as_of`); a tier/weight/evidence change closes and reopens. Pins/bans apply.
  7. Publish `theme_membership` for `as_of` with quality report (themes, members per theme,
     verified/rejected/invalid counts, etf failures). Gate: at least one theme with ≥ 1 member.
- `load_member_spans(db, *, slugs=None) -> list[MemberSpan]` (all intervals, open and closed, of
  non-retired themes).
- `load_proxy_inception(db) -> {etf: inception_date|None}` from the latest holdings snapshot per ETF.
- `etf_prices.refresh_theme_etf_prices(target) -> dict`: every `Taxonomy.proxy_etfs()` ticker +
  "SPY.US" not already covered, via `history_backfill.backfill_price_history(list, history_days=352,
  target_date=target, include_corporate_actions=False, include_target_session=True,
  publication_dataset=THEME_ETF_PRICE_DATASET, publish_when_complete=True,
  minimum_ticker_coverage=0.80, required_sessions_by_ticker=...)` with required sessions only
  after each ETF's known inception (from `load_proxy_inception`; None → all). ETFs go into `tickers`
  only (never security_master).
- Tests `tests/test_theme_membership.py`: candidate channels, keyword word boundaries/negatives,
  fabricated quote rejected, cache reuse (LLM not called twice), no-key fallback, interval
  diffing (close/reopen/unchanged), pins/bans, ETF failure tolerance, holding ticker mapping
  (class shares, non-US dropped), etf_prices required-session logic (patch backfill_price_history).

### 3.3 `services/themes/scoring.py` (owner: scoring agent) — pure

`score_themes(inputs: ScoringInputs) -> ScoringResult`, `SCORING_VERSION = "theme-heat-v1"`.
Follow §Scoring below exactly. No DB/network imports. Vectorize with pandas/numpy; it must handle
~3,000 tickers × 260 sessions in < 10 s on a laptop. Tests `tests/test_theme_scoring.py` with
synthetic frames: basket weights/capping, log excess returns, B50/NH/ΔB50, ignition counts, RVOL,
attention baseline/degradation (missing days, total band), robust z clipping and MAD=0 path, EWMA
half-life (step input), ranks/rank_change/streak, ts_z min obs, stage priorities + 3-day hold +
immediate Emerging, crowded via proxy inception, insufficient members, PIT flag, member/company
outputs and NaN-safe leader scores, determinism, performance smoke test.

### 3.4 `services/themes/pipeline.py` (owner: pipeline agent, wave 2)
`refresh_theme_trends(target, *, backfill_sessions=0) -> dict`:
1. Normalize target to a session. Dependencies (exact date via a `_published_run_for_date` copy):
   `screener` and `price_history` for target; `theme_membership` published on/before target
   (any date) — else `{"status":"deferred","reason":"missing-publications","missing":[...]}`.
2. Skip if an exact-date `theme_trends` publication with rows exists, unless
   `backfill_sessions > 0` or the existing run's quality metrics say `attention_degraded` and
   `news_attention` is now published for target (one re-run).
3. Load: universe (`load_theme_universe`), spans (`load_member_spans`), proxy inception,
   sessions = last `260 + backfill_sessions` sessions through target, prices for universe ∪
   member tickers ∪ SPY.US ∪ proxy ETFs in 500-ticker chunks selecting
   (ticker, date, adjusted_close, close, volume) with `type_coerce(..., Float)`; attention via
   `load_attention` for UTC days (first session − 110 days) .. target.
4. `begin_pipeline_run("theme_trends", target, version=SCORING_VERSION)`; compute
   `score_themes` in `asyncio.to_thread` with `output_sessions = max(1, backfill_sessions)`.
5. Quality gate (QualityReport/DataQualityError): ≥ `PIPELINE_MIN_RANKABLE_THEMES` rankable themes
   at target; ≥ `PIPELINE_MIN_THEME_PRICE_COVERAGE` of active member tickers priced on target;
   no NaN heat for rankable rows; ranks unique. Attention problems → warning +
   `metrics.attention_degraded=True`, never an error.
6. One write transaction: insert theme/member/company rows (chunks ≤ 500), publish
   `theme_trends` for target (and for every backfilled date) via `publish_dataset`/
   `publish_datasets_and_finish`, sweep superseded same-date rows.
7. Return `{"status":"published","run_id","as_of_date","rankable",...}`.
Also owns: `core/scheduler.py` wiring (see §5), `services/themes/bootstrap.py`,
`scripts/backfill_theme_trends.py` (news backfill to THEME_NEWS_BACKFILL_START + trends backfill of
`THEME_BACKFILL_SESSIONS`), `.env.example` entries, tests `tests/test_theme_pipeline.py` and
`tests/test_theme_scheduler.py`.

### 3.5 `services/themes/serving.py` + API routes (owner: API agent)
Implements the stubs; returns dicts that validate against `api/schemas.py`.
- Publication selection: latest published `theme_trends` (or exact `as_of_date` → 404-style
  `ThemeTrendsUnavailable` if absent). `meta.stale`: compare to the expected session computed in
  America/New_York with a 06:00 cutoff (before 06:00 ET use yesterday as the reference) via
  `latest_completed_us_session`. `point_in_time` = all rows at as_of have `pit=True`;
  `attention_degraded`/`coverage_pct`/`warnings` from the run's quality_report.
- Conversions: `excess_*d = exp(x) - 1`; `proxy_excess_21d = exp(proxy_x21) - 1`; member
  `excess_*`, company `excess_5d`, `excess_5d_vs_sector` likewise; `near_high_ratio = pth`;
  `relative_volume_5d = exp(rvol5)`; `pct_above_ma50 = b50` (fraction), `near_high_share = nh`,
  `pct_above_ma50_change_10d = d_b50`; `ignitions_today = ign`, `ignitions_5d = ign5`.
- `heat_sparkline`: last 60 `heat_now` values for the theme across published dates ≤ as_of
  (one row per date from that date's publication).
- History: for each published date (≤ as_of, last `days`), the theme row of that date's run;
  `basket_index`, `benchmark_index`, `proxy_index` normalized to 1.0 at the first returned date —
  basket from `basket_index`; benchmark/proxy from local `daily_prices` (SPY.US / proxy ETF
  adjusted close). Detail page embeds 120 days.
- Members: `theme_member_daily` rows of the selected run joined to `theme_members` open on as_of
  (tier, exposure, role, review_status, sources, evidence) and names from the universe/tickers.
  Sort: is_leader desc, leader_rank, weight desc, ticker.
- Brief: `briefs.latest_theme_brief(db, theme_id, as_of)` (stale if its date < as_of); tolerate
  NotImplementedError → None until the brief agent lands.
- List filters: `view` (now → sort by rank_now; trend → rank_trend; unrankable last),
  `stage` filter; summary counts; `top_riser` = slug with the largest positive rank_change.
- Proposals: list (default pending first, newest last_seen first) and decide (accepted/rejected,
  `decided_at`; accepted returns a YAML snippet for `themes_v1.yaml` using the suggested slug,
  labels, rationale as definition and tickers as `seed_core`).
- Routes (single router in `api/routers.py`, contiguous block after `/api/v1/watchlist/quotes`):
  `require_theme_access` dependency (THEMES_PUBLIC → open, else `require_admin_api_key`),
  `tags=["Themes"]`:
  `GET /api/v1/themes?as_of_date&view&stage`, `GET /api/v1/themes/{slug}?as_of_date`
  (slug `Path(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")`), `GET /api/v1/themes/{slug}/history?days`
  (20..504, default 252), `GET /api/v1/trending-companies?as_of_date&limit` (1..200, default 50),
  `GET /api/personal/themes/proposals?status`, `POST /api/personal/themes/proposals/{id}/decision`
  (admin). 503 ↔ ThemeTrendsUnavailable, 404 ↔ ThemeNotFound/ThemeProposalNotFound.
- Tests `tests/test_theme_api.py` (TestClient + `X-API-Key: test-secret`): 401/503/404/422,
  stale flag, conversions, history alignment, PIT flag, no external calls (patch
  `eodhd_client._fetch_from_eodhd`, `deepseek_client._create_client`, `httpx.AsyncClient.send` to raise).

### 3.6 Frontend (owner: frontend agent)
- `lib/api.ts`: types mirroring `api/schemas.py` Theme* (snake_case) and fetchers
  `fetchThemes({asOfDate?, view, stage?}, adminKey, signal?)`, `fetchThemeDetail(slug, adminKey,
  asOfDate?, signal?)`, `fetchThemeHistory(slug, days, adminKey, signal?)`,
  `fetchTrendingCompanies({asOfDate?, limit?}, adminKey, signal?)`,
  `fetchThemeProposals(adminKey, status?, signal?)`, `decideThemeProposal(id, decision, adminKey)`.
- Nav: `{ name: "Themes", path: "/themes" }` in `NAV_PAGES`; TopNavBar active check prefix-matches.
- `/themes` page: locked state (no fetch) with "Unlock personal workspace" using
  `usePersonalWorkspace` + `PersonalUnlockDialog`; header with as-of chip, stale/quality banners,
  "Backfilled · not point-in-time" badge when `!meta.point_in_time`, degraded-attention note;
  KPI strip (Emerging / Accelerating / Leading / Crowded / Fading counts, top riser);
  `SegmentedControl` Now (5D) / Trend (20D); stage filter; tabs Themes | Trending companies |
  Proposals (proposals visible when unlocked). Themes table: Rank (Δ) · Theme (EN, ZH muted) ·
  Stage chip · Heat bar + value · 60D heat sparkline · P/B/V/A mini bars · 5D/21D excess ·
  % > MA50 · Ignitions 5D · Streak · Leaders (ticker links `/?ticker=`) · Proxy ETF 21D.
  View toggle Table / Treemap (ECharts treemap: area = n_priced, color = heat via a new
  `chartTheme` diverging scale; no hex outside chartTheme.ts) / Rotation (RRGChart over proxy
  ETFs via `/api/v1/rrg`, warn on missing tickers).
- `/themes/[slug]` page: definition, stage chip, KPIs, charts (heat now/trend + stacked components;
  basket vs SPY vs proxy; B50/NH with ignition bars), brief card ("Why it's moving", Chinese text,
  evidence list with links; stale label), members table (tier, ticker, name, weight, why in theme
  (quote + source + as-of), 5D/21D, near high, RVOL, news z, leader, first ignition, sparkline).
- Status bar: `setStatus([{label:"Themes", value: as_of, tone}], `scoring ${version}`)` + clear on unmount.
- Tests: page tests with `importOriginal` spread mocks and `src/test/themesFixture.ts`; locked state
  no fetch; 401 relocks; missing values "—"; stage filter; detail page; e2e `frontend/e2e/themes.spec.ts`
  with catch-all `**/api/**` mocks, seeded session key, pageerror and `.app-page` overflow checks.
  designTokens test must pass.

### 3.7 Briefs, alerts, proposals, report section (owner: reports agent, wave 2)
- `briefs.generate_theme_briefs(target)`: for the target publication's top `THEME_BRIEF_TOP_N`
  rankable themes by rank_now plus any with `emerging_first_20` or `stage_changed`: build evidence
  (E1.. metric facts from theme_daily/member rows with pre-formatted signed `display` strings and
  ISO dates; then up to 12 `news_items` from the last 3 UTC days mentioning leaders/core members,
  deduped), hash (`THEME_BRIEF_PROMPT_VERSION`, model, slug, as_of, evidence), skip if cached,
  generate Chinese text with sections `**盘面事实**`, `**同期新闻**`, `**风险与反方**`, one sentence
  per bullet ending with exactly one `[E#]`, no causal claims from headlines ("同期新闻包括"),
  metric codes instead of "21日". Validate with a theme-specific section/citation validator +
  `ai_assistant.validate_evidence_numbers`; ≤ 3 attempts with repair; only validated output is
  inserted (IntegrityError → adopt winner). `latest_theme_brief(db, theme_id, as_of)` returns the
  newest brief with date ≤ as_of as a dict matching `ThemeBriefResponse` (minus `stale`, which
  serving sets) or None. Disabled when `THEME_BRIEFS_ENABLED` false or no DeepSeek key.
- `alerts.run_theme_alerts(reference_date=None)`: only for a publication whose as_of equals the
  latest completed session; events = themes with `emerging_first_20` (and no emerging alert in the
  last 20 sessions) + top-10 rank_now themes whose stage changed into crowded/fading. One Chinese
  digest via `NotificationManager.broadcast` (generic card); record `theme_alerts` only after a
  successful send (`on_conflict_do_nothing`); statuses alerted / already-delivered / no-alerts / skipped.
- `proposals.refresh_theme_proposals(target)`: candidates = universe tickers not in any open
  membership with ≥ 2 sessions in the last 10 where (return ≥ +5% and rvol1 ≥ 2) or attention a* > 3
  (reuse scoring helpers via public functions if the scoring agent exposes them; otherwise compute
  locally from daily_prices/attention); cluster by 60-session return correlation ≥ 0.5 (connected
  components, ≥ 3 tickers); DeepSeek names each cluster (label_en, label_zh, slug_suggestion,
  rationale citing [E#] headlines); merge with existing pending proposals by ticker Jaccard ≥ 0.5
  (update last_seen/hit_count/tickers); expire pending proposals not seen for 20 sessions.
- Morning report: `report_market.collect_theme_heat(now)` (latest publication ≤ report date, JSON-safe
  dict: as_of, expected_as_of, ranked, degraded, pit, top 5 [name_zh, slug, rank, rank_change,
  stage, heat, excess_21d_pct, b50_pct, leaders], emerging, stage_changes) wired in
  `daily_reporter._generate_and_broadcast` for `morning_briefing` only (Option B in the recon),
  rendered by `report_renderer.render_theme_section` inside `render_market_sections` (morning only,
  after the breadth block), `quality_summary` gap handling only when the key is present,
  `"题材热度"` added to `report_card._MARKET_SECTIONS`, `REPORT_RENDERER_VERSION` →
  `"cross-asset-v2.3"` (+ docs/daily_report_v2/README.md and deploy assertions).
- Tests: `tests/test_theme_briefs.py`, `tests/test_theme_alerts.py`, `tests/test_theme_proposals.py`,
  plus report tests extended (stale matrix, morning-only, card routing).

## 4. Scoring

All inputs are pandas objects indexed by ascending session `date` with canonical tickers as columns.

Constants: tier weights (1.0 / 0.75 / 0.4) come from MemberSpan.weight; MAX_MEMBER_WEIGHT = 0.15;
MIN_RANKABLE_MEMBERS = `settings.THEME_MIN_RANKABLE_MEMBERS` read at call time (pass via inputs
or read settings inside the function — do not freeze at import); RETURN_CLIP = 0.5;
IGNITION_RETURN = 0.05; IGNITION_RVOL = 2.0; NEAR_HIGH = 0.98; W_P = (x21 .5, x63 .3, x5 .2);
W_B = (B50 .4, NH .3, dB50 .3); W_H = (P .40, B .30, V .15, A .15); half-lives 5 and 20 sessions;
Z_CLIP = 3; ATT_CLIP = 4; ATT_MAD_FLOOR = 0.3; attention baseline = calendar days d−97..d−8 with
≥ 60 observations; ATT_TOTAL_BAND = (0.4, 2.5); TS_Z_MIN_OBS = 60; STAGE_HOLD = 3.

Robust cross-sectional z: (v − median)/(1.4826·MAD); MAD = 0 → use std; both 0 → 0; clip ±3.
Computed per date over the **comparison set** = rankable themes ∪ industry baskets (industries
from `inputs.industries` with ≥ 8 priced universe members, equal weight). Only themes are emitted.

Basket per theme/date: active members (span active, close at d and d−1); weights capped at 15% of
the total and renormalized iteratively (≤ 10 passes); r_T,d = Σ w·clip(r, ±0.5)/Σ w;
I_T = cumprod(1 + r_T) (basket_index, starts at 1.0 on the first window session);
x_k = ln(I_d / I_{d−k}) − ln(SPY_d / SPY_{d−k}), k ∈ {5, 21, 63, 126}. If a theme has no span
with valid_from ≤ d, use the spans active on as_of for date d and set `pit=False` for that row;
otherwise `pit=True`.

Components:
- P = Σ W_P·z(x_k).
- B50 = Σw·1[close > MA50]/Σw (MA50 needs 50 obs); NH = Σw·1[close ≥ 0.98·rolling_max(close, 252,
  min_periods=126)]/Σw; dB50 = B50_d − B50_{d−10}; B = Σ W_B·z(·).
- rvol1 = vol_d / mean(vol over the prior 20 sessions); ignited = r ≥ 0.05 ∧ rvol1 ≥ 2;
  IGN = #ignited members today; IGN5 = #distinct members ignited in the last 5 sessions.
- rvol5 = ln(mean(vol, last 5) / median(vol, last 60)); V = z(weighted median rvol5).
- Attention (per ticker, session d): c7 = Σ counts over UTC days d−6..d; ℓ = ln(1+c7);
  a = (ℓ_d − median ℓ over days [d−97, d−8]) / max(1.4826·MAD, 0.3) (≥ 60 non-NaN obs else NaN);
  a* = clip(a − median_j a_j, ±4) over all tickers with non-NaN a; ATT = Σw·a*/Σw;
  AB = Σw·1[a* > 2]/Σw; A = 0.6 z(ATT) + 0.4 z(AB).
  `attention_degraded(d)` = any non-complete day in d−6..d, or the 7-day total outside
  [0.4, 2.5] × its trailing 60-day median of 7-day totals, or < 50% of the theme's members have a*.
- H_raw = 0.40P + 0.30B + 0.15V + 0.15A; degraded → (0.40P + 0.30B + 0.15V)/0.85 and
  score_attention = None.
- heat = 100/(1+exp(−H_raw)); heat_now/heat_trend = EWMA with λ = 0.5^(1/h), seeded with the first
  heat; NaN heat leaves the EWMA unchanged (carry).
- rank_now/rank_trend over rankable themes (1 = hottest; ties by slug); rank_change =
  rank_now(d−1) − rank_now(d) (None if either missing); streak_top5 = consecutive sessions with
  rank_now ≤ 5; pct_now/pct_trend = percentile rank (0..1, highest = 1) of heat_now/heat_trend over
  the comparison set.
- ts_z = (H_raw_d − median of prior ≤ 252 H_raw)/(1.4826·MAD), ≥ 60 prior obs.
- cocorr: residual daily returns vs SPY (rolling 120-session beta, min 60); mean pairwise
  correlation of members' residuals over the last 60 sessions (≥ 3 members);
  cocorr_pct = percentile within its own prior ≤ 252 values (≥ 60 obs).
- proxy: first proxy ETF with prices → proxy_etf, proxy_x21 (log excess vs SPY).

Flags and stages:
- emerge = ts_z ≥ 2 ∧ pct_now(d−21) < 0.5 ∧ ign5 ≥ 3 ∧ d_b50 > 0.10 ∧ x21 > 0.
- attention_only = z(ATT) ≥ 2 ∧ P < 0 (flag, never a stage).
- candidate stage priority: 1 crowded = (leading_cond ∨ accel_cond) ∧ ((cocorr_pct ≥ 0.8 ∧ x63 in
  the top quartile of the comparison set) ∨ proxy_inception within 365 days before d);
  2 leading = ≥ 15 of the last 20 sessions with pct_trend ≥ 0.75 ∧ b50 ≥ 0.6;
  3 emerging = emerge in the last 10 sessions ∧ pct_trend < 0.75;
  4 accelerating = heat_now > heat_trend + 5 ∧ P > 0.5 ∧ B > 0;
  5 fading = heat_now < heat_trend − 5 ∧ heat_now ≤ max(heat_now, last 20) − 15 ∧ d_b50 < 0;
  6 neutral. Not rankable → "insufficient".
- stage (displayed) switches to a new candidate only after it held for 3 consecutive sessions;
  switching into "emerging" is immediate; the series starts at the first candidate.
  stage_prev = previous session's stage; stage_changed = stage ≠ stage_prev;
  emerging_first_20 = stage == "emerging" ∧ stage_changed ∧ no "emerging" stage in the previous
  20 sessions.
- leaders (theme row JSON) = top-3 member tickers by leader_score at d.
- flags JSON: {"insufficient_members": bool, "attention_degraded_reasons": [...], ...}.

Members (as_of only): close, r1, x5, x21 (stock log excess vs SPY), pth = close/rolling max 252
(min 126), rvol5, att_z = a*, ignited, first_ignition_date (earliest ignition in last 63
sessions), leader_score = 0.4·pct_T(x21) + 0.3·pct_T(pth) + 0.3·pct_T(a*) (missing components drop
out, weights renormalize), leader_rank (1 = best), is_leader = rank ≤ 3, sparkline = last 60
adjusted closes / first value (NaN → None), tier, weight (capped normalized weight).

Companies (as_of only): eligible = universe tickers with close ≥ $3 and median 20-session dollar
volume ≥ $5M; CT = 0.35·z(x5 − sector median x5) + 0.25·z(rvol5) + 0.20·z(a*) + 0.20·z(pth) over the
eligible set (missing → drop & renormalize); top 50 by CT: rank, score, name, sector, industry,
close, market_cap, r1, x5, x5_sector (x5 − sector median), rvol5, att_z, pth, theme_slugs (active
memberships on as_of), flags (["attention_only"] when a* > 2 ∧ x5 < 0; ["ignited"] when ignited today).

## 5. Jobs (core/scheduler.py, America/New_York), gated by `settings.THEMES_ENABLED`

| Time | Days | Job id | Function |
|---|---|---|---|
| 04:35 | tue-sat | daily_theme_etf_prices | refresh_theme_etf_prices(target) |
| 04:40 | tue-sat | daily_theme_news_sync | ingest_market_news(target) |
| 04:50, 07:50 | tue-sat | daily_theme_trends_sync | refresh_theme_trends(target) |
| 05:05 | tue-sat | daily_theme_briefs | generate_theme_briefs(target) if THEME_BRIEFS_ENABLED |
| 05:15 | tue-sat | daily_theme_alerts | run_theme_alerts() if THEME_ALERTS_ENABLED |
| 08:30 | sat | weekly_theme_proposals | refresh_theme_proposals(target) if THEME_PROPOSALS_ENABLED (after the 07:50 trends retry) |
| 05:00 | sun | weekly_theme_membership_refresh | sync_theme_taxonomy + refresh_theme_membership(as_of) |
| start + 120 s | once | theme_bootstrap | bootstrap_themes() |

`target = latest_completed_us_session(datetime.now(ny_tz).date())`. Each wrapper uses its own
`asyncio.Lock` and a skip-if-published guard; import service functions by name into
`core.scheduler` so tests can patch them. `bootstrap_themes`: taxonomy sync → membership if none
published or older than `THEME_MEMBERSHIP_MAX_AGE_DAYS` → ETF prices → news (bounded) → trends for
the latest target if not published. `bootstrap_themes` never sends alerts; the scheduler wrapper then
runs the briefs/alerts follow-ups whose slot already passed. `services/catchup.py` is NOT changed.

## 6. As built (deviations recorded during implementation, 2026-10-11)

These supersede the sections above where they differ.

- **News (§3.1).**
  - An article whose UTC date differs from the requested day is ignored, never counted on its own day. A link counts on one day only.
  - A refetched day's `news_items` are replaced, not upserted.
  - Titles are HTML-unescaped and whitespace-normalized for storage and dedupe. Raw lineage keeps the provider text.
  - Guards:
    - A refetch that shrinks a complete day by more than 50% keeps the previous data.
    - Consecutive unavailable days stop the run early.
    - A run with no successful day is `failed`.
- **Membership (§3.2).**
  - A failed ETF fetch reuses stored holdings up to 35 days old before dropping that ETF's candidates.
  - Fund inception dates are stored as a `__fund__` row in `etf_holdings_snapshots`; readers must ignore `membership.FUND_METADATA_HOLDING_KEY`.
  - A refresh with an `as_of` older than `THEME_MEMBERSHIP_MAX_AGE_DAYS` returns `skipped/as-of-too-old`.
  - Keyword matching tolerates a plural suffix.
  - Verified members' evidence source and date move to the latest confirming run.
- **Scoring (§4).**
  - `score_themes(inputs, *, published=...)` pins stage history to published rows before the output window (the pipeline passes the last 30 sessions). Without it, "previous session" values come from the run's own window. With `pin_output_dates=True` rows inside the output window pin too (never the as-of session); the pipeline uses it for the dates a backfill keeps.
  - `insufficient` displays immediately but does not reset the 3-session hold; once the theme is rankable again it resumes from the last displayed stage.
  - `emerging_first_20` additionally requires an `emerging` candidate that session.
  - MA50 is the mean of the last 50 observations, so gaps are tolerated.
  - Benchmark gaps are reported in diagnostics.
  - Memberless themes are never attention-degraded.
- **Pipeline (§3.4).**
  - `refresh_theme_trends(..., overwrite=False)`: a backfill keeps earlier dates already published by a daily run (those runs hold the only member and company rows for their date) unless `overwrite=True`. The kept dates' served rows pin their sessions, so the dates the backfill publishes continue them; the run fails if the kept dates change before its write.
  - `quality_report.metrics.dates[<iso>]` holds each published date's own facts (`published_date_facts`). Serving reads these for `meta.attention_degraded`, `coverage_pct` and `warnings`.
  - `metrics.coverage_pct` is in percent (0–100); `metrics.member_price_coverage` is the 0–1 fraction.
  - The run-level `attention_degraded` considers rankable rows only.
- **Scheduler (§5).**
  - After `theme_trends` publishes (or is already published), the trends job runs briefs and alerts whose own slot already passed for that target.
  - The 05:15 alerts job defers while a 07:50 attention re-run is pending, so it never alerts from a publication about to be replaced.
  - `services/catchup.py` is unchanged.
- **Serving (§3.5).**
  - `point_in_time` ignores memberless and retired themes.
  - History steps that cannot be measured return `None`, assume the basket moved with SPY across the gap, and add a warning.
  - An accepted proposal's slug may carry a numeric suffix so it never collides with an existing slug.
- **Briefs (§3.7).**
  - Evidence items carry one display value each.
  - The validator adds rules for metric words, ticker binding, sign, plain text only and no headline numbers outside a verbatim quote. A verbatim copy of the cited headline (quoted or not) is one opaque span for the sentence and number checks.
  - Over-long sections are truncated to their caps rather than rejected.
  - When three attempts fail, a deterministic template brief built from evidence labels is validated and stored with `model="template"`. The UI labels it, and the next run retries the LLM.
  - The UI renders only `#theme-evidence-E#` links from brief text.
- **Proposals (§3.7).**
  - An attention session counts toward the 2-of-10 rule only if it is at least 7 days from every other counted attention session and from every ignition.
  - A named proposal whose tickers drift loses its name if renaming fails.
