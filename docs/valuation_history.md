# Historical valuation multiples

The individual stock page exposes **P/E, P/S, P/B, P/FCF, EV/Revenue and
EV/EBITDA** under **Price & Factors → Price & valuation history**. The Valuation
tab also links to this view. Price, volume and the selected multiple share zoom
and crosshair dates. Daily, weekly and monthly observations are supported;
switching the multiple preserves zoom. The dashed reference is the median of
all valid observations at the selected frequency, not a fair-value target.

## Calculation contract

These are **reconstructed estimates**, not a replacement for the published PIT
factor dataset and not inputs to DCF, peer comparisons or backtests.

- P/E = split-only close / sum of the latest four quarterly reported earnings
  per provider statement share. Each quarter uses its own share count. Common
  earnings are preferred; reported net income is the fallback. This is not
  presented as verified GAAP diluted EPS. EODHD `Earnings.History.epsActual`
  (non-GAAP) and current `TrailingPE` are not used.
- Equity value proxy = split-only close × latest quarterly provider shares.
  P/S and P/FCF divide this by TTM revenue and TTM CFO minus absolute capex.
  Provider FCF is not substituted when CFO/capex is incomplete. P/B divides by
  the latest stockholders' equity; this balance is not summed over four quarters.
- Simplified EV = equity value proxy + resolved reported debt − cash and
  short-term investments (cash/equivalents when the wider total is unavailable).
  EV/Revenue and EV/EBITDA use complete TTM denominators. The proxy excludes
  preferred equity and noncontrolling interests; debt lease scope remains
  provider-qualified. EV multiples are unavailable for financial companies.

EODHD historical statement shares are treated as split-adjusted to their
payload vintage, **not** as unadjusted fiscal-date shares. Each recorded version
uses its own fetch date. Shares and raw close are normalized to the last price
date using corporate actions. Duplicate identical splits are deduplicated and
conflicting/invalid factors disable the calculation. A `sharesBasis` of
`period_end` explicitly identifies unadjusted period-end shares. The chart's
price panel keeps existing dividend-adjusted OHLC; the multiple calculation
never uses `adjusted_close`.

Split normalization requires a stored EODHD split response covering the full
range of price dates and statement share vintages. Recent-only backfills and
an empty corporate-actions table do not prove historical coverage. A verified
empty provider response does establish no splits in its requested range.
Calculations use the verified raw response, so missing normalized action rows
cannot silently inflate multiples. Unreadable or invalid responses disable all
six multiples. The response includes `split_history_verified`, the source
snapshot ID and its coverage horizon.

## Availability and gaps

Versioned quarterly statements are selected by availability. Legacy statements
are used only for periods without versions. Inputs start on the first observed
price session **strictly after** all known filing/availability dates for that
statement, a conservative rule for unknown intraday timing. Estimated/missing
dates are excluded. Later captured revisions do not replace earlier values
before their availability date. Initial historical payloads can already be
restated, so this cannot establish exact historical information availability.

Four consecutive quarters are required for TTM (60–130 days between periods;
240–310 days across the four quarter-end dates). No annual substitution,
interpolation or zero imputation is used. A latest quarterly statement older
than 180 days expires. Missing/non-positive denominators, invalid raw prices,
unknown share basis, currency mismatch, known ADR conversions and incomplete
debt/cash bridges produce null observations and explicit reasons. Negative
simplified EV is also shown as unavailable. Other valid metrics remain usable.

Statement currency can inherit its section's explicit currency at ingestion.
For older rows, the read path can recover it from an exactly matching statement
in the latest immutable raw snapshot. It is never inferred from the quote's
currency. Legacy share vintage uses the raw snapshot's fetch date when present.

Weekly/monthly values use the last actual price session's ratio, including null
values. Labels match existing Friday/month-end price buckets; the tooltip shows
the actual session date. Lines do not connect gaps. The latest summary does not
carry forward an earlier valid observation.

## API and implementation

- `GET /api/stocks/{ticker}` includes `valuation_history` after the existing
  stock synchronization. It also repairs missing split coverage independently
  of price/fundamental freshness, under the existing ticker lock and external
  request rate limit. Failed or rate-limited repair keeps cached prices usable
  while leaving unverified multiples unavailable. The stock interval applies
  to both histories.
- `GET /api/stocks/{ticker}/valuation-history?interval=1d|1wk|1mo` is a local,
  read-only endpoint. It does not synchronize data or call external APIs.
- Six metric summaries report latest value/reason, median and coverage.
- Each point references a compact basis entry with statement periods,
  availability date, raw snapshot IDs, share vintage and financial inputs.
- Existing tables suffice; no schema migration or new provider subscription.
- Historical forward multiples remain unavailable until dated consensus
  snapshots can be sourced; today's forecast is never backfilled.

Provider definitions: [EODHD fundamental fields](https://eodhd.com/financial-academy/financial-faq/fundamentals-glossary-common-stock)
and [raw versus adjusted prices](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes).

Validation covers six formulas, missing/negative inputs, cash-flow definition,
quarter continuity, disclosure timing, revisions with different split vintages,
currency inheritance, ADR exclusion, split conflicts, stale data, sampling,
API validation, metric switching and linked chart axes. Browser checks exercise
all six metrics and three frequencies in both themes on desktop and mobile.
