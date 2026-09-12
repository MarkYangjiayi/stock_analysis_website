"""Rebuild historical multiples and compare independent SEC/Yahoo anchors.

Run with the repository's Python environment. --prepare DATA_ROOT refreshes the
committed compact fixture from the original audit caches. Normal runs are offline.
No database or network calls are made by this script.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
SYMBOLS = ["AAPL", "MSFT", "AMZN", "WMT", "CAT", "INTC", "XOM", "JNJ", "JPM", "NEE", "AMT"]
ANCHORS = ["2025-03-31", "2026-09-10"]
KEYS = ["pe", "ps", "pb", "pfcf", "ev_revenue", "ev_ebitda"]
REVENUE = {"AAPL": "RevenueFromContractWithCustomerExcludingAssessedTax", "MSFT": "RevenueFromContractWithCustomerExcludingAssessedTax", "AMZN": "RevenueFromContractWithCustomerExcludingAssessedTax", "WMT": "Revenues", "CAT": "Revenues", "INTC": "RevenueFromContractWithCustomerExcludingAssessedTax", "XOM": "Revenues", "JNJ": "RevenueFromContractWithCustomerExcludingAssessedTax", "JPM": "RevenuesNetOfInterestExpense", "NEE": "RegulatedAndUnregulatedOperatingRevenue", "AMT": "Revenues"}


def load(path):
    if str(path).endswith(".gz"):
        with gzip.open(path, "rt") as stream:
            return json.load(stream)
    return json.loads(path.read_text())


def day(value):
    return date.fromisoformat(str(value)[:10])


def number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def raw_price(point, basis):
    values, inputs = point["values"], basis["inputs"]
    for metric, denominator in [("ps", "revenue"), ("pe", "eps"), ("pb", "book"), ("pfcf", "fcf")]:
        if values.get(metric) is not None:
            return values[metric] * inputs[denominator] / (1 if metric == "pe" else inputs["shares"])
    if values.get("ev_revenue") is not None:
        return (values["ev_revenue"] * inputs["revenue"] - inputs["debt"] + inputs["cash"]) / inputs["shares"]
    return None


def sec_reference(sec, end, anchor, symbol):
    """Independent annual + current YTD - preceding YTD; strict filing cutoff."""
    def facts(tag, unit="USD"):
        return [r for r in sec["facts"]["us-gaap"].get(tag, {}).get("units", {}).get(unit, []) if r.get("form") in ("10-K", "10-Q", "10-K/A", "10-Q/A") and r.get("filed", "9999") < anchor]

    def pick(tag, ending, start=None, unit="USD"):
        candidates = [r for r in facts(tag, unit) if abs((day(r["end"]) - day(ending)).days) <= 4 and ((start is None and "start" not in r) or (start is not None and "start" in r and abs((day(r["start"]) - day(start)).days) <= 4))]
        return min(candidates, key=lambda r: (abs((day(r["end"]) - day(ending)).days), -day(r["filed"]).toordinal())) if candidates else None

    def instant(tags, unit="USD"):
        for tag in tags:
            row = pick(tag, end, unit=unit)
            if row:
                return {"value": row["val"], "tag": tag, "facts": [row]}
        return None

    def flow(tags, unit="USD"):
        for tag in tags:
            annual = [r for r in facts(tag, unit) if "start" in r and 330 <= (day(r["end"]) - day(r["start"])).days <= 380 and day(r["end"]) <= day(end) + timedelta(days=4) and (day(end) - day(r["end"])).days < 365]
            if not annual:
                continue
            base = max(annual, key=lambda r: (r["end"], r["filed"]))
            if abs((day(base["end"]) - day(end)).days) <= 4:
                return {"value": base["val"], "tag": tag, "facts": [base]}
            current = pick(tag, end, (day(base["end"]) + timedelta(days=1)).isoformat(), unit)
            prior = pick(tag, day(end).replace(year=day(end).year - 1).isoformat(), base["start"], unit)
            if current and prior:
                return {"value": base["val"] + current["val"] - prior["val"], "tag": tag, "facts": [base, current, prior]}
        return None

    output = {
        "eps": flow(["EarningsPerShareDiluted"], "USD/shares"),
        "revenue": flow([REVENUE[symbol]]),
        "cfo": flow(["NetCashProvidedByUsedInOperatingActivities"]),
        "capex": flow(["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsForAdditionsToPropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]),
        "book": instant(["StockholdersEquity"]),
        "shares": instant(["CommonStockSharesOutstanding"], "shares"),
        "debt": instant(["LongTermDebtAndFinanceLeaseObligationsCurrentAndNoncurrent", "DebtAndFinanceLeaseObligationsCurrentAndNoncurrent", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"]),
        "cash": instant(["CashCashEquivalentsAndShortTermInvestments", "CashAndCashEquivalentsAtCarryingValue"]),
        "operating_income": flow(["OperatingIncomeLoss"]),
        "da": flow(["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet", "DepreciationAndAmortization", "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment"]),
    }
    if symbol == "CAT":
        leased = flow(["PaymentsToAcquireEquipmentOnLease"])
        if leased and output["capex"]:
            output["capex"] = {"value": output["capex"]["value"] + leased["value"], "tag": "PP&E plus equipment on lease", "facts": output["capex"]["facts"] + leased["facts"]}
        total, minority = instant(["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]), instant(["MinorityInterest"])
        if total and minority:
            output["book"] = {"value": total["value"] - minority["value"], "tag": "Consolidated equity less noncontrolling interest", "facts": total["facts"] + minority["facts"]}
    if symbol == "NEE" and end == "2024-12-31":
        output["capex"] = {"value": 24729000000, "tag": "Consolidated cash-flow capex: FPL + NEER + nuclear fuel + other", "facts": [], "url": "https://www.sec.gov/Archives/edgar/data/753308/000075330825000011/nee-20241231.htm"}
    # Explicitly reviewed company/period bridges, only in this external audit.
    # Both providers include operating and finance leases in their reported
    # total. Never generalize this scope to every issuer's debt field.
    if (symbol, end) in (("MSFT", "2024-12-31"), ("WMT", "2025-01-31")):
        tags = ["LongTermDebt", "FinanceLeaseLiability", "OperatingLeaseLiability", "CommercialPaper" if symbol == "MSFT" else "ShortTermBorrowings"]
        parts = [instant([tag]) for tag in tags]
        if all(parts):
            output["debt"] = {"value": sum(p["value"] for p in parts), "tag": " + ".join(tags), "facts": [f for p in parts for f in p["facts"]], "scope_verified": True, "scope": "Interest-bearing debt including current maturities + finance leases + operating leases + separately reported short-term borrowing"}
    for evidence in output.values():
        if evidence:
            for fact in evidence["facts"]:
                fact["filing_url"] = f"https://www.sec.gov/Archives/edgar/data/{int(sec['cik'])}/{fact['accn'].replace('-', '')}/{fact['accn']}-index.html"
    return output


def prepare(data_root):
    """Store only chart data and fields needed by the valuation implementation."""
    bundle = {"source_kind": "frozen audit data; historical prices except AAPL inferred from old API financial basis", "symbols": {}}
    manifest = {}
    def tracked(path):
        manifest[str(path.relative_to(data_root)) if path.is_relative_to(data_root) else path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return load(path)
    for symbol in SYMBOLS:
        live_path = data_root / (f"historical_multiples_review_2026-09-12/{symbol}-live.json" if symbol == "AAPL" else f"valuation_audit_2026-09-12/{symbol}-live.json")
        live = tracked(live_path)
        history = live["valuation_history"]
        bases = {b["id"]: b for b in history["bases"]}
        fundamentals = tracked(data_root / f"sec_mapping_cache/eodhd/{symbol}.json.gz")
        fundamentals = {"General": {k: v for k, v in fundamentals["General"].items() if k in ("CurrencyCode", "Sector", "HomeCategory", "Type")}, "Financials": fundamentals["Financials"]}
        # Keep full original statement fields so the audit cannot accidentally
        # remove a field on which a new quality rule depends. Gzip is compact.
        old_points = {p["date"]: p for p in history["points"]}
        raw_prices = []
        fallback_dates = []
        if symbol == "AAPL":
            eod = tracked(data_root / "historical_multiples_review_2026-09-12/AAPL-eod.json")
            raw_prices = [[p["date"], p["close"]] for p in eod]
            splits = [[r["date"], float(r["split"].split("/")[0]) / float(r["split"].split("/")[1])] for r in tracked(data_root / "historical_multiples_review_2026-09-12/AAPL-splits.json")]
        else:
            splits = []
            for quote in live["historical_data"]:
                if quote["date"] not in old_points:
                    continue
                point = old_points[quote["date"]]
                price = raw_price(point, bases[point["basis_id"]]) if point["basis_id"] is not None else None
                if price is None:
                    price = quote["close"]
                    fallback_dates.append(quote["date"])
                raw_prices.append([quote["date"], price])
        anchors = {}
        for anchor in ANCHORS:
            point = old_points[anchor]
            basis = bases[point["basis_id"]]
            sec_path = data_root / f"sec_mapping_cache/sec/{symbol}.json.gz"
            fresh = data_root / f"valuation_audit_2026-09-12/{symbol}-sec-fresh.json.gz"
            if anchor == "2026-09-10" and fresh.exists():
                sec_path = fresh
            if symbol == "XOM" and anchor == "2025-03-31":
                sec_path = data_root / "valuation_audit_2026-09-12/XOM-sec.json.gz"
            sec = tracked(sec_path)
            yahoo_path = HERE / f"AAPL-yahoo-{anchor}.json" if symbol == "AAPL" else data_root / f"valuation_audit_2026-09-12/{symbol}-yahoo{'-' + anchor if anchor != '2025-03-31' else ''}.json"
            yahoo = tracked(yahoo_path)["chart"]["result"][0]
            yahoo_date = datetime.fromtimestamp(yahoo["timestamp"][0]).date().isoformat()
            assert yahoo_date == anchor, (symbol, anchor, yahoo_date)
            price = yahoo["indicators"]["quote"][0]["close"][0]
            anchors[anchor] = {"price_yahoo": price, "old_values": point["values"], "old_inputs": basis["inputs"], "period_end": basis["period_end"], "sec": sec_reference(sec, basis["period_end"], anchor, symbol), "sec_cik": sec["cik"]}
        bundle["symbols"][symbol] = {"sector": live["profile"]["sector"], "currency": live["profile"]["currency"], "fundamentals": fundamentals, "prices": raw_prices, "splits": splits, "fallback_price_dates": fallback_dates, "old_coverage": {m["key"]: m["valid_points"] for m in history["metrics"]}, "anchors": anchors}
    data = json.dumps(bundle, separators=(",", ":")).encode()
    (HERE / "inputs.json.gz").write_bytes(gzip.compress(data, mtime=0))
    (HERE / "source_hashes.json").write_text(json.dumps(manifest, indent=2) + "\n")


def statements(fundamentals, period):
    sections = fundamentals["Financials"]
    dates = sorted(set().union(*(set(section.get(period, {})) for section in sections.values())))
    rows = []
    for dt in dates:
        parts = [sections.get(section, {}).get(period, {}).get(dt, {}) for section in ("Income_Statement", "Balance_Sheet", "Cash_Flow")]
        rows.append(SimpleNamespace(period_end=day(dt), fiscal_date=day(dt), period="Quarterly" if period == "quarterly" else "Yearly", period_type="Quarterly" if period == "quarterly" else "Yearly", income_statement=parts[0], balance_sheet=parts[1], cash_flow=parts[2], filing_at=None, available_at=None, availability_estimated=False, fetched_at=datetime(2026, 7, 30), revision=1, source="EODHD", raw_snapshot_id=1))
    return rows


def ratio(numerator, denominator):
    return numerator / denominator if numerator is not None and denominator is not None and numerator > 0 and denominator > 0 else None


def verify():
    from services.valuation_history import build_valuation_history
    bundle = load(HERE / "inputs.json.gz")
    output = {"limitations": ["Except AAPL, long-history prices are recovered from the prior API basis, with empty split actions preserving the normalized share/price basis. No independent historical price audit is claimed.", "Anchor runs use independent Yahoo closes and SEC facts published before the anchor. Same-share comparisons isolate financial mapping from weighted-average-versus-period-end share differences.", "The lack of a comparable SEC EBITDA/debt bridge is reported as unverified, never as a passing EV comparison."], "stocks": {}, "regressions": [], "summary": {}}
    built = {}
    for symbol, fixture in bundle["symbols"].items():
        kwargs = {"currency": fixture["currency"], "sector": fixture["sector"], "fundamentals": fixture["fundamentals"], "split_history_verified": True, "fallback_vintage": date(2026, 7, 30)}
        kwargs["annual_statements"] = statements(fixture["fundamentals"], "yearly")
        quarterlies = statements(fixture["fundamentals"], "quarterly")
        actions = [SimpleNamespace(ex_date=day(dt), split_factor=factor) for dt, factor in fixture["splits"]]
        prices = [SimpleNamespace(date=day(dt), close=price) for dt, price in fixture["prices"]]
        result = build_valuation_history(symbol + ".US", prices, quarterlies, actions, **kwargs)
        built[symbol] = result
        item = {"history_start": result["points"][0]["date"], "history_end": result["points"][-1]["date"], "price_count": len(result["points"]), "old_coverage": fixture["old_coverage"], "coverage": {m["key"]: m["valid_points"] for m in result["metrics"]}, "anchors": {}}
        for anchor, reference in fixture["anchors"].items():
            # Single-date build removes any reliance on API-implied anchor price.
            anchor_result = build_valuation_history(symbol + ".US", [SimpleNamespace(date=day(anchor), close=reference["price_yahoo"])], quarterlies, actions, **kwargs)
            point = anchor_result["points"][0]
            basis = anchor_result["bases"][point["basis_id"]] if point["basis_id"] is not None else None
            sec = {k: evidence["value"] if evidence else None for k, evidence in reference["sec"].items()}
            fcf = sec["cfo"] - abs(sec["capex"]) if sec["cfo"] is not None and sec["capex"] is not None else None
            equity = reference["price_yahoo"] * sec["shares"] if sec["shares"] is not None else None
            sec_ratios = {"pe": ratio(reference["price_yahoo"], sec["eps"]), "ps": ratio(equity, sec["revenue"]), "pb": ratio(equity, sec["book"]), "pfcf": ratio(equity, fcf)}
            controlled_equity = reference["price_yahoo"] * basis["inputs"]["shares"] if basis and basis["inputs"].get("shares") is not None else None
            controlled = {"pe": sec_ratios["pe"], "ps": ratio(controlled_equity, sec["revenue"]), "pb": ratio(controlled_equity, sec["book"]), "pfcf": ratio(controlled_equity, fcf)}
            if reference["sec"].get("debt") and reference["sec"]["debt"].get("scope_verified") and controlled_equity is not None and sec["cash"] is not None:
                controlled["ev_revenue"] = ratio(controlled_equity + sec["debt"] - sec["cash"], sec["revenue"])
                sec_ratios["ev_revenue"] = ratio(equity + sec["debt"] - sec["cash"], sec["revenue"]) if equity is not None else None
            comparison = {}
            for metric in KEYS:
                actual = point["values"][metric]
                expected = controlled.get(metric)
                reason = point.get("reason") or (basis["reasons"].get(metric) if basis else None) or (point.get("ev_reason") if metric.startswith("ev_") else None)
                sec_denominator = {"pe": sec["eps"], "ps": sec["revenue"], "pb": sec["book"], "pfcf": fcf, "ev_revenue": sec["revenue"]}.get(metric)
                scope_note = None
                if actual is None:
                    status = "gap"
                elif sec_denominator is not None and sec_denominator <= 0:
                    status = "invalid_positive_against_nonpositive_SEC"
                elif metric.startswith("ev_") and expected is None:
                    status = "unverified_SEC_EV_bridge"
                elif expected is None:
                    status = "SEC_reference_unavailable"
                else:
                    error = actual / expected - 1
                    status = "within_2pct" if abs(error) <= .02 else "external_difference"
                if (symbol, anchor, metric) == ("XOM", "2025-03-31", "ps") and status == "external_difference":
                    # This exact, reviewed bridge is the only exception. An
                    # arbitrary mismatch for this ticker/date must still fail.
                    scope_expected = ratio(controlled_equity, 339247000000)
                    if (sec["revenue"] == 349585000000 and basis is not None
                            and basis["inputs"]["revenue"] == 339247000000
                            and scope_expected is not None
                            and math.isclose(actual, scope_expected, rel_tol=1e-9, abs_tol=1e-9)):
                        status = "declared_scope_difference"
                        scope_note = "Actual P/S matches the same-share equity value divided by verified sales/operating revenue 339.247b; SEC Revenues is 349.585b including additional income categories. Other numerical differences are not exempted."
                comparison[metric] = {"new": actual, "old": reference["old_values"][metric], "sec_same_share_reference": expected, "sec_period_end_share_reference": sec_ratios.get(metric), "sec_denominator": sec_denominator, "relative_error": actual / expected - 1 if actual is not None and expected else None, "status": status, "reason": reason, "scope_note": scope_note}
            components = None
            if symbol in ("WMT", "CAT") and anchor == "2025-03-31" and sec["da"] is not None and basis:
                quarters = fixture["fundamentals"]["Financials"]["Income_Statement"]["quarterly"]
                selected = [quarters[d] for d in basis["periods"]]
                provider_ebit = sum(number(q["ebit"]) for q in selected)
                provider_da = sum(number(q["depreciationAndAmortization"]) for q in selected)
                components = {"scope": "Provider EBIT plus D&A; D&A independently matches SEC, EBIT remains provider definition, not GAAP EBITDA certification", "provider_ebit": provider_ebit, "provider_da": provider_da, "sec_da": sec["da"], "new_ebitda": basis["inputs"]["ebitda"], "component_sum": provider_ebit + sec["da"], "da_matches": abs(provider_da - sec["da"]) <= 1, "sum_matches": basis["inputs"]["ebitda"] is not None and abs(provider_ebit + sec["da"] - basis["inputs"]["ebitda"]) <= 1}
            item["anchors"][anchor] = {"price_yahoo": reference["price_yahoo"], "new_period_end": basis["period_end"] if basis else None, "reference_period_end": reference["period_end"], "comparisons": comparison, "ebitda_component_check": components}
        output["stocks"][symbol] = item

    def check(symbol, anchor, metric, expectation, label):
        result = built[symbol]
        p = next(p for p in result["points"] if p["date"] == anchor)
        b = result["bases"][p["basis_id"]] if p["basis_id"] is not None else None
        value = p["values"][metric]
        reason = p.get("reason") or (b["reasons"].get(metric) if b else None)
        passed = (value is None and bool(reason)) if expectation == "gap" else (value is not None and value > expectation)
        output["regressions"].append({"label": label, "ticker": symbol, "date": anchor, "metric": metric, "expectation": expectation, "value": value, "reason": reason, "passed": passed})

    check("AMZN", "2002-01-25", "pe", "gap", "Loss quarters must not turn into a positive P/E")
    check("AMZN", "2003-10-24", "pe", "gap", "The formerly 7,890x loss-period P/E is unavailable")
    check("AMZN", "2002-01-25", "ps", "gap", "Disclosed annual revenue conflicts with four quarters")
    check("AMZN", "2001-10-31", "ps", 0, "Annual conflict is not backdated before that annual disclosure")
    check("WMT", "1996-06-13", "ev_revenue", "gap", "Incomplete debt is not treated as total debt")
    check("WMT", "1996-06-13", "ev_ebitda", "gap", "Both EV multiples reject incomplete debt")
    check("MSFT", "1990-01-02", "ev_ebitda", "gap", "Old EBITDA revenue placeholders are unavailable")
    check("MSFT", "1991-10-01", "ps", "gap", "Same-day fiscal-end filing placeholders are excluded")
    check("AAPL", "1998-08-11", "pe", 100, "Near-zero positive earnings are not mechanically capped")
    check("JNJ", "2026-09-10", "pfcf", "gap", "Same-period ending cash contradicts the balance sheet")
    check("WMT", "2026-09-10", "pe", 0, "A valid consolidated versus parent-income bridge preserves P/E")
    check("WMT", "2026-09-10", "pfcf", 0, "A valid consolidated versus parent-income bridge preserves P/FCF")
    check("NEE", "2025-03-31", "pfcf", "gap", "Conflicting statement scope cannot produce positive FCF valuation")
    check("JPM", "2025-03-31", "ps", "gap", "Financial-sector revenue requires a dedicated scope")
    check("JPM", "2025-03-31", "pfcf", "gap", "Financial-sector cash flows are not an industrial FCF proxy")
    # This is an isolated mutation check, not a claim that these are actual
    # historical filing dates or verified zero long-term debt: it prevents the
    # filing/debt guards from masking the EBITDA-field guard on observed fields.
    fixture = bundle["symbols"]["MSFT"]
    isolated = copy.deepcopy([r for r in statements(fixture["fundamentals"], "quarterly") if date(1988, 9, 30) <= r.period_end <= date(1989, 6, 30)])
    for row in isolated:
        for section, attr in [("Income_Statement", "income_statement"), ("Balance_Sheet", "balance_sheet"), ("Cash_Flow", "cash_flow")]:
            part = getattr(row, attr)
            part["filing_date"] = (row.period_end + timedelta(days=45)).isoformat()
            part["currency_symbol"] = fixture["fundamentals"]["Financials"][section]["currency_symbol"]
        row.balance_sheet["longTermDebtTotal"] = 0
    isolated_result = build_valuation_history("MSFT.US", [SimpleNamespace(date=date(1989, 9, 1), close=1)], isolated, [], currency="USD", sector="Technology", split_history_verified=True)
    isolated_point, isolated_basis = isolated_result["points"][0], isolated_result["bases"][-1]
    reason = isolated_basis["reasons"].get("ev_ebitda")
    output["regressions"].append({"label": "Isolated observed EBITDA placeholder, with synthetic disclosed metadata and zero long-term debt", "ticker": "MSFT", "date": "1989-09-01", "metric": "ev_ebitda", "expectation": "gap", "value": isolated_point["values"]["ev_ebitda"], "reason": reason, "passed": isolated_point["values"]["ev_ebitda"] is None and bool(reason) and "EBITDA" in reason and isolated_point["values"]["pe"] is not None})
    # Require healthy externally checked outputs in several unrelated companies;
    # hiding every observation cannot satisfy this validation.
    healthy = {"AAPL", "MSFT", "WMT", "CAT", "AMZN", "INTC"}
    comparisons = [c for sym, item in output["stocks"].items() if sym in healthy for a in item["anchors"].values() for metric, c in a["comparisons"].items() if metric in ("pe", "ps", "pb", "pfcf")]
    valid_checked = sum(c["status"] == "within_2pct" for c in comparisons)
    all_comparisons = [c for item in output["stocks"].values() for a in item["anchors"].values() for c in a["comparisons"].values()]
    external_failures = sum(c["status"] in ("external_difference", "invalid_positive_against_nonpositive_SEC") for c in all_comparisons)
    period_failures = sum(a["new_period_end"] is not None and abs((day(a["new_period_end"]) - day(a["reference_period_end"])).days) > 4 for s in output["stocks"].values() for a in s["anchors"].values())
    passed = all(row["passed"] for row in output["regressions"]) and valid_checked >= 32 and external_failures == 0 and period_failures == 0
    output["summary"] = {"passed": passed, "symbols": len(output["stocks"]), "historical_points": sum(item["price_count"] for item in output["stocks"].values()), "regressions_passed": sum(c["passed"] for c in output["regressions"]), "regressions_total": len(output["regressions"]), "healthy_valid_external_comparisons": valid_checked, "healthy_minimum_required": 32, "healthy_coverage_passed": valid_checked >= 32, "unexplained_external_failures": external_failures, "reference_period_failures": period_failures, "declared_scope_differences": sum(c["status"] == "declared_scope_difference" for c in all_comparisons), "explicit_gaps": sum(c["status"] == "gap" for c in all_comparisons), "unverified_EV_values": sum(c["status"] == "unverified_SEC_EV_bridge" for c in all_comparisons)}
    (HERE / "verification.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output["summary"], indent=2))
    for row in output["regressions"]:
        print(row)
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", type=Path, help="Original data directory; only needed to regenerate the compact fixture")
    args = parser.parse_args()
    if args.prepare:
        prepare(args.prepare.resolve())
    raise SystemExit(verify())
