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
  |-- daily stale-data alert (services/freshness_alerts.py)
  |-- live quote hub: the only EODHD WebSocket (analysis-page prices, optional move monitor)
  `-- weekly online SQLite backup

Immutable raw JSON.gz ---> normalized point-in-time tables ---> factor panels
```

Uvicorn never owns scheduled jobs. This makes API worker scaling safe and prevents duplicate notifications or data writes. `worker.py` is the only scheduler process.

The optional RSI monitor runs at 04:30 America/New_York on Tuesday through
Saturday, after the preceding US session's daily pipeline. It reads the
server-side personal watchlist unless `RSI_MONITOR_SYMBOLS` overrides it,
refreshes stale daily price histories on demand, and sends one Feishu digest
for RSI(14) values at or beyond the configured 30/70 thresholds. Successfully
delivered ticker/date alerts are persisted so worker restarts cannot duplicate
the same daily alert.

The analysis page header shows real-time trades during the regular session
(`services/live_quotes.py`). EODHD allows 50 WebSocket symbols per API token
across every socket, so the worker holds the only connection and SQLite is the
interface to it: each 3-second poll of `GET /api/v1/quotes/live` renews a
one-minute row in `live_quote_leases`; the worker subscribes leased tickers
(newest first, up to `LIVE_QUOTE_MAX_SYMBOLS`, with `WS_SYMBOLS` pinned while
the move monitor is on), writes the latest regular-session trade to
`live_quotes` about once a second, and keeps a heartbeat in `live_quote_feed`.
The socket is open only while something is leased in the session (plus five
minutes for the closing print). When the heartbeat is stale or a ticker is not
streamed, the API serves the 15-minute delayed quote; after the close it serves
that session's close until the daily bar lands.

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

## 6. Factor methodology (`lfq-v1`)

- Value: earnings yield and book-to-price.
- Quality: ROE, gross margin and inverse leverage.
- Growth: five-year sales growth.
- Momentum: 12–1 momentum when 252 days exist, otherwise a 63-day warm-up signal.
- Low volatility: negative annualized realized volatility.

Each cross-section applies sector-median missing-value handling, 1%/99% winsorization, sector demeaning and z-scoring. The composite is the equal-weight mean. At least 80% of the universe must have both momentum and low-volatility observations. Factor rows are append-only by source run and record their version, availability time and sector; readers only expose the run referenced by the matching publication.

## 7. Security and operations

- Without `ADMIN_API_KEY`, production starts in read-only mode and admin APIs return 503; no default admin secret is used.
- Admin and state-changing APIs require `X-API-Key`; expensive public research endpoints are rate-limited.
- CORS is an explicit allow-list.
- API keys are never logged.
- `.dockerignore` excludes credentials, databases, raw data and local build artifacts.
- The backend container runs as a non-root Python 3.12 user and applies Alembic migrations before Uvicorn.
- Docker Compose forces `ENVIRONMENT=production`; a missing `ADMIN_API_KEY` is logged and disables admin operations without taking public reads offline.
- `/health/live` and `/health/ready` support orchestration.
- `scripts/backup_sqlite.py` uses SQLite's online backup API and runs `PRAGMA quick_check` on every copy. A copy that passes is compressed with zstd (level 9, about a tenth of the size) and stored as `.db.zst`; the uncompressed file is deleted only after the compressed one restores to the same SHA-256. If compression fails, the verified uncompressed copy is kept and the error is logged. Retention counts both forms. To restore, run `zstd -d <backup>.db.zst -o <target>.db` on the host.
- Deploy and recover jobs share the `production-server` concurrency group, so a second push waits instead of overlapping (two overlapping deploys on 2026-10-20 deleted each other's images). If an interrupted deploy left the backend stopped, the next deploy loads the new images and takes the predeploy backup in a one-off container before starting any service.
- A failed or deferred pipeline keeps the previous publication serving, so `services/freshness_alerts.py` checks every watched dataset against the latest completed XNYS session at 08:45 New York time (Tue-Sat). It sends one Feishu card per session that lists each lagging dataset and its latest run's error, and records each delivery in `data_freshness_alerts`.
- The S&P 500 membership parser joins same-day handoffs: since October 2026 EODHD ends a ticker's segment on the day its next segment starts at renames and spin-offs. Genuine overlaps still fail the publication. The quality report lists the joined tickers under `same_day_handoff_tickers`.

## 8. Commands

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

## 9. Data boundary

Snapshots created before v2 did not preserve historical membership or information availability and must not be used for backtests. They remain readable by the dashboard as legacy data. Trustworthy research history begins with v2 quality-gated publications or with separately imported, verified point-in-time source data.
