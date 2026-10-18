# Consensus revisions and short interest

Analysts' EPS estimate revisions and short-interest changes move faster than
quarterly statements, so they surface risk and momentum between filings. They
appear in three places:

- **Screener** (`services/screener_fields.py`): six fields. *EPS Estimate
  Revision (30D/90D)*, *EPS Revision Breadth (30D)* and *EPS Estimate Analysts*
  are under Fundamental. *Short Interest Change (1M)* and *Short Ratio (Days to
  Cover)* are under Descriptive.
- **Key risks** on the Analysis page: *Analysts cutting EPS estimates* and
  *Short interest rising* / *Heavily shorted*, next to the statement rules. The
  Risks tab adds a *Consensus revisions & short interest* summary with the raw
  values. The *Expectation signal* card in the Events & expectations panel
  shows the same verdict: `/api/stocks/{ticker}/events-expectations` returns
  the same `expectation_signals` block as decision support, built from the
  same payload by `build_expectation_context`.
- **Feishu daily report and weekly digest**: watchlist tags such as
  `预期下修 4.2%`, `预期上修 21/33家`, `空头增加 31%` and `空头占流通 21%`.

All logic lives in `services/expectation_signals.py`.

## Source and freshness

Everything comes from the EODHD fundamentals document that the screener already
fetches, so there are no extra provider calls:

| Value | Field |
|---|---|
| Current fiscal-year (FY1) consensus EPS now, 30 and 90 days ago | `Earnings.Trend`, latest row with `period == "0y"` |
| Next fiscal-year consensus EPS now, 30 and 90 days ago | `Earnings.Trend`, latest row with `period == "+1y"` |
| Upward and downward revisions in 30 days, covering analysts | same `0y` row |
| Shares sold short, prior month, days to cover | `Technicals.SharesShort`, `SharesShortPriorMonth`, `ShortRatio` (fallback `SharesStats`) |
| Short interest as a share of float | `SharesStats.ShortPercentFloat` |

The Trend map keeps every earlier fiscal year under the same `0y` label, so the
latest period end is the current year. A latest `0y` row that ended more than
120 days before the payload's `General.UpdatedAt` is treated as no longer
maintained. Values are "as of" `UpdatedAt`.

Screener fields refresh with the screener's fundamentals cache (at least weekly,
`SCREENER_FUNDAMENTAL_MAX_AGE_DAYS`). Key risks and report tags read each
ticker's latest stored payload and ignore payloads older than 14 days. None of
this is point-in-time history, so the fields never enter factors or backtests.

## Definitions

- **Revision** = (estimate now − estimate then) ÷ |estimate then|. Unavailable
  when |estimate then| < $0.05. A provider value of exactly 0 is a placeholder.
  For loss-making companies a negative revision means a larger expected loss.
- **Breadth** = (up − down revisions in 30 days) ÷ covering analysts.
- **Short interest change** = shares short ÷ prior-month shares short − 1.

## Rules

Revisions need at least 3 covering analysts. A percentage revision counts only
when the next fiscal year moves the same way by at least the threshold (the
smaller of the two moves is compared), which screens out one-off gains and
charges that move only the current year. The first matching rule sets the
direction: 30-day change, then 30-day breadth, then 90-day change.

| Flag | Warning | High |
|---|---|---|
| EPS estimate cut, 30 days | ≤ −3% | ≤ −10% |
| EPS estimate cut, 90 days | ≤ −7.5% | ≤ −20% |
| Net analysts cutting (current year only) | breadth ≤ −25% with ≥ 3 cuts | — |
| Short interest rising | ≥ +25% month over month at ≥ 5% of float | ≥ +50% at ≥ 10% of float |
| Heavily shorted | ≥ 15% of float | ≥ 25% of float |

Raises (≥ +3% in 30 days, ≥ +7.5% in 90 days, or breadth ≥ +25% with ≥ 3
raises) only produce a report tag, not a risk.

The flags stay out of the cited brief's evidence registry and the deterministic
summary, which keep describing the statement rules only.

## Deploying

Migration `0025_expectation_signals` adds the six screener columns. The fields
then fill in as the screener refreshes each ticker over about a week. To fill
them at the next screener publication instead, derive them from the stored
payloads (no provider calls; published snapshots are not modified):

```bash
docker compose exec worker python scripts/rederive_expectation_signals.py --dry-run
```

```bash
docker compose exec worker python scripts/rederive_expectation_signals.py
```

## Known limitations

- EODHD leaves `epsRevisionsDownLast7days` empty, so there is no 7-day window.
- The short-interest settlement date is not in the payload; only the update
  date is shown.
- Consensus EPS can still include one-off items when they span both fiscal years.
