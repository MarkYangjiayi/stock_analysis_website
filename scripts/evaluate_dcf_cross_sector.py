"""Reproducible cross-sector DCF diagnostic; no production model mutations.

The panel and perturbations are fixed before fetching. Prices and third-party
valuations are comparison points, never fitting targets. Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PANEL = {
    "Technology": ["NVDA", "MSFT", "AAPL"],
    "Communication Services": ["GOOGL", "META", "VZ"],
    "Consumer Discretionary": ["AMZN", "HD", "MCD"],
    "Consumer Staples": ["PG", "KO", "WMT"],
    "Health Care": ["JNJ", "UNH", "LLY"],
    "Financials": ["JPM", "BAC", "PGR"],
    "Energy": ["XOM", "CVX", "SLB"],
    "Industrials": ["CAT", "UNP", "HON"],
    "Materials": ["LIN", "FCX", "NEM"],
    "Utilities": ["NEE", "DUK", "SO"],
    "Real Estate": ["PLD", "AMT", "O"],
}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def request_json(url):
    with urlopen(Request(url, headers={"User-Agent": "Finbrain-DCF-Diagnostic/1.0"}), timeout=45) as response:
        body = response.read()
    return json.loads(body), hashlib.sha256(body).hexdigest()


def fetch_one(ticker, destination):
    result = {"ticker": ticker, "retrieved_at_utc": stamp()}
    for label, suffix in (("decision", "/decision-support"), ("financials", "?financial_period=Quarterly")):
        url = f"https://finbrain.icu/api/stocks/{ticker}.US{suffix}"
        try:
            data, digest = request_json(url)
            if label == "financials":
                # Drop unrelated price/technical history, retain every financial observation.
                data = {k: data.get(k) for k in ("profile", "historical_financials", "valuation_metrics")}
            result[label] = {"url": url, "response_sha256": digest, "data": data}
        except Exception as exc:
            result[label] = {"url": url, "error": str(exc)}
    write_json(destination / "snapshots" / f"{ticker}.json", result)
    return ticker, [label for label in ("decision", "financials") if "error" in result[label]]


# Frozen v1 function retained to reproduce the original audit after engine upgrades.
def _frozen_v1_calculator(*, fcf: float, cash: float, debt: float, shares: float, fcf_growth_rate: float, wacc: float, perpetual_growth: float) -> dict[str, Any]:
    projected_fcf: list[float] = []
    present_value_fcf = 0.0
    current_fcf = fcf
    for year in range(1, 6):
        current_fcf *= 1 + fcf_growth_rate
        projected_fcf.append(current_fcf)
        present_value_fcf += current_fcf / (1 + wacc) ** year
    terminal_value = projected_fcf[-1] * (1 + perpetual_growth) / (wacc - perpetual_growth)
    present_value_terminal = terminal_value / (1 + wacc) ** 5
    enterprise_value = present_value_fcf + present_value_terminal
    equity_value = enterprise_value + cash - debt
    return {'intrinsic_value_per_share': equity_value / shares, 'enterprise_value': enterprise_value, 'equity_value': equity_value, 'projected_fcf': projected_fcf, 'present_value_explicit_fcf': present_value_fcf, 'present_value_terminal': present_value_terminal}


def production_calculator():
    return _frozen_v1_calculator


def independent_dcf(inputs, growth, rate, terminal, fade=False):
    flows = [inputs["fcf"] * (1 + growth) ** year for year in range(1, 6)]
    if fade:
        # Fixed universal ablation, not a claimed analyst forecast or optimized curve.
        for year in range(6, 11):
            year_growth = terminal + (growth - terminal) * 0.7 ** (year - 5)
            flows.append(flows[-1] * (1 + year_growth))
    pv = sum(flow / (1 + rate) ** year for year, flow in enumerate(flows, 1))
    pvt = flows[-1] * (1 + terminal) / (rate - terminal) / (1 + rate) ** len(flows)
    return (pv + pvt + inputs["cash"] - inputs["debt"]) / inputs["shares"]


def relative_change(value, base):
    return value / base - 1 if base is not None and base > 0 else None


def evaluate(snapshot, sector, calc):
    ticker = snapshot["ticker"]
    row = {"ticker": ticker, "panel_sector": sector}
    if "error" in snapshot["decision"]:
        return {**row, "available": False, "fetch_error": snapshot["decision"]["error"]}
    d = snapshot["decision"]["data"]
    v = d["valuation"]
    p = v["inputs"]
    basis = v.get("assumption_basis", {})
    w = basis.get("wacc", {})
    growth = basis.get("growth", {})
    scenarios = {s["scenario"]: s for s in v["scenarios"]}
    base = scenarios["base"]
    a = base["assumptions"]
    fit = ("equity_model_required" if sector == "Financials" else
           "reit_cashflow_model_required" if sector == "Real Estate" else
           "capital_intensive_review" if sector == "Utilities" else
           "cycle_normalization_review" if sector in ("Energy", "Materials") else
           "fcff_candidate")
    row.update({
        "provider_sector": d["metadata"].get("sector"),
        "industry": d["metadata"].get("industry"),
        "price_date": d["metadata"].get("price_date"),
        "financial_date": d["metadata"].get("financial_statement_date"),
        "currency": d["metadata"].get("currency"),
        "available": v["available"], "unavailable_reasons": v["unavailable_reasons"],
        "model_fit_review": fit, "price": v["current_price"],
        "base_value": base.get("intrinsic_value_per_share"),
        "bear_value": scenarios["bear"].get("intrinsic_value_per_share"),
        "bull_value": scenarios["bull"].get("intrinsic_value_per_share"),
        "position": v["position"]["status"],
        "wacc": w.get("wacc"), "beta": w.get("beta"), "beta_source": w.get("beta_source"),
        "wacc_quality": w.get("quality"), "wacc_notes": w.get("notes", []),
        "debt_cost_source": w.get("debt_cost_source"), "cost_of_debt": w.get("cost_of_debt"),
        "growth": a["fcf_growth_rate"], "raw_base_growth": growth.get("raw_base_growth"),
        "base_cap_hit": (growth.get("raw_base_growth") or 0) > 0.20 + 1e-10,
        "base_floor_hit": growth.get("raw_base_growth") is not None and growth["raw_base_growth"] < -0.05 - 1e-10,
        "growth_method": growth.get("method"), "scenario_spread": growth.get("scenario_spread"),
        "growth_signals": growth.get("signals", []), "terminal_growth": a["perpetual_growth"],
        "fcff": p.get("fcf"), "reported_fcf": p.get("reported_fcf"),
        "interest_adjustment": p.get("after_tax_interest_adjustment"),
        "debt_dcf": p.get("debt"), "cash_dcf": p.get("cash"), "shares_dcf": p.get("shares"),
        "implied_growth": v.get("implied_growth", {}).get("implied_fcf_growth_rate"),
    })
    hist = snapshot["financials"].get("data", {}).get("historical_financials") or []
    matched = next((q for q in hist if q["date"] == row["financial_date"]), None)
    if matched:
        reported_debt = matched.get("total_debt")
        row["debt_financial_view"] = reported_debt
        row["debt_discrepancy"] = (reported_debt is not None and p.get("debt") is not None and
                                   abs(reported_debt - p["debt"]) > max(1, abs(reported_debt) * .005))
        if row["debt_discrepancy"]:
            row["debt_bridge_delta_per_share"] = (p["debt"] - reported_debt) / p["shares"] if p.get("shares", 0) else None
        latest4 = sorted([q for q in hist if q["date"] <= row["financial_date"]], key=lambda q:q["date"])[-4:]
        row["ttm_cross_view_fcf"] = sum(q["free_cash_flow"] for q in latest4) if len(latest4) == 4 and all(q.get("free_cash_flow") is not None for q in latest4) else None
        if row["ttm_cross_view_fcf"] is not None and p.get("reported_fcf") is not None:
            row["ttm_cross_view_difference"] = row["ttm_cross_view_fcf"] - p["reported_fcf"]
    else:
        row["financial_view_error"] = snapshot["financials"].get("error", "No matching quarter")
    if v["available"] and base["available"]:
        inputs = {k:p[k] for k in ("fcf", "cash", "debt", "shares")}
        calculated = calc(**inputs, **{k:a[k] for k in ("fcf_growth_rate", "wacc", "perpetual_growth")})
        value = row["base_value"]
        independent = independent_dcf(inputs, a["fcf_growth_rate"], a["wacc"], a["perpetual_growth"])
        row["production_reproduction_error"] = calculated["intrinsic_value_per_share"] - value
        row["independent_reproduction_error"] = independent - value
        row["value_to_price"] = value / row["price"] if row.get("price") and row["price"] > 0 else None
        row["terminal_share_of_enterprise_value"] = base["present_value_terminal"] / base["enterprise_value"]
        variants = {"fade_10yr": independent_dcf(inputs, a["fcf_growth_rate"], a["wacc"], a["perpetual_growth"], True)}
        for name, rate, term in (("wacc_minus_1pp", a["wacc"]-.01,a["perpetual_growth"]), ("wacc_plus_1pp", a["wacc"]+.01,a["perpetual_growth"]), ("terminal_plus_05pp",a["wacc"],a["perpetual_growth"]+.005)):
            if .03 <= rate <= .25 and rate-term >= .005-1e-12:
                variants[name] = independent_dcf(inputs,a["fcf_growth_rate"],rate,term)
        raw = growth.get("raw_base_growth")
        if raw is not None:
            variants["remove_20pct_cap_only"] = independent_dcf(inputs,max(-.05,raw),a["wacc"],a["perpetual_growth"])
        row["diagnostic_variants"] = {name:{"value":val,"relative_change":relative_change(val,value)} for name,val in variants.items()}
    return row


def med(values):
    valid = [v for v in values if v is not None and math.isfinite(v)]
    return median(valid) if valid else None


def summarize(rows):
    available = [r for r in rows if r["available"]]
    eligible = [r for r in available if r.get("model_fit_review") not in ("equity_model_required", "reit_cashflow_model_required")]
    def subset(items):
        return {
            "n":len(items), "available":sum(r["available"] for r in items),
            "beta_fallbacks":[r["ticker"] for r in items if r.get("beta_source")=="fallback_market_beta"],
            "base_cap_hits":[r["ticker"] for r in items if r.get("base_cap_hit")],
            "debt_discrepancies":[r["ticker"] for r in items if r.get("debt_discrepancy")],
            "above_bull":[r["ticker"] for r in items if r.get("position")=="above_bull"],
            "median_value_to_price":med([r.get("value_to_price") for r in items]),
            "median_wacc":med([r.get("wacc") for r in items]),
            "median_growth":med([r.get("growth") for r in items]),
            "median_fade_change":med([r.get("diagnostic_variants",{}).get("fade_10yr",{}).get("relative_change") for r in items]),
            "median_wacc_minus_1pp_change":med([r.get("diagnostic_variants",{}).get("wacc_minus_1pp",{}).get("relative_change") for r in items]),
        }
    return {"all":subset(rows),"fcff_available_excluding_financials_reits":subset(eligible),
            "sectors":{sector:subset([r for r in rows if r["panel_sector"]==sector]) for sector in PANEL}}


def collect_beta_context(destination, refresh=False):
    path = destination/"beta_screener.json"
    if refresh or not path.exists():
        body = {"columns":["ticker","name","sector","beta_1yr","market_cap"],"limit":500}
        tickers = {ticker+".US" for members in PANEL.values() for ticker in members}
        req = Request("https://finbrain.icu/api/stocks/screener/query",data=json.dumps(body).encode(),
                      headers={"Content-Type":"application/json"},method="POST")
        with urlopen(req,timeout=45) as response:
            data = json.load(response)
        data["items"] = [item for item in data["items"] if item["ticker"] in tickers]
        write_json(path,{"request":body,"retrieved_at_utc":stamp(),"response_subset":data})
    return {item["ticker"].removesuffix(".US"):item for item in json.loads(path.read_text())["response_subset"]["items"]}


def reproduce_beta_boundary(destination):
    source = ROOT/"services/valuation_assumptions.py"
    tree = ast.parse(source.read_text())
    nodes = [n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ("_number","estimate_company_wacc")]
    namespace = {"Any":Any,"math":math}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),"exec"),namespace)
    inputs = dict(market_cap=1e11,latest_debt=0,prior_debt=0,average_debt=0,interest_expense=0,
                  income_tax_expense=21,income_before_tax=100,current_price=100,shares=1e9,
                  risk_free_rate=.04808,equity_risk_premium=.06,fallback_debt_spread=.015,
                  fallback_tax_rate=.21,assumptions_as_of="2026-09-09")
    results = []
    for beta in (-.01,0,.01,1):
        value = namespace["estimate_company_wacc"](beta=beta,**inputs)
        results.append({"input_beta":beta,"resolved_beta":value["beta"],"source":value["beta_source"],"wacc":value["wacc"]})
    write_json(destination/"beta_boundary_reproduction.json",{"fixed_inputs":inputs,"results":results})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT/"docs/valuation_cross_sector_2026-09-10")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    dest = args.output
    (dest/"snapshots").mkdir(parents=True, exist_ok=True)
    manifest_path = dest/"manifest.json"
    if not manifest_path.exists():
        write_json(manifest_path, {"locked_at_utc":stamp(),"panel":PANEL,
            "selection":"Purposive large-cap diagnostic panel, equal count by 11 broad sectors; no claim of random sampling or market representation.",
            "holdout_status":"All 33 are diagnostic observations. No model fitting. Future model selection requires new untouched names and point-in-time out-of-sample periods.",
            "rules":{"fade_years":10,"fade_excess_growth_retention":.7,"wacc_perturbation_pp":[-1,1],"terminal_perturbation_pp":.5,"debt_difference_tolerance":.005},
            "price_role":"Descriptive diagnostic only; never an error label, calibration target, or proof of fair value.",
            "source":"Public default API responses, no admin key or personal scenarios."})
    else:
        if json.loads(manifest_path.read_text())["panel"] != PANEL:
            raise ValueError("Frozen panel differs from script; use a new output directory.")
    jobs = [t for ts in PANEL.values() for t in ts if args.refresh or not (dest/"snapshots"/f"{t}.json").exists()]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(fetch_one,t,dest) for t in jobs]
        for future in as_completed(futures):
            ticker, errors = future.result()
            print(f"Fetched {ticker}: {'errors '+str(errors) if errors else 'ok'}", flush=True)
    calc = production_calculator()
    rows = [evaluate(json.loads((dest/"snapshots"/f"{t}.json").read_text()),sector,calc) for sector,ts in PANEL.items() for t in ts]
    beta_context = collect_beta_context(dest,args.refresh)
    for row in rows:
        row["observed_local_beta"] = beta_context.get(row["ticker"],{}).get("beta_1yr")
    result = {"evaluated_at_utc":stamp(),"script_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),"summary":summarize(rows),"rows":rows}
    write_json(dest/"results.json",result)
    reproduce_beta_boundary(dest)
    print(json.dumps(result["summary"],indent=2,ensure_ascii=False))


if __name__ == "__main__":
    main()
