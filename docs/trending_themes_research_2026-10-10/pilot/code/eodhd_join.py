"""Run from the QuantDashboard repo root (uses its EODHD client + settings).
   python eodhd_join.py --pilot-dir <dir with episode_results.csv> --out theme_leads.csv
Fetches daily adjusted closes from EODHD for each episode's dedicated ETF(s) and an equal-weight basket of representative
tickers, computes the weekly breakout (close > prior 52w high AND 13w return > 20%) and peak inside the episode window,
then joins with the attention fire weeks from the pilot."""
import argparse, asyncio, pandas as pd, numpy as np
from services.eodhd_client import get_eod_historical_data   # existing project client (reads EODHD key from settings)

EPISODES = {  # window, ETFs (first = dedicated), representative tickers
 "cannabis_2018":   (("2017-07-01","2019-06-30"), ["MJ","YOLO","CNBS"], ["TLRY","CGC","ACB","CRON","SNDL","IIPR","GRWG","SMG"]),
 "ev_clean_2020":   (("2019-07-01","2021-06-30"), ["KARS","DRIV","PBW","ICLN"], ["TSLA","NIO","PLUG","ENPH","FSLR","SEDG","BLNK","QS"]),
 "spac_2020":       (("2019-07-01","2021-06-30"), ["SPAK","SPCX","SPXZ"], ["SPCE","DKNG","NKLA","OPEN","CLOV","QS","SOFI","LCID"]),
 "metaverse_2021":  (("2020-07-01","2022-06-30"), ["METV"], ["RBLX","U","META","NVDA","MTTR","SNAP","TTWO"]),
 "genai_2023":      (("2022-07-01","2024-06-30"), ["CHAT","SMH"], ["NVDA","MSFT","AMD","AVGO","SMCI","PLTR","META","AI"]),
 "ai_power_2024":   (("2023-07-01","2025-06-30"), ["VOLT","ZAP"], ["VST","CEG","NRG","TLN","GEV","ETN","VRT","PWR"]),
 "nuclear_2024":    (("2023-07-01","2025-06-30"), ["NUKZ","NLR","URA"], ["CCJ","OKLO","SMR","NNE","LEU","BWXT","UEC","CEG"]),
 "quantum_2024":    (("2024-01-01","2025-12-31"), ["QTUM"], ["IONQ","RGTI","QBTS","QUBT","IBM","GOOGL","HON","ARQQ"]),
 "glp1_2023":       (("2022-07-01","2024-06-30"), ["HRTS","OZEM","THNR"], ["LLY","NVO","VKTX","AMGN","HIMS","ALT","GPCR","TERN"]),
 "humanoid_2025":   (("2024-07-01","2026-06-30"), ["KOID","HUMN","BOTT"], ["TSLA","NVDA","TER","SYM","RR","SERV"]),
 "stablecoin_2025": (("2024-07-01","2026-03-31"), ["STBQ"], ["CRCL","COIN","HOOD","PYPL","XYZ","GLXY"]),
 "space_2025":      (("2024-07-01","2026-06-30"), ["UFO","ARKX","ROKT"], ["RKLB","ASTS","LUNR","PL","RDW","FLY","SPCE","KRMN"]),
 "drones_2025":     (("2024-07-01","2026-06-30"), ["DRNZ","SHLD"], ["AVAV","KTOS","RCAT","UMAC","ONDS","DPRO","AIRO"]),
 "memory_2025":     (("2025-01-01","2026-09-30"), ["DRAM"], ["MU","WDC","STX","SNDK","LRCX","AMAT"]),
}

async def weekly_adj(sym, start="2015-01-01", end="2026-10-09"):
    rows = await get_eod_historical_data(f"{sym}.US", from_date=start, to_date=end)
    if not rows: return None
    df = pd.DataFrame(rows)
    s = pd.Series(df["adjusted_close"].astype(float).values, index=pd.to_datetime(df["date"])).sort_index()
    return s.resample("W-FRI").last().dropna()

def basket(series):  # equal-weight weekly-rebalanced index; constituents enter when they start trading
    rets = pd.concat({k: v.pct_change() for k, v in series.items() if v is not None}, axis=1)
    rets = rets.where(rets.abs() < 1.0)              # drop obviously bad prints (>100% weekly move)
    return (1 + rets.mean(axis=1, skipna=True).fillna(0)).cumprod()

def breakout(p, start, end, need=40):
    hi = p.shift(1).rolling(52, min_periods=need).max(); r13 = p / p.shift(13) - 1
    ok = (p > hi) & (r13 > 0.20) & (p.index >= start) & (p.index <= end)
    return ok[ok].index[0] if ok.any() else None

def weeks(a, b):
    return None if a is None or b is None or pd.isna(a) or pd.isna(b) else round((pd.Timestamp(b) - pd.Timestamp(a)).days / 7)

async def main(pilot_dir, out):
    fires = pd.read_csv(f"{pilot_dir}/episode_results.csv")      # from the pilot: episode, src, z2, z3, z3persist, ...
    recs = []
    for ep, ((s0, s1), etfs, ticks) in EPISODES.items():
        px = {t: await weekly_adj(t) for t in etfs + ticks}
        refs = {}
        for t in etfs:
            if px[t] is not None:
                refs[f"etf:{t}"] = (breakout(px[t], s0, s1), px[t][s0:pd.Timestamp(s1) + pd.Timedelta(weeks=26)].idxmax())
        b = basket({t: px[t] for t in ticks})
        refs["basket"] = (breakout(b, s0, s1), b[s0:pd.Timestamp(s1) + pd.Timedelta(weeks=26)].idxmax())
        for _, f in fires[fires.episode == ep].iterrows():
            for ref, (bo, pk) in refs.items():
                recs.append(dict(episode=ep, src=f.src, ref=ref, breakout=bo, peak=pk, z3=f.z3, z3persist=f.z3persist,
                                 lead_bo_z3=weeks(f.z3, bo), lead_bo_z3p=weeks(f.z3persist, bo), lead_peak_z3=weeks(f.z3, pk)))
    df = pd.DataFrame(recs); df.to_csv(out, index=False)
    s = df[df.ref == "basket"].dropna(subset=["lead_bo_z3"])
    print(s.groupby("src")["lead_bo_z3"].agg(n="count", led=lambda v: int((v > 0).sum()), median="median"))

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--pilot-dir", required=True); ap.add_argument("--out", default="theme_leads.csv")
    a = ap.parse_args(); asyncio.run(main(a.pilot_dir, a.out))
