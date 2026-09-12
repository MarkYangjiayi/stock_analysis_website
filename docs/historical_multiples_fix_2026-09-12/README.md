# Historical multiples fix: independent validation

Verified on 2026-09-12 against the updated `build_valuation_history`, using 11 companies and 47,405 cached historical price observations. The validation is offline and does not modify production data. This is evidence that the tested data-quality rules reject known bad inputs while retaining useful valid observations; it is **not certification of every historical multiple or all six metrics against official filings**.

## Reproduce

From the repository root, with its Python dependencies installed:

```sh
python docs/historical_multiples_fix_2026-09-12/verify_external.py
```

`inputs.json.gz` contains the frozen EODHD statements, prices, Yahoo anchor closes, and the selected SEC facts. Full original statement fields are retained so that a new input rule cannot accidentally depend on a field removed by fixture preparation. `source_hashes.json` records the source-cache SHA-256 values. `official_cases.json` provides compact reviewed facts and primary-source URLs. `verification.json` is the complete generated result.

The optional `--prepare /path/to/original/data` regenerates the fixture from the earlier local audit caches. The two AAPL Yahoo responses are included separately because they were newly fetched for this validation. Ordinary verification makes no network calls and requires no old data directory.

## Acceptance result

The final script exits successfully only if all regression checks pass, at least 32 independent valid comparisons remain across AAPL/MSFT/AMZN/WMT/CAT/INTC, no unexplained external difference exceeds 2%, no positive output contradicts a known nonpositive SEC denominator, and reference periods match. SEC evidence missing altogether is distinguished from a known nonpositive denominator. The one explicitly declared XOM revenue-scope difference is not treated as a numerical-equality pass.

- **16/16 regression cases passed.**
- **44 valid comparisons** across the six designated healthy companies, exceeding the minimum of 32.
- **0 unexplained external errors; 0 reference-period mismatches.**
- Across all 11 companies, two dates, and six metrics: **67 comparable values within 2%, 22 explicit gaps, 35 EV values without a fully verified independent bridge, 7 values with insufficient SEC reference evidence, and 1 declared scope difference.**

The two anchor dates are 2025-03-31 and 2026-09-10. Every anchor run uses the independent Yahoo close, not a price inferred from the API. SEC annual-plus-current-YTD-minus-prior-YTD inputs use only facts filed strictly before that date. Four equity multiples are compared separately using the provider's share proxy and, where available, official period-end shares. The former isolates financial mapping errors; the latter exposes the share-basis difference.

| Company | Equity-multiple comparisons within 2%, out of 8 | Maximum error among comparable valid values |
| --- | ---: | ---: |
| AAPL | 8 | 0.3173% |
| MSFT | 8 | 0.0618% |
| AMZN | 7 | 0.1448% |
| WMT | 8 | 0.2044% |
| CAT | 8 | 0.1278% |
| INTC | 5 | <0.0001% |
| XOM | 4 | 0.1080% |
| JNJ | 5 | 0.1283% |
| JPM | 2 | <0.0001% |
| NEE | 3 | 0.1021% |
| AMT | 7 | 0.6771% |

The uncounted cells are gaps, unavailable reference evidence, or the declared scope difference; they are not silently counted as passes. AMZN and INTC have nonpositive FCF or earnings windows. Financial-company revenue and industrial-style FCF comparisons are excluded by the product's explicit scope rule.

WMT's 2026 income scope was checked specifically to avoid an erroneous gap: the supplier's income statement has net income **6.366b**, its cash-flow statement has **6.529b**, and the supplier's current-quarter `minorityInterest` and consolidated-net-income fields are missing. The [official 2026 Q2 10-Q](https://www.sec.gov/Archives/edgar/data/104169/000010416926000154/wmt-20260731.htm) confirms **both numbers**: consolidated income 6.529b less noncontrolling interests 0.163b equals income attributable to Walmart 6.366b. The provider also explicitly reports pretax income 8.012b and tax 1.483b, whose difference is precisely the cash-flow statement's 6.529b. The final general rule recognizes this strict accounting bridge without inserting the missing minority value or changing either reported profit. At the 2026 anchor, P/E **38.30619x** and P/FCF **62.44089x** remain valid; their independent-reference errors are -0.004641% and <0.000001%, respectively. The preceding quarter's signed -0.160b minority-interest line is present and is reconciled directly. Dedicated positive-output regression cases prevent this legitimate scope difference from being incorrectly treated as bad data.

## Known bad data and a valid extreme

| Case | Result after the fix |
| --- | --- |
| AMZN 2002-01-25 and 2003-10-24 P/E | Gap: zero common-share earnings conflict with reported quarterly net losses. The former 7,890x loss-period P/E is absent. |
| AMZN 2002-01-25 P/S | Gap: four quarterly revenues disagree with the already disclosed 2001 annual revenue. |
| AMZN 2001-10-31 P/S | Remains 0.73347x: the later annual conflict is not backdated before the annual disclosure. |
| WMT 1996-06-13 EV/Revenue and EV/EBITDA | Gaps: reported total debt contains only short-term debt, with long-term debt missing. |
| MSFT 1990-01-02 EV/EBITDA and 1991-10-01 P/S | Gaps: fiscal-end filing-date placeholders do not count as disclosure. |
| Isolated MSFT old EBITDA input | Still a gap when synthetic filing metadata and zero long-term debt remove those other blockers: EBITDA equals revenue while cost of revenue is absent. P/E remains valid in this isolated test. |
| **AAPL 1998-08-11 P/E** | **260.5985x retained.** A small positive earnings denominator is not clipped or smoothed simply because the multiple is high. This does not certify the provider share proxy as GAAP diluted EPS. |
| JNJ 2026-09-10 P/FCF | Gap: cash-flow ending cash is below same-period balance-sheet cash and equivalents. |
| WMT 2026-09-10 P/E and P/FCF | Remain valid: the consolidated-versus-parent net-income difference has an exact pretax-income-less-tax bridge, independently confirmed by the official minority-interest table. |
| NEE 2025-03-31 P/FCF | Gap: utility capital-investment coverage is unverified; provider capex can omit generating-project investment. |
| JPM 2025-03-31 P/S and P/FCF | Gaps: financial-company revenue and cash-flow scope require separate definitions. |

AMZN's [2001 10-K, Notes 10 and 17](https://www.sec.gov/Archives/edgar/data/1018724/000103221002000059/d10k405.htm) confirms the net losses and the incorrect quarterly revenue. WMT's [1996 Q1 10-Q](https://stock.walmart.com/sec-filings/all-sec-filings/content/0000104169-96-000006/10-Q.txt) identifies the missing long-term debt and capital leases. The isolated MSFT test changes metadata only for diagnostic isolation; those synthetic filing dates and zero long-term debt are not represented as official historical facts.

JNJ was independently traced to the **consolidated** cash-flow statement rather than a segment or mismatched fiscal period. Its [2026 Q2 10-Q, page 7](https://www.sec.gov/Archives/edgar/data/200406/000020040626000153/jnj-20260628.htm) reports H1 CFO 11.130b, capex 2.370b, and ending cash 20.422b. H1 2025 comparative CFO 8.052b agrees with the earlier filing. The provider's Q1 CFO is the correct 2.514b, so Q2 should be 8.616b; it supplies 4.711b. The same provider's cash-flow ending cash is 9.907b while its balance-sheet cash is the correct 20.422b. This is why the new general cash-consistency rule rejects P/FCF without a ticker-specific repair.

## Independent EV checks and limits

Two 2025-03-31 EV/Revenue cases have explicitly matched official debt/cash scopes:

| Company and statement | Official debt bridge (USD billions) | Cash/investments | New EV/Revenue | Independent reference | Relative error |
| --- | --- | ---: | ---: | ---: | ---: |
| MSFT, 2024-12-31 | Debt 44.970 + finance leases 36.077 + operating leases 21.862 = 102.909 | 71.555 | 10.827918 | 10.827903 | 0.000141% |
| WMT, 2025-01-31 | Debt 35.999 + short-term borrowing 3.068 + finance leases 6.723 + operating leases 14.324 = 60.114 | 9.037 | 1.116390 | 1.116390 | <0.000001% |

These match the provider's **lease-inclusive** debt definition. Sources: [MSFT 2024 December 10-Q](https://www.sec.gov/Archives/edgar/data/789019/000095017025010491/msft-20241231.htm), [MSFT lease note](https://www.sec.gov/Archives/edgar/data/789019/000095017025010491/R22.htm), and [WMT FY2025 10-K debt and lease notes](https://www.sec.gov/Archives/edgar/data/104169/000010416925000021/wmt-20250131.htm). MSFT's 4m cash difference is visible in the small residual. Official debt-component facts and filing identifiers are preserved in the fixture.

For WMT and CAT's 2025 anchor, SEC D&A matches the provider's component (12.973b and 2.153b respectively); adding provider EBIT reproduces provider EBITDA (42.010b and 16.038b). This verifies the D&A component and the stated arithmetic, **not an independently certified EBIT definition or GAAP EBITDA multiple**. Other EV values remain marked unverified where their debt/cash/EBITDA scope has not been closed independently.

XOM's 2025 P/S is 3.0473% above the comparison using SEC `Revenues`: provider revenue is 339.247b, while the SEC total is 349.585b and includes additional income categories. The scope exception is allowed only when both these exact denominators are present and the actual P/S equals same-share equity value divided by 339.247b within numerical precision; an arbitrary error for this ticker/date still fails. This is recorded as a declared denominator-scope difference, not corrected by applying a numerical multiplier. See the [XOM 2024 10-K](https://www.sec.gov/Archives/edgar/data/34088/000003408825000010/0000034088-25-000010-index.html).

## Coverage impact

The fixes deliberately reduce coverage when an input is contradictory or lacks evidence. They do not restore missing historical statements. Valid-observation counts below are for this 11-company fixture and should not be interpreted as full-market coverage:

| Metric | Old cached valid observations | New valid observations |
| --- | ---: | ---: |
| P/E | 39,467 | 22,896 |
| P/S | 42,266 | 31,456 |
| P/B | 39,367 | 34,795 |
| P/FCF | 33,669 | 17,405 |
| EV/Revenue | 34,106 | 26,791 |
| EV/EBITDA | 31,856 | 21,032 |

Only AAPL, MSFT, AMZN, and WMT have decades of actual chart observations in these caches. The other seven start in 2024. Except AAPL, historical split-only prices are recovered from the previously cached valuation basis; missing old valuation bases use the cached chart price as a fallback. Accordingly the long-history pass validates financial-input rejection and coverage, not independent historical exchange-price accuracy. AAPL uses the original EOD prices and split records. The modern Yahoo-anchor pass provides the independent price validation.

Reconstructed history can still contain later provider restatements, provider weighted-average shares, and qualified EV definitions. The unchanged high AAPL multiple and the accepted modern values are useful estimates under those declared conventions, not point-in-time backtest data or exact historical market capitalization.
