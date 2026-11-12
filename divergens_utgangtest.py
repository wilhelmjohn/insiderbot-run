# Test 2026-11-12: negativ divergens som säljsignal och längre hålltid utan 3R, även mot index (kör: python divergens_utgangtest.py <datamapp>)
"""Test: utgångar vid avtagande trend i Divergensscreen jämfört med nuvarande regel.

  python divergens_trendtest.py <datamapp>

Signaler som i divergens_bygg.py (s1_signals), köp på signaldagens stängning, max en signal per aktie och 15 dagar.
Sverige: 80 mest omsatta vid signaldagen + Lyko, marknadsfilter som på sidan. Norden: alla norska och danska aktier,
utan marknadsfilter (robusthet). Bara stängningskurser finns, så stopp och trendutgångar säljs på stängningen
och målen fylls till målkursen när stängningen når den (konservativt, missar mål som nås under dagen).

Varianter (resultat i R per affär, R = köpkurs - stopp):
  bas      : halva vid 2R, stopp till köpkurs, resten vid 3R, stopp eller dag 10 (nuvarande regel)
  sma5     : som bas, men efter 2R säljs resten vid första stängning under 5-dagarssnittet (3R och dag 10 kvar)
  sma5x2   : som sma5 men två stängningar i rad under 5-dagarssnittet
  rsi50    : som bas, men efter 2R säljs resten när RSI(9) går under 50
  lagre3   : som bas, men efter 2R säljs resten vid stängning under lägsta stängningen de 3 föregående dagarna
  sma5_20  : sma5 utan 3R-mål och med max 20 dagar (låt vinnarna löpa, sälj när trenden mattas)
  sma5_no3 : sma5 utan 3R-mål, max 10 dagar
  bas20    : bas men max 20 dagar
  hel_sma5 : hela positionen säljs vid första stängning under 5-dagarssnittet efter dag 2 (även före 2R), mål som bas
"""
import sys, numpy as np, pandas as pd

DATA = sys.argv[1].rstrip("/") + "/"
k = pd.read_csv(DATA + "kurser.csv", parse_dates=["Datum"]); k = k[k.Ticker.str.endswith(".ST")]
nd = pd.read_csv(DATA + "divergens_nordic.csv", parse_dates=["Datum"])
nd = nd[nd.Ticker.str.endswith((".OL", ".CO")) & ~nd.Ticker.isin(["OBX.OL", "OSEBX.OL"])]
k = pd.concat([k, nd], ignore_index=True)
px = k.pivot_table(index="Datum", columns="Ticker", values="Stang").sort_index()
vo = k.pivot_table(index="Datum", columns="Ticker", values="Volym").sort_index().reindex_like(px)
px = px[(px.index >= "2015-06-01") & (px.notna().sum(axis=1) > 50)]; vo = vo.reindex(px.index); px = px.ffill(limit=3)
dates = px.index
se = [t for t in px.columns if t.endswith(".ST")]
turn = (px[se] * vo[se]).rolling(60, min_periods=40).median()
rank = turn.rank(axis=1, ascending=False)
idx = pd.read_csv(DATA + "marknad.csv", parse_dates=["Datum"]).set_index("Datum").Index.reindex(dates).ffill()
idx_dd = idx / idx.rolling(250, min_periods=200).max() - 1


def rsi(c, n=9):
    d = np.diff(c, prepend=np.nan)
    up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    ru = pd.Series(up).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().values
    rd = pd.Series(dn).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().values
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100 - 100 / (1 + ru / rd)



def negdiv_dag(c, r, K, start, slut, rmin=65, kmin=None):
    """Första dag j i (start, slut] då en negativ divergens är känd: topp q (högre än två dagar på var sida, q+2 <= j, q > start-1)
    med högre kurs men lägre RSI(9) än en topp q0 5–40 dagar tidigare där RSI var över rmin."""
    for j in range(start + 1, min(slut, len(c) - 1) + 1):
        q = j - 2
        if q - 2 < 0 or q < start: continue
        w = c[q-2:q+3]
        if not np.isfinite(w).all() or not (c[q] > c[q-1] and c[q] > c[q-2] and c[q] > c[q+1] and c[q] > c[q+2]): continue
        if kmin is not None and not (np.nanmax(K[q-3:q+1]) >= kmin): continue
        for q0 in range(q - 5, max(q - 41, 1), -1):
            w0 = c[q0-2:q0+3]
            if len(w0) < 5 or not np.isfinite(w0).all(): continue
            if c[q0] > c[q0-1] and c[q0] > c[q0-2] and c[q0] > c[q0+1] and c[q0] > c[q0+2] and r[q0] > rmin and c[q] > c[q0] and r[q] < r[q0]:
                return j
    return None

def simx(c, r, K, i, e, risk, mal3=True, maxd=10, neg=False, neg_fore2r=True, kmin=None):
    """halva vid 2R och stoppen till köpkursen; resten vid 3R (om mal3), stopp, negativ divergens (om neg) eller efter maxd dagar. Utgångar på stängning."""
    Rk = max(risk, 0.015) * e; s0 = e - Rk; half = False; res = 0.0
    nd = negdiv_dag(c, r, K, i, i + maxd, kmin=kmin) if neg else None
    for d in range(1, maxd + 1):
        j = i + d
        if j >= len(c): return None
        x = c[j]
        if not np.isfinite(x): continue
        st = e if half else s0; w = .5 if half else 1.
        if x <= st: return (res + w * (x - e) / Rk, 0, d)
        if not half and x >= e + 2 * Rk: half = True; res += 1.0; w = .5
        if mal3 and half and x >= e + 3 * Rk: return (res + 1.5, 0, d)
        if nd is not None and j == nd and (half or neg_fore2r): return (res + w * (x - e) / Rk, 1, d)
    j = min(i + maxd, len(c) - 1)
    return (res + (.5 if half else 1.) * (c[j] - e) / Rk, 0, maxd)

VAR = {
 "bas": dict(),
 "neg10": dict(neg=True),
 "neg10_efter2R": dict(neg=True, neg_fore2r=False),
 "neg10_K75": dict(neg=True, kmin=75),
 "neg20_utan3R": dict(neg=True, mal3=False, maxd=20),
 "neg30_utan3R": dict(neg=True, mal3=False, maxd=30),
 "neg30_utan3R_efter2R": dict(neg=True, mal3=False, maxd=30, neg_fore2r=False),
 "tid20_utan3R": dict(mal3=False, maxd=20),
 "tid30_utan3R": dict(mal3=False, maxd=30),
}

def pivots(c):
    return [i for i in range(2, len(c) - 2) if np.isfinite(c[i - 2:i + 3]).all()
            and c[i] < c[i - 1] and c[i] < c[i - 2] and c[i] < c[i + 1] and c[i] < c[i + 2]]


def signals(c, r, K, hi250):
    out, piv = [], pivots(c)
    for b in piv:
        i = b + 2
        for p in piv:
            if 5 <= b - p <= 40 and c[b] < c[p] and r[b] > r[p] and r[p] < 35 and c[i] <= 0.85 * hi250[i] \
                    and np.nanmin(K[i - 3:i + 1]) < 25:
                e = c[i]; risk = (e - c[b] * 0.99) / e
                if np.isfinite(risk) and risk <= 0.08:
                    out.append(dict(i=i, b=b, p=p, e=e, risk=max(risk, 0.015)))
                break
    return out


# bredd: andel svenska aktier (top 80) under 50-dagarssnitt
ma50 = px[se].rolling(50).mean()
under = (px[se] < ma50) & (rank <= 80)
bredd = under.sum(axis=1) / (rank <= 80).sum(axis=1).replace(0, np.nan)


def sim(c, sma5, r, i, e, R, var):
    """Returnerar resultat i R. R här = riskandel; kursnivåer i kronor."""
    Rk = e * R; stop0 = e - Rk; m2, m3 = e + 2 * Rk, e + 3 * Rk
    maxd = 20 if var in ("sma5_20", "bas20") else 10
    no3 = var in ("sma5_20", "sma5_no3")
    half, res, under_n = False, 0.0, 0
    for d in range(1, maxd + 1):
        j = i + d
        if j >= len(c): return None
        x = c[j]
        if not np.isfinite(x): continue
        stop = e if half else stop0
        w = 0.5 if half else 1.0
        if x <= stop: return res + w * (x - e) / Rk
        if var == "hel_sma5" and not half and d >= 2 and x < sma5[j]: return res + (x - e) / Rk
        if not half and x >= m2:
            half = True; res += 0.5 * 2.0
            if no3: continue
        if half:
            if not no3 and x >= m3: return res + 0.5 * 3.0
            trig = False
            if var in ("sma5", "sma5_20", "hel_sma5", "sma5_no3"): trig = x < sma5[j]
            elif var == "sma5x2":
                under_n = under_n + 1 if x < sma5[j] else 0; trig = under_n >= 2
            elif var == "rsi50": trig = r[j] < 50
            elif var == "lagre3": trig = x < np.nanmin(c[j - 3:j])
            if trig: return res + 0.5 * (x - e) / Rk
    x = c[min(i + maxd, len(c) - 1)]
    return res + (0.5 if half else 1.0) * (x - e) / Rk


VARS = ["bas", "sma5", "sma5x2", "rsi50", "lagre3", "sma5_20", "hel_sma5", "sma5_no3", "bas20"]
rows = []
for t in px.columns:
    c = px[t].values.astype(float); s = pd.Series(c)
    if np.isfinite(c).sum() < 300: continue
    lo, hi = s.rolling(14).min().values, s.rolling(14).max().values
    with np.errstate(divide="ignore", invalid="ignore"):
        K = 100 * (c - lo) / (hi - lo)
    hi250 = s.rolling(250, min_periods=200).max().values
    r = rsi(c); sma5 = s.rolling(5).mean().values
    senast = -999
    for sg in signals(c, r, K, hi250):
        i = sg["i"]
        if i - senast < 15 or dates[i] < pd.Timestamp("2016-01-01"): continue
        land = "SE" if t.endswith(".ST") else "NO/DK"
        if land == "SE":
            if not (rank[t].iloc[i] <= 80 or t == "LYKO-A.ST"): continue
            filt = (idx_dd.iloc[i] <= -0.10) or (bredd.iloc[i] > 0.70)
            if not filt:
                rel = (c[i] / c[sg["p"]] - 1) - (idx.iloc[i] / idx.iloc[sg["p"]] - 1)
                if rel < -0.06: continue
        senast = i
        out = {}
        for namn, kw in VAR.items():
            res = simx(c, r, K, i, sg["e"], sg["risk"], **kw)
            if res is None: break
            out[namn] = res
        if len(out) < len(VAR): continue
        rows.append(dict(t=t, d=dates[i], i=i, land=land, risk=sg["risk"], **{k: v[0] for k, v in out.items()}, **{k + "_neg": v[1] for k, v in out.items()}, **{k + "_dag": v[2] for k, v in out.items()}))

df = pd.DataFrame(rows)
df["per"] = np.where(df.d < "2023-01-01", "2016-22", "2023-26")


import math
g=df[df.land=="SE"].copy(); I=idx.values
def mkt(row,v):
    j=min(int(row.i)+int(row[v+"_dag"]),len(I)-1); return (I[j]/I[int(row.i)]-1)/max(row.risk,0.015)
for v in ("bas","tid20_utan3R","tid30_utan3R","neg30_utan3R"):
    g[v+"_m"]=g[v]-g.apply(lambda r: mkt(r,v),axis=1)
print("SE, avdrag för indexets rörelse under hålltiden (i R):")
for v in ("bas","tid20_utan3R","tid30_utan3R","neg30_utan3R"):
    dd=g[v+"_m"]-g["bas_m"]; m=dd.groupby(g.d.dt.to_period("M")).mean(); t=m.mean()/(m.std(ddof=1)/math.sqrt(len(m)))
    print(f"  {v:14s} {g[v+'_m'].mean():+.3f}R mot index | mot bas {dd.mean():+.3f}R (t {t:+.2f}) | dagar {g[v+'_dag'].mean():.1f} | per dag {g[v+'_m'].mean()/g[v+'_dag'].mean():+.4f}R")
