# Quantify v2 Architecture

## 1. Runtime topology

The application is a small, single-node research platform with deliberately separate lifecycles:

```text
Browser / Next.js
        |
        v
FastAPI read and admin APIs -----> SQLite (WAL, FK enabled)
                                      |
Dedicated worker --------------------+
  |-- XNYS-aware APScheduler
  |-- data quality and publication jobs
  |-- optional daily watchlist RSI alerts
  |-- optional WebSocket monitor
  `-- weekly online SQLite backup

Immutable raw JSON.gz ---> normalized point-in-time tables ---> factor panels ---> research/backtests
```

Uvicorn never owns scheduled jobs. This makes API worker scaling safe and prevents duplicate notifications or data writes. `worker.py` is the only scheduler process.

The optional RSI monitor runs at 04:30 America/New_York on Tuesday through
Saturday, after the preceding US session's daily pipeline. It reads the
server-side personal watchlist unless `RSI_MONITOR_SYMBOLS` overrides it,
refreshes stale daily price histories on demand, and sends one Feishu digest
for RSI(14) values at or beyond the configured 30/70 thresholds. Successfully
delivered ticker/date alerts are persisted so worker restarts cannot duplicate
the same daily alert.

## 2. Storage choices

SQLite remains the serving and metadata database because the current universe and write rate are small. WAL, a 30-second busy timeout and foreign-key enforcement are configured on every connection. Alembic owns production schema changes.

Raw provider responses are content-addressed gzip JSON files under `data/raw`. The database stores their checksum, location, fetch time and dataset identity. This preserves lineage and allows normalized data to be rebuilt.

For larger offline panels, the raw files can later be converted to Parquet and queried with DuckDB without changing the API database.

## 3. Point-in-time schema

- `security_master`, `symbol_history`: canonical identity and vendor symbol history.
- `universe_membership`: membership intervals with `effective_from/effective_to`; exited securities are retained.
- `daily_prices`, `corporate_actions`: raw/adjusted prices, splits and dividends. The daily exchange-wide bulk sync updates all three without per-symbol action requests.
- `fundamental_versions`: period end, filing time, information `available_at`, fetch time and preserved revisions.
- `raw_data_snapshots`: immutable source lineage.
- `pipeline_runs`, `data_publications`: resumable job state, validation report and the dates API consumers may read.
- `rrg_price_snapshots`, `market_breadth_snapshots`: immutable ETF prices and 252-session raw breadth counts for each successful market publication.
- `factor_values`: named, versioned raw and normalized factor observations.
- `strategy_definitions`, `signal_snapshots`, `backtest_runs`: reproducible research outputs.

Legacy `financial_statements` and `stock_screener_snapshot` remain for dashboard compatibility. New research must use quality-gated publications and versioned factor values.

## 4. Publication pipeline

The daily screener pipeline performs these stages:

1. Resolve the observed Russell 3000 + Nasdaq-100 union, retaining each source index (including the derived Russell 3000) as a distinct live filter label.
2. Fetch EOD prices and fundamentals; store the immutable source batch.
3. Record security identity, universe intervals and fundamental availability/revisions.
4. Upsert prices and current screener rows idempotently.
5. Calculate indicators using prices no later than the snapshot date.
6. Validate universe size, ticker uniqueness, price coverage and fundamental coverage.
7. Atomically publish the snapshot and cumulative price panel only after the quality gate passes.
8. Compute and publish the `lfq-v1` factor cross-section in a separate tracked run.

Failures are persisted and re-raised so APScheduler does not report false success. API reads prefer the latest `data_publications` row. Historical Screener reconstruction is refused unless an archived point-in-time source payload is explicitly imported; current fundamentals are never relabeled as historical.

The single-security Market Snapshot uses the published Screener row when present, then fills missing values from the latest local fundamentals, financial statements, corporate actions and adjusted price history. A ticker does not need to belong to the Screener universe; membership is required only for index labels and cross-sectional peer context.

The market-overview pipeline currently imports EODHD `HistoricalTickerComponents` for GSPC, validates complete non-overlapping intervals, and transactionally replaces only provider-owned index history. It publishes S&P 500 breadth from each date's active members. Russell 2000 and the deduplicated combined universe remain explicitly disabled until a reliable strict historical membership source is available; live constituents are never substituted. A publication requires matching `price_history`, `universe_history`, and `rrg_price_history` dates; a failed quality gate leaves the prior complete market snapshot available as stale. RRG snapshots referenced by retained market publications are protected from independent RRG retention cleanup.

## 5. Cold start and catch-up

`python scripts/cold_start_init.py`:

1. Initializes/migrates tables and seeds benchmark/sector ETFs.
2. Imports strict S&P 500 historical membership intervals.
3. Backfills 504 trading sessions for every member needed by the one-year breadth panel.
4. Publishes immutable sector ETF, SPY and RSP price history.
5. Captures the latest Screener snapshot and publishes the first 252-session market-breadth panel.
6. Recomputes MA/RSI after the history is warm and publishes the first factor cross-section.

Backfill is resumable: tickers with sufficient coverage are skipped and every run records progress. Worker startup catches up the latest completed XNYS session. It intentionally does not fabricate every missed date with today's universe.

The public `GET /api/v1/market-overview` endpoint serves 3M/6M/1Y aligned S&P 500 arrays for sector trends, RSP/SPY, MA breadth, advances/declines, new highs/lows, McClellan and cross-sectional dispersion. Disabled Russell 2000 and combined requests return an explicit validation error. `/market` renders the arrays in one linked ECharts timeline; `/rrg` remains the rotation view and keeps its existing URL.

Factor Lab backtests likewise default to S&P 500. A Russell 2000 or combined backtest is rejected unless strict historical rows continuously cover every US market session in the requested window for every required underlying index, preventing an incomplete S&P-only period from being mislabeled as a combined-universe result.

## 6. Factor methodology (`lfq-v1`)

- Value: earnings yield and book-to-price.
- Quality: ROE, gross margin and inverse leverage.
- Growth: five-year sales growth.
- Momentum: 12–1 momentum when 252 days exist, otherwise a 63-day warm-up signal.
- Low volatility: negative annualized realized volatility.

Each cross-section applies sector-median missing-value handling, 1%/99% winsorization, sector demeaning and z-scoring. The composite is the equal-weight mean. At least 80% of the universe must have both momentum and low-volatility observations. Factor rows are append-only by source run and record their version, availability time and sector; readers only expose the run referenced by the matching publication.

The research API reports Rank IC, IC information ratio, positive IC rate, quantile returns, monotonicity, long-short spread and top-quantile turnover.

## 7. Backtest rules

- Factor signals must have been published by the official XNYS execution close, execute at least one later trading close and earn returns only after that execution close.
- Membership is resolved at each signal date; missing PIT membership fails by default.
- Prices use adjusted close with raw close fallback.
- Portfolio construction enforces top-N, position and sector caps and may retain cash when constraints are infeasible.
- Turnover includes cash transitions. Commission and slippage are charged on every rebalance.
- Missing held-security prices fail by default, forcing the researcher to load a delisting return; an explicit `liquidate_last` policy is available.
- A period with no executable, invested rebalance fails instead of reporting a misleading zero-return backtest.
- Outputs include return, volatility, Sharpe, Sortino, drawdown, VaR/CVaR, Beta, Alpha, tracking error, information ratio, turnover, costs and sector contribution.
- Signal snapshots are keyed by backtest run, so rerunning the same strategy never overwrites an earlier run's evidence.

## 8. Security and operations

- Without `ADMIN_API_KEY`, production starts in read-only mode and admin APIs return 503; no default admin secret is used.
- Admin and state-changing APIs require `X-API-Key`; expensive public research endpoints are rate-limited.
- CORS is an explicit allow-list.
- API keys are never logged.
- `.dockerignore` excludes credentials, databases, raw data and local build artifacts.
- The backend container runs as a non-root Python 3.12 user and applies Alembic migrations before Uvicorn.
- Docker Compose forces `ENVIRONMENT=production`; a missing `ADMIN_API_KEY` is logged and disables admin operations without taking public reads offline.
- `/health/live` and `/health/ready` support orchestration.
- `scripts/backup_sqlite.py` uses SQLite's online backup API and validates every copy.

## 9. Commands

```bash
cp .env.example .env
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
pytest -q

uvicorn main:app --reload
python worker.py

cd frontend
npm ci
npm run lint
npx tsc --noEmit
npm run build
npm run dev
```

## 10. Data boundary

Snapshots created before v2 did not preserve historical membership or information availability and must not be used for backtests. They remain readable by the dashboard as legacy data. Trustworthy research history begins with v2 quality-gated publications or with separately imported, verified point-in-time source data.
