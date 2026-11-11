# Test 2026-11-11: insiderköp nära bottnen som extra villkor i Divergensscreen (kör: python divergens_insidertest.py <datamapp>)
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
        res = sim(c, sma5, r, i, sg["e"], sg["risk"], "bas")
        if res is None: continue
        rows.append(dict(t=t, d=dates[i], dp=dates[sg["p"]], db=dates[sg["b"]], dbm5=dates[max(sg["b"]-5,0)], land=land, R=res, risk=sg["risk"]))

df = pd.DataFrame(rows)
df["per"] = np.where(df.d < "2023-01-01", "2016-22", "2023-26")

import math
df=df[df.land=="SE"].copy()
tm=pd.read_csv(DATA+"ticker_map.csv"); bid2t=tm.set_index("Bolag_id").Ticker
kop=pd.read_csv(DATA+"insiderkop.csv",parse_dates=["Publiceringsdatum"])
kop=kop[(kop["Karaktär"]=="Förvärv")&(kop["Instrumenttyp"]=="Aktie")&(~kop["Intern_overforing"].fillna(False).astype(bool))&(kop["Valuta"]=="SEK")].copy()
kop["Ticker"]=kop.Bolag_id.map(bid2t); kop=kop.dropna(subset=["Ticker"])
kop["kr"]=(kop.Volym*kop.Pris).clip(upper=5e8)
han=pd.read_csv(DATA+"insiderhandel.csv",parse_dates=["Publiceringsdatum"])
isin2t=tm.dropna(subset=["ISIN"]).drop_duplicates("ISIN").set_index("ISIN").Ticker
sa=han[(han["Karaktär"]=="Avyttring")&(han["Instrumenttyp"]=="Aktie")&(han["Valuta"]=="SEK")].copy(); sa["Ticker"]=sa.ISIN.map(isin2t); sa=sa.dropna(subset=["Ticker"]); sa["kr"]=(sa.Volym*sa.Pris).clip(upper=5e8)
K=dict(tuple(kop.groupby("Ticker"))); S=dict(tuple(sa.groupby("Ticker")))
def feat(row):
    k=K.get(row.t); o={}
    def n(lo,hi,minkr=0):
        if k is None: return 0
        kk=k[(k.Publiceringsdatum>=lo)&(k.Publiceringsdatum<=hi)]
        return kk["Person i ledande ställning"].nunique() if kk.kr.sum()>=minkr else 0
    for dg in (3,7,14):
        o[f"k{dg}"]=n(row.d-pd.Timedelta(days=dg),row.d)
    o["kb"]=n(row.dbm5,row.d)          # från 5 handelsdagar före andra bottnen till signalen
    o["kb2"]=n(row.db,row.d)           # från andra bottnen till signalen
    o["kb_mkr"]=n(row.dbm5,row.d,1e6)
    return pd.Series(o)
df=pd.concat([df.reset_index(drop=True),df.apply(feat,axis=1).reset_index(drop=True)],axis=1)
df["mo"]=df.d.dt.to_period("M")
def st(g):
    if len(g)<8: return f"n={len(g)}"
    m=g.groupby("mo").R.mean(); t=m.mean()/(m.std(ddof=1)/math.sqrt(len(m))) if len(m)>2 else float('nan')
    return f"n={len(g):4d} {g.R.mean():+.3f}R (t {t:+.2f}) vinst {100*(g.R>0).mean():.0f} % | 2016-22 {g[g.per=='2016-22'].R.mean():+.3f}R (n {sum(g.per=='2016-22')}), 2023-26 {g[g.per=='2023-26'].R.mean():+.3f}R (n {sum(g.per=='2023-26')})"
def diff(a,b):
    d=a.R.mean()-b.R.mean(); s=math.sqrt(a.R.var()/len(a)+b.R.var()/len(b)); return f"skillnad {d:+.3f}R t={d/s:+.2f}"
print("Alla signaler (80 största + Lyko, marknadsfilter):", st(df))
for namn,sel in (("Köp senaste 3 dagar",df.k3>=1),("Köp senaste 7 dagar",df.k7>=1),("Köp senaste 14 dagar",df.k14>=1),
                 ("Köp från 5 d före andra bottnen",df.kb>=1),("Köp från andra bottnen",df.kb2>=1),
                 ("Köp från 5 d före botten, ≥ 1 Mkr",df.kb_mkr>=1),("Minst 2 köpare från 5 d före botten",df.kb>=2),("Minst 2 köpare senaste 14 dagar",df.k14>=2)):
    g=df[sel]; print(f"{namn:40s} {st(g)}  {diff(g,df[~sel]) if 8<=len(g)<len(df) else ''}")
