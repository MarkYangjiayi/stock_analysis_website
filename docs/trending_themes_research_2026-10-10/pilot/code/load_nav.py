"""Turn issuer NAV-history spreadsheets (VanEck 'fundhistoprices' xlsx, SSGA 'navhist' xlsx) into daily NAV series.
NAV per share is price-only (distributions not reinvested). One-day moves beyond -40%/+70% are treated as unadjusted
share splits and back-adjusted (flagged in output)."""
import os, sys, json, datetime as dt
sys.path.insert(0, os.path.dirname(__file__)); from xlsx_peek import read
def parse_date(s):
    for f in ("%m/%d/%Y", "%d-%b-%Y"):
        try: return dt.datetime.strptime(s.strip(), f).date()
        except Exception: pass
    try:  # excel serial
        return dt.date(1899, 12, 30) + dt.timedelta(days=int(float(s)))
    except Exception: return None
def load(path):
    rows = read(path); hdr_i = next(i for i, r in enumerate(rows) if r and r[0] == "Date")
    nav_col = rows[hdr_i].index("NAV"); out = []
    for r in rows[hdr_i + 1:]:
        if not r or r[0] is None or len(r) <= nav_col or r[nav_col] in (None, ""): continue
        d = parse_date(str(r[0]))
        try: v = float(str(r[nav_col]).replace(",", "").replace("$", ""))
        except ValueError: continue
        if d and v > 0: out.append((d, v))
    out.sort(); splits = []
    adj = [v for _, v in out]
    for i in range(len(out) - 1, 0, -1):
        ratio = adj[i] / adj[i - 1]
        if ratio < 0.6 or ratio > 1.7:
            f = ratio; splits.append((out[i][0].isoformat(), round(1 / ratio, 3)))
            for j in range(i): adj[j] *= f
    return [(d.isoformat(), a) for (d, _), a in zip(out, adj)], splits
if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]; os.makedirs(dst, exist_ok=True)
    for fn in sorted(os.listdir(src)):
        if not fn.endswith(".xlsx"): continue
        try:
            series, splits = load(os.path.join(src, fn))
        except StopIteration:
            print(fn, "not a NAV table"); continue
        tk = fn.split("_")[-1].replace(".xlsx", "").split("-")[-1].upper()
        json.dump({"ticker": tk, "source_file": fn, "splits_adjusted": splits, "nav": series}, open(os.path.join(dst, tk + ".json"), "w"))
        print(tk, len(series), series[0][0], series[-1][0], "splits:", splits)
