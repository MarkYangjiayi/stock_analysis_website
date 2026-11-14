# Implied expectations: what the price requires

The scenario DCF starts from trailing free cash flow. That number is negative
or distorted while a company invests heavily (ORCL, AMZN), or when financing
flows sit in operating cash flow (MELI), so its reverse DCF has no answer or a
misleading one. This model starts from revenue and asks what the current
price requires of the business.

`/api/stocks/{ticker}/decision-support` returns it as `implied_expectations`.
All logic lives in `services/implied_expectations.py`. The Analysis page shows
it at the top of the Valuation tab (`ImpliedExpectationsPanel`), where it
replaced the reverse-DCF block, and shows the implied return in the Overview's
valuation outlook.

## Model

Ten forecast years, valued by the same kernel as the scenario DCF
(`services/dcf.py`):

- **Revenue** grows at one rate for five years, then fades linearly to
  terminal growth (`VALUATION_TERMINAL_GROWTH_RATE`).
- **Operating margin** moves linearly from the trailing-twelve-month margin to
  a target margin over five years, then holds.
- **Reinvestment** is new revenue divided by the sales-to-capital ratio:
  trailing revenue over invested capital (equity + debt - cash), capped at 5.
  Shrinking revenue hands no capital back.
- **Terminal value** assumes new investment earns the discount rate, so growth
  after year ten adds no value.

One price fits many combinations of these, so each answer fixes the others.

## What it returns

- `implied_return`: the annual return the price offers if consensus is met.
  Revenue follows consensus for two years and then fades; the margin moves to
  the consensus-implied margin (trailing margin when there is no EPS
  consensus). It needs no discount rate. `status` is `below_range` when even a
  3% rate values consensus below the price.
- `hurdles`, one per required return (8%, 10%, 12%; 10% is the default):
  - `growth_by_margin`: the five-year revenue growth the price needs at the
    trailing margin, the consensus margin and the five-year-high margin.
  - `margin_at_consensus_growth`: the margin it needs if revenue follows
    consensus.
  - `sales_to_capital_at_consensus`: the revenue per dollar of invested
    capital it needs at consensus growth and margin. Compare with
    `inputs.sales_to_capital`; this is the open question for companies in a
    capital-spending surge.
- `references`: five-year revenue growth and operating-margin range from
  annual statements, and the consensus figures.
- `assessment`: whether the implied return clears the default required return,
  and whether the default growth hurdle sits above, within or below the
  history and consensus growth rates. It describes the hurdle; it is not a
  valuation verdict.

A value is `null` when nothing in the search range matches the price. For
growth that usually means new capital earns less than the required return, so
no amount of growth justifies the price.

## Consensus

The current and next fiscal-year rows of `Earnings.Trend` in the stored EODHD
fundamentals document, used only while the expectation signals are fresh (14
days).

- **Next twelve months of revenue** blend the two fiscal years by how much of
  the current fiscal year is still ahead of the trailing period.
- **Consensus margin** is next-fiscal-year EPS x shares / (1 - tax rate) /
  revenue. It ignores net interest and non-operating items and inherits the
  provider's EPS basis, which may exclude stock compensation. Treat it as
  approximate.

## Limits

- The required return drives the hurdles: for the same price, 8% and 12% can
  differ by tens of points of growth. Read across the three rows.
- Sales-to-capital comes from the balance sheet at one date. A spending surge
  that has not produced revenue yet makes it look worse than steady state.
- Not meaningful for banks and insurers, where debt is an operating input.
- Non-US listings and ADRs mostly have no consensus rows, so only the growth
  hurdles are solved.

## Share count

Multi-class companies (GOOG, GOOGL) have a provider share count for one listed
class. When price x provider shares is more than 25% away from the published
market value and price x statement shares is within 25%, valuation uses the
statement shares. This applies to the scenario DCF as well.
