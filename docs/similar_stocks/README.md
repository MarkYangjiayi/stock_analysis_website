# Similar price moves

Overview now includes up to five similar US common stocks, inline cumulative-return comparison, navigation and the existing personal watchlist actions. The UI follows the site's English copy and supports mobile, light and dark themes.

## Serving contract

`GET /api/stocks/{ticker}/similar` reads local SecurityMaster, Ticker and DailyPrice tables only. It does not fetch vendor data or write to the database. Results are computed on request, so newly synchronized prices are reflected without a separate job or cache invalidation.

The fixed window uses 61 adjusted closes over 60 completed US exchange sessions. Same-date simple daily returns are ranked by Pearson correlation, with an initial conservative minimum of 0.65 and deterministic ticker tie-breaking. Missing/nonpositive/nonfinite prices and constant-return series are excluded. Inactive securities and assets other than Common Stock are excluded. Issuer names normalized for punctuation and class suffixes suppress obvious duplicate share classes; this is not a full issuer-identity database.

Every chart pair shares its y scale and starts at zero cumulative return. Main-chart interval changes do not affect this panel. No predictive score, historical analog search, window control, or research view is included.

## Data limitations

This requires the existing migrated database and daily synchronization to populate security classifications and complete adjusted-price history. No unadjusted fallback or missing-day filling is used. A missing latest completed session returns insufficient_history rather than silently rolling back. Initial release covers US common stocks only. Recommendation quality and query latency should be monitored against the deployed universe; there is no persistent pair matrix or precomputation.

The prior saved 2026-09-04 research snapshot was used as a historical smoke check (497 profiles, not live data): JPM matched BAC/STT/BNY/MS/NTRS; VRT matched ETN/CAT/ASX/CMI/KLAC; AAPL and NVDA had no matches at the conservative threshold. A no-match result is intentional, and the threshold is not a predictive-confidence claim.

## Verification

- Backend tests cover aligned returns, missing observations, inverse/flat series, duplicate classes, adjusted prices, eligible asset types, completed sessions and API serialization.
- Component tests cover inline comparison, date inspection, navigation, shared watchlist actions, retry and request cancellation.
- Playwright covers desktop/mobile, fixed window across main-chart changes, switching comparisons, personal unlock and light/dark screenshots.
- Screenshots generated under frontend/test-results by `npm run test:e2e -- similar-stocks.spec.ts`.
