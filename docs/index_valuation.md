# Index-level valuation: S&P 500 historical P/E

The Market tab exposes **Valuation → S&P 500 Historical P/E**
(`/market/index-valuation`): the index's month-end aggregate price-to-earnings
from `INDEX_VALUATION_HISTORY_START` (default 2010-01-01) to the latest
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

- Membership is **point-in-time**: a company contributes to a month only when
  its EODHD `HistoricalTickerComponents` interval covers that month-end **and**
  the underlying price session falls inside the interval (mid-month joiners
  and leavers are handled exactly).
- **Multi-class members are one company.** Grouping uses the cached official
  SEC company-tickers file (`INDEX_VALUATION_SEC_TICKERS_PATH`, ticker → CIK)
  with a documented fallback list (`STATIC_COMPANY_GROUPS`, verified against
  the provider's GSPC history: GOOG/GOOGL, FOXA/FOX, NWSA/NWS, CMCSA/CMCSK,
  TFCFA/TFCF, UAA/UA, LBRDA/LBRDK, MOB.A/MOB.B). The provider reports
  **company-wide statement shares on every class**, so each class's equity
  already approximates the whole company; summing classes would double-count
  the numerator while earnings count once. Company equity therefore uses the
  primary (largest) class's equity proxy, and company-wide earnings are
  counted exactly once and must agree across classes — when two classes
  report earnings that disagree beyond a 2% tolerance, the company stays a
  gap for that month rather than guessing which class is right. A missing CIK
  cache degrades to the static list and is disclosed as a warning in the
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
  is a valid negative contribution, never zero.
- Provider statement shares are split-adjusted weighted-average proxies, so
  equity totals are **estimates of market capitalization**, not verified
  historical market caps. The aggregate ratio is far less sensitive to this
  than the per-company equity level, but the caveat stands.
- The series is **reconstructed, not point-in-time backtest data**: initial
  provider payloads may contain later restatements; revisions become effective
  only after their availability date. The 2010 start deliberately stays inside
  the earnings window the 2026-09 review could verify; earlier provider data
  is not trusted and not backfilled.
- EODHD's GSPC history itself misses some members entirely (measured ~444/500
  companies for early 2010 months). Missing members are absent from both
  numerator and denominator; the coverage columns make the residual visible
  per month.

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
  window (one EOD call), fetches full fundamentals once when stored
  statements do not cover most of the window's expected quarters (ten calls;
  current screener members already carry full statement history), and
  verifies complete split history (one call) — roughly 800 price calls +
  800 split calls + ~10 calls per still-fundamentals-less member in total.
  Completed tickers are skipped on re-run.
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
