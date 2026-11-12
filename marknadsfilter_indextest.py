# Test 2026-11-12: OMXSPI köpt när Divergensscreens marknadsfilter slår på, mot köp och behåll (kör: python marknadsfilter_indextest.py <datamapp>)
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



import math
ix = idx.copy(); ix = ix[ix.index >= "2016-01-01"].dropna()
F = ((idx_dd <= -0.10) | (bredd > 0.70)).reindex(ix.index).fillna(False)
r1 = ix.pct_change().fillna(0)
start = F & ~F.shift(1, fill_value=False)
pos = {d: k for k, d in enumerate(ix.index)}
V = ix.values
print(f"OMXSPI {ix.index[0].date()}–{ix.index[-1].date()} (prisindex, utan utdelning). Filtret på {100*F.mean():.0f} % av dagarna, {int(start.sum())} gånger det slår på.")
print("\nA) Köp OMXSPI på stängningen när filtret slår på, håll N dagar, mot alla dagar:")
for N in (10, 20, 60):
    ev = [V[pos[d]+N]/V[pos[d]]-1 for d in ix.index[start] if pos[d]+N < len(V)]
    alla = (ix.shift(-N)/ix - 1).dropna()
    on = alla[F.reindex(alla.index)]
    print(f"  {N:2d} d: när filtret slår på {100*np.mean(ev):+.2f} % (n={len(ev)}, upp i {100*np.mean(np.array(ev)>0):.0f} %) | alla dagar med filtret på {100*on.mean():+.2f} % | alla dagar {100*alla.mean():+.2f} %")
def stat(ret, namn):
    eq = (1 + ret).cumprod(); ar = len(ret) / 252
    cagr = eq.iloc[-1] ** (1/ar) - 1; dd = (eq / eq.cummax() - 1).min(); vol = ret.std() * math.sqrt(252)
    print(f"  {namn:42s} årlig {100*cagr:+5.1f} %  maxras {100*dd:6.1f} %  vol {100*vol:4.1f} %  i marknaden {100*(ret!=0).mean():3.0f} %")
    return ret
print("\nB) Strategier, hela perioden (signalen på dagens stängning, position från nästa dag, 0,1 % kostnad per byte):")
def regel(inne):
    inne = inne.shift(1, fill_value=False).astype(bool)
    byte = inne != inne.shift(1, fill_value=False)
    return r1.where(inne, 0.0) - 0.001 * byte
stat(r1, "Köp och behåll OMXSPI")
stat(regel(F), "Äg index bara när filtret är på")
for N in (20, 60):
    h = pd.Series(False, index=ix.index)
    for d in ix.index[start]: h.iloc[pos[d]:pos[d]+N] = True
    stat(regel(h), f"Köp när filtret slår på, håll {N} dagar")
stat(regel(~F), "Äg index bara när filtret är av")
print("\nC) Per år (köp och behåll mot 'bara när filtret är på', i %):")
rr = pd.DataFrame({"behåll": r1, "filter_på": regel(F)})
print((rr.groupby(rr.index.year).apply(lambda x: (1+x).prod()-1)*100).round(1).T.to_string())
