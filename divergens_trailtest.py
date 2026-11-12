# Test 2026-11-12: trailing stop efter 2R (EMA10, Parabolic SAR på stängning, SMA5) och 200-dagarsfilter, även mot OMXSPI med samma vikter (kör: python divergens_trailtest.py <datamapp>)
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
I = idx.values
idx_dd = idx / idx.rolling(250, min_periods=200).max() - 1


def rsi(c, n=9):
    d = np.diff(c, prepend=np.nan)
    up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    ru = pd.Series(up).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().values
    rd = pd.Series(dn).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().values
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100 - 100 / (1 + ru / rd)



def psar_close(c, af0=0.02, step=0.02, amax=0.2):
    """Parabolic SAR på stängningskurser (högsta = lägsta = stängning, eftersom dagliga högsta/lägsta saknas bakåt)."""
    n = len(c); sar = np.full(n, np.nan); up = True; af = af0
    k0 = next((k for k in range(n) if np.isfinite(c[k])), None)
    if k0 is None or k0 + 1 >= n: return sar
    ep = c[k0]; s = c[k0]
    for k in range(k0 + 1, n):
        if not np.isfinite(c[k]): sar[k] = sar[k-1]; continue
        s = s + af * (ep - s)
        if up:
            if c[k] < s: up, s, ep, af = False, ep, c[k], af0
            elif c[k] > ep: ep, af = c[k], min(af + step, amax)
        else:
            if c[k] > s: up, s, ep, af = True, ep, c[k], af0
            elif c[k] < ep: ep, af = c[k], min(af + step, amax)
        sar[k] = s if up else np.nan    # bara relevant i upptrend (stopp under kursen)
    return sar

def simx(c, ema10, sar, i, e, risk, mal3=True, maxd=10, trail=None):
    """halva vid 2R och stoppen till köpkursen; resten vid 3R (om mal3), stopp, eller efter 2R när kursen stänger under
    EMA10/SAR (trail) eller efter maxd dagar. Ger (R, R för OMXSPI med samma vikter och dagar, hålldagar)."""
    Rk = max(risk, 0.015) * e; s0 = e - Rk; half = False; res = 0.0; ires = 0.0; I0 = I[i]
    iR = lambda j, w: w * (I[j] / I0 - 1) * e / Rk
    for d in range(1, maxd + 1):
        j = i + d
        if j >= len(c): return None
        x = c[j]
        if not np.isfinite(x): continue
        st = e if half else s0; w = .5 if half else 1.
        if x <= st: return (res + w * (x - e) / Rk, ires + iR(j, w), d)
        if not half and x >= e + 2 * Rk:
            half = True; res += 1.0; ires += iR(j, .5); w = .5
        if mal3 and half and x >= e + 3 * Rk: return (res + 1.5, ires + iR(j, .5), d)
        if half and trail == "ema" and x < ema10[j]: return (res + .5 * (x - e) / Rk, ires + iR(j, .5), d)
        if half and trail == "sar" and not np.isfinite(sar[j]): return (res + .5 * (x - e) / Rk, ires + iR(j, .5), d)
        if half and trail == "sma5" and x < np.nanmean(c[j-4:j+1]): return (res + .5 * (x - e) / Rk, ires + iR(j, .5), d)
    j = min(i + maxd, len(c) - 1); w = .5 if half else 1.
    return (res + w * (c[j] - e) / Rk, ires + iR(j, w), maxd)

VAR = {
 "bas": dict(),
 "ema10_60d": dict(mal3=False, maxd=60, trail="ema"),
 "ema10_10d": dict(mal3=False, maxd=10, trail="ema"),
 "sar_60d": dict(mal3=False, maxd=60, trail="sar"),
 "sma5_60d": dict(mal3=False, maxd=60, trail="sma5"),
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
    ema10 = s.ewm(span=10, adjust=False).mean().values; sar = psar_close(c); ma200 = s.rolling(200, min_periods=180).mean().values
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
        if land != "SE": continue
        out = {}
        for namn, kw in VAR.items():
            res = simx(c, ema10, sar, i, sg["e"], sg["risk"], **kw)
            if res is None: break
            out[namn] = res
        if len(out) < len(VAR): continue
        m200 = ma200[i]; m200_20 = ma200[i-20] if i >= 20 else np.nan
        rows.append(dict(t=t, d=dates[i], risk=sg["risk"], nara200=c[i]/m200-1 if np.isfinite(m200) else np.nan,
                         lut200=m200/m200_20-1 if np.isfinite(m200_20) else np.nan,
                         **{k: v[0] for k, v in out.items()}, **{k+"_i": v[1] for k, v in out.items()}, **{k+"_d": v[2] for k, v in out.items()}))

df = pd.DataFrame(rows)
df["per"] = np.where(df.d < "2023-01-01", "2016-22", "2023-26")

import math
def tm(x, d):
    m = x.groupby(d.dt.to_period("M")).mean(); return m.mean() / (m.std(ddof=1) / math.sqrt(len(m)))
g = df; per = g.d < "2023-01-01"
print(f"SE: {len(g)} affärer. R = rått, mot index = R minus OMXSPI med samma vikter och dagar")
for v in VAR:
    mi = g[v] - g[v+"_i"]; dd = g[v] - g.bas; ddm = mi - (g.bas - g.bas_i)
    extra = "" if v == "bas" else f" | mot bas {dd.mean():+.3f}R (t {tm(dd,g.d):+.2f}), mot index-justerad bas {ddm.mean():+.3f}R (t {tm(ddm,g.d):+.2f}); 2016-22 {dd[per].mean():+.3f}, 2023-26 {dd[~per].mean():+.3f}"
    print(f"  {v:10s} {g[v].mean():+.3f}R  mot index {mi.mean():+.3f}R (t {tm(mi,g.d):+.2f})  dagar {g[v+'_d'].mean():.1f}{extra}")
print("\nTrendfilter (på nuvarande regler):")
mi = g.bas - g.bas_i
for namn, sel in (("Över MA200", g.nara200 >= 0), ("Högst 5 % under MA200", g.nara200 >= -0.05), ("Högst 10 % under MA200", g.nara200 >= -0.10),
                  ("MA200 faller < 2 % på 20 d", g.lut200 >= -0.02), ("MA200 faller < 4 % på 20 d", g.lut200 >= -0.04), ("MA200 stiger", g.lut200 >= 0)):
    a, b_ = g[sel], g[~sel & g.nara200.notna()]
    d_ = a.bas.mean() - b_.bas.mean(); s_ = math.sqrt(a.bas.var()/len(a) + b_.bas.var()/len(b_))
    print(f"  {namn:28s} n={len(a):4d} ({100*len(a)/len(g):.0f} %)  {a.bas.mean():+.3f}R mot index {mi[sel].mean():+.3f}R | bortfiltrerade n={len(b_)} {b_.bas.mean():+.3f}R mot index {mi[~sel & g.nara200.notna()].mean():+.3f}R | skillnad t={d_/s_:+.2f}; 2016-22 {a[a.d<'2023'].bas.mean():+.3f} mot {b_[b_.d<'2023'].bas.mean():+.3f}, 2023-26 {a[a.d>='2023'].bas.mean():+.3f} mot {b_[b_.d>='2023'].bas.mean():+.3f}")
