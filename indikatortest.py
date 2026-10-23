"""Test: ger MACD, stochastic eller RSI något extra till Divergensscreen (köp) och Rasbevakning (blankning)?

  python indikatortest.py <datamapp>

Divergens: signaler och filter som i divergens_trendtest.py, nuvarande utgång (halva 2R, rest 3R/stopp/dag 10), R per affär.
Ras: regler som i ras_bygg.py (utan insynsfiltret), blankning dag 5 på stängning, samma utgång spegelvänd, kostnad 0,35 %.
För varje indikatorvillkor på signaldagen: medel-R med/utan villkoret och t-värde, per period 2016-22 och 2023-26.
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
idx = pd.read_csv(DATA + "marknad.csv", parse_dates=["Datum"]).drop_duplicates("Datum").set_index("Datum").Index.reindex(dates).ffill()
idx_dd = idx / idx.rolling(250, min_periods=200).max() - 1
I = idx.values
ma50 = px[se].rolling(50).mean(); under = (px[se] < ma50) & (rank <= 80)
bredd = under.sum(axis=1) / (rank <= 80).sum(axis=1).replace(0, np.nan)

def rsi(c, n=9):
    d = np.diff(c, prepend=np.nan); up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    ru = pd.Series(up).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().values
    rd = pd.Series(dn).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().values
    with np.errstate(divide="ignore", invalid="ignore"): return 100 - 100 / (1 + ru / rd)
def pivots(c):
    return [i for i in range(2, len(c) - 2) if np.isfinite(c[i - 2:i + 3]).all() and c[i] < c[i - 1] and c[i] < c[i - 2] and c[i] < c[i + 1] and c[i] < c[i + 2]]
def signals(c, r, K, hi250):
    out, piv = [], pivots(c)
    for b in piv:
        i = b + 2
        for p in piv:
            if 5 <= b - p <= 40 and c[b] < c[p] and r[b] > r[p] and r[p] < 35 and c[i] <= 0.85 * hi250[i] and np.nanmin(K[i - 3:i + 1]) < 25:
                e = c[i]; risk = (e - c[b] * 0.99) / e
                if np.isfinite(risk) and risk <= 0.08: out.append(dict(i=i, b=b, p=p, e=e, risk=max(risk, 0.015)))
                break
    return out
def sim(c, i, e, R, sida=1):
    """sida 1 = köp, -1 = blankning. Resultat i R."""
    Rk = e * R; half, res = False, 0.0
    for d in range(1, 11):
        j = i + d
        if j >= len(c): return None
        x = c[j]
        if not np.isfinite(x): continue
        g = sida * (x - e) / Rk; w = 0.5 if half else 1.0
        if g <= (0 if half else -1): return res + w * g
        if not half and g >= 2: half = True; res += 1.0
        if half and g >= 3: return res + 1.5
    x = c[min(i + 10, len(c) - 1)]
    return res + (0.5 if half else 1.0) * sida * (x - e) / Rk
def ind(c):
    s = pd.Series(c)
    lo, hi = s.rolling(14).min().values, s.rolling(14).max().values
    with np.errstate(divide="ignore", invalid="ignore"): K = 100 * (c - lo) / (hi - lo)
    D = pd.Series(K).rolling(3).mean().values
    m = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean(); sg = m.ewm(span=9, adjust=False).mean()
    return K, D, m.values, (m - sg).values, rsi(c)

div, ras = [], []
for t in px.columns:
    c = px[t].values.astype(float)
    if np.isfinite(c).sum() < 300: continue
    K, D, mac, hist, r = ind(c); hi250 = pd.Series(c).rolling(250, min_periods=200).max().values
    # ---- divergens
    senast = -999
    for sg in signals(c, r, K, hi250):
        i = sg["i"]
        if i - senast < 15 or dates[i] < pd.Timestamp("2016-01-01"): continue
        land = "SE" if t.endswith(".ST") else "NO/DK"
        if land == "SE":
            if not (rank[t].iloc[i] <= 80 or t == "LYKO-A.ST"): continue
            if not ((idx_dd.iloc[i] <= -0.10) or (bredd.iloc[i] > 0.70)):
                if (c[i] / c[sg["p"]] - 1) - (I[i] / I[sg["p"]] - 1) < -0.06: continue
        senast = i
        res = sim(c, i, sg["e"], sg["risk"])
        if res is None: continue
        b, p = sg["b"], sg["p"]
        div.append(dict(t=t, d=dates[i], land=land, R=res,
            macd_hist_upp=hist[i] > hist[i - 1], macd_hist_pos=hist[i] > 0, macd_div=mac[b] > mac[p],
            macd_kors3=bool(np.any((hist[i - 2:i + 1] > 0) & (hist[i - 3:i] <= 0))), macd_under0=mac[i] < 0,
            stoch_kd=K[i] > D[i], stoch_K_nu_lt30=K[i] < 30, stoch_K_upp=K[i] > K[i - 1], rsi_gt40=r[i] > 40))
    # ---- ras (Sverige, omsättning >= 2 MSEK)
    if not t.endswith(".ST"): continue
    oms = (px[t] * vo[t]).rolling(60, min_periods=40).median().values
    upptagen = -1
    for q in range(260, len(c) - 16):
        if q <= upptagen or not (np.isfinite(c[q]) and np.isfinite(c[q - 1]) and np.isfinite(oms[q]) and oms[q] >= 2e6): continue
        dr = c[q] / c[q - 1] - 1; ir = I[q] / I[q - 1] - 1
        if not (dr <= -0.08 and dr - ir <= -0.05): continue
        i = q + 5
        if not (c[i] < c[q] and (c[i] / c[q] - 1) < (I[i] / I[q] - 1)): continue
        stop0 = np.nanmax(c[q:i + 1]) * 1.01; risk = (stop0 - c[i]) / c[i]
        if not np.isfinite(risk) or risk > 0.08: continue
        risk = max(risk, 0.015)
        res = sim(c, i, c[i], risk, -1)
        if res is None: continue
        res -= 0.0035 / risk; upptagen = i + 10
        ras.append(dict(t=t, d=dates[i], R=res, mk_topp=idx_dd.iloc[i] > -0.10,
            rsi_lt30=r[i] < 30, rsi_lt40=r[i] < 40, rsi_gt50=r[i] > 50, stoch_K_lt20=K[i] < 20, stoch_K_gt50=K[i] > 50, stoch_kd_ned=K[i] < D[i],
            macd_hist_ned=hist[i] < hist[i - 1], macd_hist_neg=hist[i] < 0, macd_under0=mac[i] < 0))

pd.set_option("display.width", 250)
def rapport(df, namn):
    df = df.copy(); df["per"] = np.where(df.d < "2023-01-01", "2016-22", "2023-26")
    print(f"\n===== {namn}: {len(df)} affärer, medel {df.R.mean():+.3f}R (2016-22 {df[df.per=='2016-22'].R.mean():+.3f}, 2023-26 {df[df.per=='2023-26'].R.mean():+.3f})")
    print(f"{'villkor':18s} {'andel':>6s} {'med':>7s} {'utan':>7s} {'t':>5s} | {'med 16-22':>9s} {'utan':>6s} | {'med 23-26':>9s} {'utan':>6s}")
    for col in [x for x in df.columns if df[x].dtype == bool]:
        a, b = df[df[col]], df[~df[col]]
        if len(a) < 20 or len(b) < 20: continue
        t = (a.R.mean() - b.R.mean()) / np.sqrt(a.R.var() / len(a) + b.R.var() / len(b))
        o = [f"{col:18s} {len(a)/len(df):6.0%} {a.R.mean():+7.3f} {b.R.mean():+7.3f} {t:5.1f}"]
        for per in ("2016-22", "2023-26"):
            x = df[df.per == per]; o.append(f"{x[x[col]].R.mean():+9.3f} {x[~x[col]].R.mean():+6.3f}")
        print(" | ".join(o))
d = pd.DataFrame(div); r_ = pd.DataFrame(ras)
rapport(d[d.land == "SE"], "Divergens Sverige"); rapport(d[d.land != "SE"], "Divergens Norge/Danmark"); rapport(d, "Divergens alla")
rapport(r_, "Ras (blankning) alla"); rapport(r_[r_.mk_topp].drop(columns="mk_topp"), "Ras, börsen nära topp")
