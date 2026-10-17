"""Multi-source agreement rule: first week where >=2 of {wiki, hn, edgar} had z>3 (with level floors) in the trailing 4 weeks.
Variant B: (wiki or hn z>3 in trailing 4w) AND edgar z>2 in trailing 8w."""
import sys, os
sys.argv = [sys.argv[0], sys.argv[1]]
code = open(os.path.join(os.path.dirname(__file__), "analyze.py")).read().split("rows, crow, rates = [], [], []")[0]
exec(code)
import pandas as pd
def fires(cfg, thr):
    out = {}
    for src, x in series_for(cfg).items():
        if x is None or src == "edgar_new": continue
        pr = SRC_PARAMS[src]; z, _ = robust_z(x, pr["floor_abs"], pr["floor_rel"])
        out[src] = ((z > thr) & (x >= pr["min_level"])).astype(int)
    return pd.DataFrame(out).fillna(0)
def first(mask, s0, s1):
    m = mask[(mask.index >= s0) & (mask.index <= s1)]
    return m[m].index[0].date() if m.any() else None
res = []
for name, cfg in list(EPISODES.items()) + list(CONTROLS.items()):
    s0, s1 = (META[name]["win"] if name in META else CTRL_WIN[name])
    f3 = fires(cfg, 3); f2 = fires(cfg, 2)
    agreeA = (f3.rolling(4, min_periods=1).max().sum(axis=1) >= 2)
    att = f3[[c for c in ["wiki", "hn"] if c in f3]].rolling(4, min_periods=1).max().max(axis=1) > 0
    agreeB = att & (f2["edgar"].rolling(8, min_periods=1).max() > 0) if "edgar" in f2 else att & False
    res.append(dict(name=name, kind="episode" if name in META else "control", ruleA_2of3_z3=first(agreeA, s0, s1), ruleB_attn_and_edgar=first(agreeB, s0, s1),
                    bo=None if name not in META else None))
df = pd.DataFrame(res); print(df.to_string())
for k in ["episode", "control"]:
    d = df[df.kind == k]; print(k, "ruleA fired", d.ruleA_2of3_z3.notna().sum(), "/", len(d), "| ruleB fired", d.ruleB_attn_and_edgar.notna().sum(), "/", len(d))
