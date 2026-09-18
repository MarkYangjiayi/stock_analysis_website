# Index-level valuation: S&P 500 historical P/E

The Market tab exposes **Valuation → S&P 500 Historical P/E**
(`/market/index-valuation`): the index's month-end aggregate price-to-earnings
from `INDEX_VALUATION_HISTORY_START` (default 2016-01-01) to the latest
published session, served by `GET /api/v1/index-valuation?universe=SP500`.

Two variants are always shown together:

- **index_pe (all members)** = Σ member equity ÷ Σ member TTM reported
  earnings, where loss-makers stay in the denominator with their negative
  contribution. This is the whole-index reading.
- **index_pe_earners (earning companies only)** repeats the ratio over
  profitable companies, plus the median of company-level P/Es. This isolates
  what the multiple looks like without loss-makers dragging the aggregate.

## Pipeline

`services/index_valuation.py` recomputes and republishes the **full month-end
series every run** (self-healing against restatements, which only become
effective after their recorded availability date):

1. Dependency gate: the day's `price_history` and `universe_history`
   publications must exist; otherwise the run defers.
2. Every ever-member of the S&P 500 since the history start is reconstructed
   with the per-stock `build_valuation_history` engine at month-end sampling
   (`interval="1mo"`), inheriting all of its gates. Prices are capped at the
   target session (`through`), so a database carrying later sessions can
   never leak future prices or a future split reference into the series.
3. Members are grouped into **companies** before aggregation (below), then
   each completed month-end label is aggregated independently; the running
   month is never published with a future label.
4. Quality gate: months with fewer than `PIPELINE_MIN_SP500_SIZE` companies or
   company coverage below `PIPELINE_MIN_INDEX_VALUATION_COVERAGE` become
   gaps with the reason stored on the month (and aggregated in the quality
   report); if fewer than `INDEX_VALUATION_MIN_MONTH_COVERAGE` of all months
   are valid, the run fails and publishes nothing.
5. Publication follows the market-breadth pattern: one transaction inserts
   `index_valuation_snapshots`, publishes the `index_valuation` dataset, and
   retains only the five most recent runs.

Scheduling: `core/scheduler.py` runs `scheduled_index_valuation_sync` Tue–Sat
04:15 America/New_York (after breadth), and `services/catchup.py` recovers the
latest session after downtime. Forward P/E is intentionally absent: EODHD
consensus estimates are current-only and archived expectations do not exist,
so a forward history cannot be reconstructed honestly.

## Membership and company grouping

- Membership is **point-in-time** and evaluated at each month's **final
  trading session**, never at a holiday calendar month-end: a company
  contributes to a month only when its EODHD `HistoricalTickerComponents`
  interval covers that final session **and** the underlying price session
  falls inside the interval (mid-month joiners and leavers are handled
  exactly).
- **Multi-class members are one company.** The verified static pairs
  (`STATIC_COMPANY_GROUPS`: GOOG/GOOGL, FOXA/FOX, NWSA/NWS, CMCSA/CMCSK,
  TFCFA/TFCF, UAA/UA, LBRDA/LBRDK, MOB.A/MOB.B) take precedence over the
  cached SEC company-tickers file, because the current SEC file can list
  only one class of a delisted multi-class company (CMCSA is listed while
  CMCSK is not) and would split a declared pair; the CIK map still groups
  every pair the static list does not know about. The provider reports
  **company-wide statement shares on every class**, so each class's equity
  already approximates the whole company; summing classes would double-count
  the numerator while earnings count once. Company equity therefore uses the
  primary (largest) class's equity proxy, and company-wide earnings are
  counted exactly once and must agree across classes — when two classes
  report earnings that disagree beyond a 2% tolerance, the company stays a
  gap for that month rather than guessing which class is right. A missing
  CIK cache degrades to the static list and is disclosed as a warning in the
  run's quality report (`cik_map_available`), not silently.
- Provider rows without a `StartDate` are anchored at the earliest served
  date (see `services/universe.py`): EODHD omits join dates only for members
  whose tenure predates its records, every join since 2010 is dated, and the
  anchored tickers are listed in the run's quality report
  (`join_date_anchored_tickers`). For the index series this is conservative:
  such members never appear before the history start, and members that listed
  later than the anchor (e.g. IR, listed 2014) simply fail coverage for the
  months before their first price session.

## Earnings basis and known limitations

- Earnings follow the per-stock P/E gates exactly: currency match, four
  consecutive disclosed quarters, a latest statement no older than 180 days,
  annual reconciliation, and the earnings-quality quarantines documented in
  `docs/valuation_history.md` and the
  `docs/historical_multiples_review_2026-09-12/` review (zeroed losses, filing
  placeholders, placeholder EBITDA remain blocking). A non-positive TTM total
  is a valid negative contribution, never zero. EODHD intermittently omits
  `netIncomeApplicableToCommonShares` for financial companies even while its
  reported `netIncome` remains present. In that one case, the index aggregate
  uses reported net income as an explicit proxy only after every other P/E
  gate passes. The individual company's P/E remains unavailable, because the
  proxy can include preferred dividends. The quality report discloses how many
  months and companies used the proxy.
- Provider statement shares are split-adjusted weighted-average proxies, so
  equity totals are **estimates of market capitalization**, not verified
  historical market caps. The aggregate ratio is far less sensitive to this
  than the per-company equity level, but the caveat stands.
- The series is **reconstructed, not point-in-time backtest data**: initial
  provider payloads may contain later restatements; revisions become effective
  only after their availability date. Production backfill verification found
  every month from 2016-01 onward above the 80% company-coverage gate after the
  constrained financial proxy, while 2010-2015 remained below it (roughly
  48%-79%) because of unverified filing dates, absent historical prices and
  irreconcilable provider statements. The default therefore begins at 2016-01
  instead of weakening the gate or publishing a systematically biased series.
- EODHD's GSPC history itself misses some members entirely (measured ~444/500
  companies for early 2010 months during the backfill audit). Missing members
  are absent from both numerator and denominator; the coverage columns make
  the residual visible per month.

## Backfill and operations

`scripts/backfill_index_valuation.py` is a one-time, resumable data
acquisition for the deployment database:

```bash
python scripts/backfill_index_valuation.py --dry-run          # work plan only
python scripts/backfill_index_valuation.py --tickers AAPL.US --verify --skip-refresh
python scripts/backfill_index_valuation.py                    # full run + refresh
```

Prerequisites and behaviour:

- **The daily pipeline must have published `price_history` for the target
  session first** (a normal deployment does this every night; a fresh one
  should run `scripts/cold_start_init.py` once). The script fails fast with
  instructions otherwise, because the final aggregation refresh defers
  without it.
- Membership intervals are refreshed with `force=True`: an older parser
  version may already have published the session's intervals without the
  anchored ancient members, and the skip-if-published guard would leave that
  truncated history in place. The script also warns when the first sample
  month has fewer members than the publication gate.
- The SEC company-tickers file is downloaded once when the cache is absent
  (it is not shipped in the image; `data/` is excluded from builds).
- Per ever-member it backfills prices over that member's own membership
  window (one EOD call), fetches full fundamentals once when the stored
  **distinct** quarters do not cover the window — the completion check
  requires quarters near both ends of the span and no quarter-sized holes,
  so a recent-only partial history is re-fetched instead of silently
  stranding the early months (a fetch within the last 7 days that still
  fails the check is accepted as a provider limitation, with a warning,
  instead of re-paying on every re-run) — and verifies complete split
  history (one call) — roughly 800 price calls + 800 split calls + ~10 calls
  per still-fundamentals-less member in total. A durable checkpoint is updated
  in the same transaction as successful fundamentals normalization; it keeps
  identical, deduplicated raw payloads from defeating the seven-day re-fetch
  guard and never suppresses recovery after a failed normalized write.
  Completed tickers are skipped on re-run.
- Before aggregation, legacy statement versions that predate section-currency
  inheritance are repaired from their own immutable raw fundamentals snapshot.
  The repair requires an exact same-period statement match and never infers a
  historical reporting currency from today's quote or ticker profile.
- Acquisition failures mark the backfill run `failed` with the ticker list
  (the gated aggregation refresh may still publish; its coverage gates
  decide) and the script exits non-zero when the refresh does not publish,
  with the missing datasets named.

The migration (`0021_index_valuation_snapshots`) runs with the normal
`alembic upgrade head`.

Configuration lives in `core/config.py` / `.env.example`:
`INDEX_VALUATION_HISTORY_START`, `PIPELINE_MIN_INDEX_VALUATION_COVERAGE`,
`INDEX_VALUATION_MIN_MONTH_COVERAGE`, `INDEX_VALUATION_COMPUTE_CONCURRENCY`,
`INDEX_VALUATION_SEC_TICKERS_PATH`.
