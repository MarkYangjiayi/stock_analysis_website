# Company-specific WACC and FCFF validation — 2026-09-09

## Scope

This validation runs the production assumption functions against the latest
EODHD fundamentals available on 2026-09-09; the external figures were rechecked
on 2026-09-10.  The sample is intentionally made up of familiar, large US
issuers with different beta and capital structures.

The implementation uses:

- cost of equity = US 10-year Treasury yield + provider beta × 6% equity risk premium;
- cost of debt = TTM interest expense / average of the latest four quarterly book-debt observations;
- WACC = market-value equity weight × cost of equity + book-debt weight × after-tax cost of debt;
- FCFF estimate = provider CFO-minus-capex free cash flow + after-tax interest;
- one WACC and one terminal-growth rate for all three operating-growth scenarios;
- a separate WACC × terminal-growth sensitivity matrix.

The 6% equity-risk-premium convention, book debt, TTM interest/debt cost and
effective-tax treatment match the published [GuruFocus WACC methodology](https://www.gurufocus.com/term/wacc).
The FCFF/WACC pairing and firm-value-to-equity bridge follow
[Damodaran's firm DCF framework](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/lectures/fcff.html).

## Recalculation against GuruFocus

| Ticker | Local WACC | GuruFocus WACC | Difference |
|---|---:|---:|---:|
| AAPL | 11.20% | [10.11%](https://www.gurufocus.com/term/wacc/AAPL) | +1.09 pp |
| MSFT | 11.32% | [11.73%](https://www.gurufocus.com/term/wacc/MSFT) | -0.41 pp |
| GOOGL | 11.99% | [12.84%](https://www.gurufocus.com/term/wacc/GOOGL) | -0.85 pp |
| AMZN | 12.73% | [13.47%](https://www.gurufocus.com/term/wacc/AMZN) | -0.74 pp |
| NVDA | 18.06% | [17.05%](https://www.gurufocus.com/term/wacc/NVDA) | +1.01 pp |

Mean absolute difference is **0.82 percentage points** and the maximum is
**1.09 percentage points**.  The remaining difference is mainly beta-window
choice: the local EODHD provider beta agrees with the independently displayed
five-year beta on StockAnalysis, while GuruFocus documents a three-year beta.
For example, AAPL is 1.085 locally/1.09 on StockAnalysis but 0.9189 on
GuruFocus; GOOGL is 1.225 locally/1.22 on StockAnalysis but 1.3669 on GuruFocus.

## Second-source reasonableness check

StockAnalysis reports financial statistics sourced from S&P Global Market
Intelligence.  Its WACC methodology uses different market assumptions, so this
is a range check rather than an exact-formula reproduction.

| Ticker | Local WACC | StockAnalysis WACC | Difference |
|---|---:|---:|---:|
| AAPL | 11.20% | [10.04%](https://stockanalysis.com/stocks/aapl/statistics/) | +1.16 pp |
| MSFT | 11.32% | [10.06%](https://stockanalysis.com/stocks/msft/statistics/) | +1.26 pp |
| GOOGL | 11.99% | [10.72%](https://stockanalysis.com/stocks/googl/statistics/) | +1.27 pp |
| AMZN | 12.73% | [11.26%](https://stockanalysis.com/stocks/amzn/statistics/) | +1.47 pp |
| NVDA | 18.06% | [16.33%](https://stockanalysis.com/stocks/nvda/statistics/) | +1.73 pp |

All five estimates preserve the same risk ordering as both external sources.
The local estimates run higher than StockAnalysis because the local formula
uses the explicitly disclosed 4.808% risk-free rate and 6% equity-risk premium.

## Operating scenarios produced in the same run

| Ticker | Bear growth | Base growth | Bull growth | Terminal growth | TTM FCFF estimate |
|---|---:|---:|---:|---:|---:|
| AAPL | 1.46% | 11.46% | 21.46% | 2.50% | $141.4B |
| MSFT | 5.35% | 15.35% | 25.35% | 2.50% | $69.4B |
| GOOGL | 4.83% | 14.83% | 24.83% | 2.50% | $55.1B |
| AMZN | 8.18% | 13.18% | 18.18% | 2.50% | -$9.0B |
| NVDA | 15.00% | 20.00% | 25.00% | 2.50% | $127.4B |

AMZN correctly remains unavailable for this positive-FCFF Gordon-growth DCF
because its reconstructed TTM FCFF is negative.  The application does not turn
that missing model fit into a zero or positive valuation.

## Full valuation comparison

The following is a useful directional check, but not an exact reproduction.
The local model discounts five years of estimated FCFF and a Gordon terminal
value at WACC.  GuruFocus's published FCF-based DCF uses a ten-year two-stage
per-share model, so differences reflect both inputs and model architecture.

| Ticker | Market price | Local Bear | Local Base | Local Bull | GuruFocus FCF-based DCF |
|---|---:|---:|---:|---:|---:|
| AAPL | $316.22 | $106.62 | $161.01 | $236.98 | [$176.01](https://www.gurufocus.com/term/intrinsic-value-dcf-fcf-based/AAPL) |
| MSFT | $493.95 | $123.25 | $182.73 | $265.01 | [$167.67](https://www.gurufocus.com/term/intrinsic-value-dcf-fcf-based/MSFT) |
| GOOGL | $338.36 | $63.75 | $89.52 | $125.16 | [$126.64](https://www.gurufocus.com/term/intrinsic-value-dcf-fcf-based/GOOGL) |
| AMZN | $256.97 | unavailable | unavailable | unavailable | n/a for this check |
| NVDA | $225.73 | $57.07 | $67.55 | $79.73 | [$162.66](https://www.gurufocus.com/term/intrinsic-value-dcf-fcf-based/NVDA) |

AAPL and MSFT place the external estimate inside the local scenario range.
GOOGL is close to the local Bull case.  NVDA is the largest divergence: the
local model applies an 18.06% WACC and caps the mechanically derived five-year
base growth at 20%, making its output deliberately more conservative than the
external ten-year high-growth model.  This is an assumption difference rather
than an arithmetic reconciliation failure, and the UI leaves those assumptions
editable and visible.

## Conclusion

The local WACC calculations reproduce the published GuruFocus method closely
enough for the observed provider-beta difference, and the second source confirms
the level and cross-company ordering are reasonable.  WACC remains an estimate,
not an observable fact; the UI therefore exposes every component, fallback and
as-of date and retains a ±1/2 percentage-point sensitivity grid.
