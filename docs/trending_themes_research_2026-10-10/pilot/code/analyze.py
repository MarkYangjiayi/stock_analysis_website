"""Attention-lead pilot: weekly attention series, robust z vs trailing 52w median/MAD, first z>2 / z>3 weeks,
compared with ETF NAV breakouts, ETF inception and catalyst dates. Weeks end Friday (W-FRI)."""
import json, os, sys, glob, datetime as dt
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__)); from config import EPISODES, CONTROLS
D = sys.argv[1]
WK = "W-FRI"
def fname(x): return x.replace("/", "_").replace(" ", "_")
# ---------- loaders ----------
EXTRA_WIKI = {"Generative artificial intelligence": ["Generative artificial intelligence"],  # redirect titles summed with canonical
              "Environmental impact of artificial intelligence": ["Environmental impact of artificial intelligence"],
              "Semaglutide": ["Ozempic", "Wegovy"]}
def wiki_daily(title):
    rec = json.load(open(os.path.join(D, "wiki", title.replace("/", "_") + ".json")))
    s = pd.Series({pd.Timestamp(k): v for k, v in rec.get("daily", {}).items()}, dtype=float)
    for t2 in EXTRA_WIKI.get(title, []):
        p = os.path.join(D, "wiki_raw", t2 + ".json")
        if os.path.exists(p):
            r2 = json.load(open(p)); s2 = pd.Series({pd.Timestamp(k): v for k, v in r2.get("daily", {}).items()}, dtype=float)
            s = s.add(s2, fill_value=0) if t2 != rec.get("canonical") else s
    idx = pd.date_range("2015-07-01", "2026-10-09", freq="D")
    return s.reindex(idx).fillna(0.0)          # missing day = 0 views (article absent / no traffic)
def wiki_weekly(titles):
    return sum(wiki_daily(t) for t in titles).resample(WK).sum()
def hn_weekly(phrases):
    ids = {}
    for p in phrases:
        ids.update(json.load(open(os.path.join(D, "hn", fname(p) + ".json")))["hits"])   # dedupe across phrases by objectID
    ts = pd.to_datetime([v[0] for v in ids.values()], unit="s")
    s = pd.Series(1.0, index=ts).sort_index()
    return s.resample(WK).sum().reindex(pd.date_range("2014-01-03", "2026-10-09", freq=WK)).fillna(0.0)
def edgar_weekly(phrases):
    if not phrases: return None
    f = {}
    for p in phrases:
        f.update(json.load(open(os.path.join(D, "edgar", fname(p) + ".json")))["filings"])  # dedupe by accession (adsh)
    s = pd.Series(1.0, index=pd.to_datetime([v[0] for v in f.values()])).sort_index()
    return s.resample(WK).sum().reindex(pd.date_range("2015-01-02", "2026-10-09", freq=WK)).fillna(0.0)
def edgar_newcik_weekly(phrases):
    if not phrases: return None
    f = {}
    for p in phrases:
        f.update(json.load(open(os.path.join(D, "edgar", fname(p) + ".json")))["filings"])
    df = pd.DataFrame([(pd.Timestamp(v[0]), v[1]) for v in f.values()], columns=["d", "cik"]).sort_values("d")
    first = df.groupby("cik")["d"].min()                  # first week each company used the phrase (since 2015)
    s = pd.Series(1.0, index=first.values).sort_index()
    return s.resample(WK).sum().reindex(pd.date_range("2015-01-02", "2026-10-09", freq=WK)).fillna(0.0)
def nav_weekly(tk):
    r = json.load(open(os.path.join(D, "nav", tk + ".json")))
    s = pd.Series({pd.Timestamp(d): v for d, v in r["nav"]}).sort_index()
    return s.resample(WK).last().dropna()
# ---------- signal rules ----------
def robust_z(x, floor_abs=1.0, floor_rel=0.0, win=52):
    med = x.shift(1).rolling(win, min_periods=win).median()
    mad = (x.shift(1) - med).abs().rolling(win, min_periods=win).median()    # approx MAD over trailing window
    # exact trailing MAD: median(|x_i - median|) over the same window
    vals = x.values; m2 = np.full(len(x), np.nan); s2 = np.full(len(x), np.nan)
    for i in range(win, len(x)):
        w = vals[i - win:i]; m = np.median(w); m2[i] = m; s2[i] = np.median(np.abs(w - m))
    med = pd.Series(m2, index=x.index); mad = pd.Series(s2, index=x.index)
    scale = np.maximum(1.4826 * mad, np.maximum(floor_abs, floor_rel * med))
    return (x - med) / scale, med
def first_fire(z, x, thr, start, end, min_level=0.0, persist=False):
    zz = z[(z.index >= start) & (z.index <= end)]; xx = x.reindex(zz.index)
    hit = (zz > thr) & (xx >= min_level)
    if persist:   # z>thr in >=2 of 3 consecutive weeks (first week of that run)
        h = hit.astype(int); r = h.rolling(3, min_periods=3).sum().shift(-2)
        hit = hit & (r >= 2)
    return hit[hit].index[0] if hit.any() else None
def breakout(p, start, end):
    hi = p.shift(1).rolling(52, min_periods=40).max(); r13 = p / p.shift(13) - 1
    ok = (p > hi) & (r13 > 0.20) & (p.index >= start) & (p.index <= end)
    return ok[ok].index[0] if ok.any() else None
def fire_rate(z, x, thr, min_level, start="2017-01-01", end="2026-10-09", persist=False):
    zz = z[(z.index >= start) & (z.index <= end)].dropna(); xx = x.reindex(zz.index)
    hit = (zz > thr) & (xx >= min_level)
    if persist:
        h = hit.astype(int); hit = hit & (h.rolling(3, min_periods=3).sum().shift(-2) >= 2)
    return float(hit.mean())
def lift(z, x, thr, min_level, ref, base, weeks=26):
    if ref is None or not base: return None
    zz = z[(z.index < pd.Timestamp(ref)) & (z.index >= pd.Timestamp(ref) - pd.Timedelta(weeks=weeks))]
    return round(float(((zz > thr) & (x.reindex(zz.index) >= min_level)).mean()) / base, 1)
# ---------- episode metadata (dates verified separately; see report) ----------
META = {
 "cannabis_2018":  dict(win=("2017-07-01","2019-06-30"), etf=("MJ (repurposed)","2017-12-26"), price=None, cats=[("2018-06-21","Cannabis Act royal assent (Canada)"),("2018-08-15","Constellation +C$5B Canopy"),("2018-10-17","Canada legalization in force"),("2018-12-20","2018 Farm Bill signed")]),
 "ev_clean_2020":  dict(win=("2019-07-01","2021-06-30"), etf=("KARS","2018-01-18"), price="SMOG", cats=[("2020-11-07","US election called"),("2020-11-16","Tesla S&P 500 add announced"),("2021-01-06","Georgia runoff results")]),
 "spac_2020":      dict(win=("2019-07-01","2021-06-30"), etf=("SPAK","2020-09-30"), price=None, cats=[("2020-06-04","Nikola lists via SPAC"),("2020-07-22","Pershing Square Tontine IPO"),("2021-04-12","SEC staff statement on SPAC warrants")]),
 "metaverse_2021": dict(win=("2020-07-01","2022-06-30"), etf=("METV","2021-06-30"), price="ESPO", cats=[("2021-03-10","Roblox direct listing"),("2021-10-28","Facebook renames to Meta")]),
 "genai_2023":     dict(win=("2022-07-01","2024-06-30"), etf=("CHAT","2023-05-18"), price="SMH", cats=[("2022-11-30","ChatGPT release"),("2023-03-14","GPT-4"),("2023-05-24","NVIDIA Q1 FY24 guide")]),
 "ai_power_2024":  dict(win=("2023-07-01","2025-06-30"), etf=("VOLT","2024-12-03"), price="XLU", cats=[("2024-03-04","Talen-AWS data-center campus"),("2024-09-20","Constellation-Microsoft Crane PPA"),("2025-01-21","Stargate announced")]),
 "nuclear_2024":   dict(win=("2023-07-01","2025-06-30"), etf=("NUKZ","2024-01-23"), price="NLR", cats=[("2023-12-02","COP28 triple-nuclear pledge"),("2024-07-09","ADVANCE Act signed"),("2024-09-20","Microsoft-Constellation TMI PPA"),("2024-10-14","Google-Kairos SMR deal"),("2024-10-16","Amazon-X-energy SMR deal")]),
 "quantum_2024":   dict(win=("2024-01-01","2025-12-31"), etf=("QTUM","2018-09-04"), price=None, cats=[("2024-12-09","Google Willow"),("2025-02-19","Microsoft Majorana 1")]),
 "glp1_2023":      dict(win=("2022-07-01","2024-06-30"), etf=("HRTS","2023-11-20"), price="PPH", cats=[("2021-06-04","Wegovy approved"),("2023-08-08","SELECT topline"),("2023-11-08","Zepbound approved")]),
 "humanoid_2025":  dict(win=("2024-07-01","2026-06-30"), etf=("KOID","2025-06-04"), price="IBOT", cats=[("2025-01-06","NVIDIA CES physical-AI keynote"),("2025-03-18","NVIDIA GR00T N1")]),
 "stablecoin_2025":dict(win=("2024-07-01","2026-03-31"), etf=("STBQ","2025-12-22"), price="DAPP", cats=[("2024-10-21","Stripe to buy Bridge"),("2025-06-05","Circle IPO"),("2025-06-17","Senate passes GENIUS"),("2025-07-18","GENIUS Act signed")]),
 "space_2025":     dict(win=("2024-07-01","2026-06-30"), etf=("UFO","2019-04-11"), price="ROKT", cats=[("2025-01-27","Iron Dome for America EO"),("2025-05-20","Golden Dome architecture"),("2025-08-07","Firefly IPO")]),
 "drones_2025":    dict(win=("2024-07-01","2026-06-30"), etf=("DRNZ","2025-10-29"), price="XAR", cats=[("2025-06-01","Operation Spiderweb"),("2025-06-06","Drone Dominance EO"),("2025-07-10","DoD drone-dominance memo")]),
 "memory_2025":    dict(win=("2025-01-01","2026-09-30"), etf=("DRAM","2026-04-02"), price="SMH", cats=[("2025-10-01","Samsung/SK hynix join Stargate"),("2025-12-17","Micron FQ1-26 results")]),
}
CTRL_WIN = {"nft":("2020-07-01","2022-06-30"),"web3":("2021-01-01","2022-12-31"),"lk99":("2023-01-01","2024-06-30"),
 "rt_superconductor":("2023-01-01","2024-06-30"),"metaverse_post2022":("2023-01-01","2026-09-30"),"cultured_meat":("2019-01-01","2024-12-31"),
 "vertical_farming":("2019-01-01","2023-12-31"),"fusion":("2021-01-01","2024-12-31"),"vision_pro":("2023-01-01","2024-12-31"),"clubhouse":("2020-07-01","2022-06-30")}
SRC_PARAMS = {"wiki": dict(floor_abs=1.0, floor_rel=0.05, min_level=0), "hn": dict(floor_abs=1.0, floor_rel=0.0, min_level=3), "edgar": dict(floor_abs=1.0, floor_rel=0.0, min_level=3), "edgar_new": dict(floor_abs=1.0, floor_rel=0.0, min_level=3)}
def series_for(cfg):
    return {"wiki": wiki_weekly(cfg["wiki"]) if cfg["wiki"] else None, "hn": hn_weekly(cfg["hn"]) if cfg["hn"] else None, "edgar": edgar_weekly(cfg["edgar"]), "edgar_new": edgar_newcik_weekly(cfg["edgar"])}
def wk(d): return None if d is None else pd.Timestamp(d)
def diff_w(a, b):  # weeks from attention fire a to reference b (positive = attention led)
    return None if a is None or b is None else round((pd.Timestamp(b) - pd.Timestamp(a)).days / 7)
rows, crow, rates = [], [], []
for name, cfg in EPISODES.items():
    m = META[name]; s0, s1 = m["win"]; ser = series_for(cfg)
    px = nav_weekly(m["price"]) if m["price"] else None
    bo = breakout(px, s0, s1) if px is not None else None
    peak = px[(px.index >= s0) & (px.index <= pd.Timestamp(s1) + pd.Timedelta(weeks=26))].idxmax() if px is not None else None
    cat1 = m["cats"][0][0]; etf_d = m["etf"][1]
    for src, x in ser.items():
        if x is None: continue
        pr = SRC_PARAMS[src]; z, med = robust_z(x, pr["floor_abs"], pr["floor_rel"])
        f2 = first_fire(z, x, 2, s0, s1, pr["min_level"]); f3 = first_fire(z, x, 3, s0, s1, pr["min_level"])
        f3p = first_fire(z, x, 3, s0, s1, pr["min_level"], persist=True)
        r3 = fire_rate(z, x, 3, pr["min_level"]); r3p = fire_rate(z, x, 3, pr["min_level"], persist=True)
        rates.append(dict(series=f"{name}:{src}", src=src, rate_z2=fire_rate(z, x, 2, pr["min_level"]), rate_z3=r3, rate_z3p=r3p))
        bo_ok = bo if (bo is not None and bo > pd.Timestamp(s0) + pd.Timedelta(weeks=4)) else None
        nweeks = (pd.Timestamp(bo_ok) - pd.Timestamp(s0)).days / 7 if bo_ok is not None else None
        nz = (x.shift(1) > 0).rolling(52, min_periods=52).sum()
        art = bool(src == "wiki" and f3 is not None and nz.get(f3, 52) < 40)
        etf_in_win = pd.Timestamp(etf_d) >= pd.Timestamp(s0)
        rows.append(dict(creation_artifact=art, etf_in_win=etf_in_win, bo_valid=bo_ok is not None, chance_z3_before_bo=None if nweeks is None else round(1 - (1 - r3) ** nweeks, 2),
            chance_z3p_before_bo=None if nweeks is None else round(1 - (1 - r3p) ** nweeks, 2),
            lift26_etf=lift(z, x, 3, pr["min_level"], etf_d, r3), lift26_bo=lift(z, x, 3, pr["min_level"], bo_ok, r3), base_rate_z3=round(r3, 3), base_rate_z3p=round(r3p, 3),episode=name, src=src, z2=f2 and f2.date(), z3=f3 and f3.date(), z3persist=f3p and f3p.date(),
            breakout=bo and bo.date(), peak=peak and peak.date(), etf=m["etf"][0], etf_date=etf_d, first_catalyst=cat1,
            lead_bo_z3=diff_w(f3, bo), lead_bo_z3p=diff_w(f3p, bo), lead_peak_z3=diff_w(f3, peak), lead_etf_z3=diff_w(f3, etf_d),
            lead_cat_z3=diff_w(f3, cat1), lead_etf_z2=diff_w(f2, etf_d), lead_bo_z2=diff_w(f2, bo),
            base_median_prewin=float(med.reindex([pd.Timestamp(s0)], method="nearest").iloc[0]) if len(med.dropna()) else None,
            max_in_win=float(x[(x.index >= s0) & (x.index <= s1)].max())))
for name, cfg in CONTROLS.items():
    s0, s1 = CTRL_WIN[name]; ser = series_for(cfg)
    for src, x in ser.items():
        if x is None: continue
        pr = SRC_PARAMS[src]; z, med = robust_z(x, pr["floor_abs"], pr["floor_rel"])
        f2 = first_fire(z, x, 2, s0, s1, pr["min_level"]); f3 = first_fire(z, x, 3, s0, s1, pr["min_level"])
        f3p = first_fire(z, x, 3, s0, s1, pr["min_level"], persist=True)
        zz = z[(z.index >= s0) & (z.index <= s1)]
        crow.append(dict(control=name, src=src, z2=f2 and f2.date(), z3=f3 and f3.date(), z3persist=f3p and f3p.date(),
                         max_z=round(float(zz.max()), 1) if zz.notna().any() else None, weeks_z3=int(((zz > 3) & (x.reindex(zz.index) >= pr["min_level"])).sum()),
                         max_weekly=float(x[(x.index >= s0) & (x.index <= s1)].max())))
        rates.append(dict(series=f"CTRL {name}:{src}", src=src, rate_z2=fire_rate(z, x, 2, pr["min_level"]), rate_z3=fire_rate(z, x, 3, pr["min_level"]), rate_z3p=fire_rate(z, x, 3, pr["min_level"], persist=True)))
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 200)
R = pd.DataFrame(rows); C = pd.DataFrame(crow); RT = pd.DataFrame(rates)
R.to_csv(os.path.join(D, "episode_results.csv"), index=False); C.to_csv(os.path.join(D, "control_results.csv"), index=False); RT.to_csv(os.path.join(D, "fire_rates.csv"), index=False)
print(R[["episode","src","creation_artifact","z2","z3","z3persist","breakout","bo_valid","peak","etf_date","first_catalyst","lead_bo_z2","lead_bo_z3","lead_bo_z3p","lead_etf_z2","lead_etf_z3","lead_cat_z3","chance_z3_before_bo","chance_z3p_before_bo","lift26_etf","lift26_bo","base_rate_z3","base_rate_z3p"]].to_string())
print(C.to_string())
print(RT.groupby("src")[["rate_z2","rate_z3","rate_z3p"]].median().to_string())
# summary per source
def p_before(col, sub):
    v = sub[col].dropna(); return f"{int((v > 0).sum())}/{len(v)} (median {v.median():+.0f}w)" if len(v) else "n/a"
for src in ["wiki", "hn", "edgar", "edgar_new"]:
    sub = R[R.src == src]; vb = sub[sub.bo_valid]
    lead_p3p = (vb["z3persist"].notna() & (vb["lead_bo_z3p"] > 0))
    print(src, "| z2 before valid breakout:", p_before("lead_bo_z2", vb), "| z3 before valid breakout:", p_before("lead_bo_z3", vb),
          "| z3persist before valid breakout:", f"{int(lead_p3p.sum())}/{len(vb)}",
          "| z3 before ETF inception (ETF launched in window):", p_before("lead_etf_z3", sub[sub.etf_in_win]), "| z2 before ETF:", p_before("lead_etf_z2", sub[sub.etf_in_win]),
          "| median lift26 pre-ETF:", sub[sub.etf_in_win]["lift26_etf"].median(), "| median lift26 pre-breakout:", vb["lift26_bo"].median(),
          "| z3 before 1st catalyst:", p_before("lead_cat_z3", sub), "| fired z3 in window:", f"{int(sub.z3.notna().sum())}/{len(sub)}")
for src in ["wiki", "hn", "edgar", "edgar_new"]:
    sub = C[C.src == src]
    print("CTRL", src, "z3 fired:", f"{int(sub.z3.notna().sum())}/{len(sub)}", "z3persist fired:", f"{int(sub.z3persist.notna().sum())}/{len(sub)}")
