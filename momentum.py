"""Momentumtest på Stockholmsbörsen (universum = bolag i kurser.csv, alltså med survivorship-bias).
Månadsvis ombalansering, lika vikt, köp bästa kvintilen efter 'moment' (avkastning över L månader, exkl. senaste månaden).
Kostnad 0,08 % per affär på omsatt andel. Jämförelse: lika viktat universum och ^OMXSPI."""
import numpy as np, pandas as pd

KOST = 0.0008; MINKURS = 5.0

def manadspriser():
    k = pd.read_csv("kurser.csv", usecols=["Datum", "Ticker", "Stang"], parse_dates=["Datum"])
    px = k.pivot_table(index="Datum", columns="Ticker", values="Stang").sort_index()
    return px.resample("ME").last().where(px.resample("ME").count() > 0)

def likviditet():
    """Median omsättning (SEK/dag) senaste ~3 månader, per månadsslut."""
    k = pd.read_csv("kurser.csv", usecols=["Datum", "Ticker", "Stang", "Volym"], parse_dates=["Datum"])
    k["Oms"] = k.Stang * k.Volym
    o = k.pivot_table(index="Datum", columns="Ticker", values="Oms").sort_index()
    return o.rolling(63, min_periods=30).median().resample("ME").last()

def kor(L=12, andel=0.2, trendfilter=False, kost=KOST, start=None, minoms=0):
    m = manadspriser(); liq = likviditet().reindex(index=m.index, columns=m.columns)
    m_ = m; idx = m.pop("^OMXSPI"); m = m.loc[:, m.columns.str.endswith(".ST")]
    ret = m.pct_change(fill_method=None)
    ret = ret.where(ret.abs() < 3)                    # datafel (>±300 %/mån) bort
    mom = m.shift(1) / m.shift(L + 1) - 1             # L månader, exkl. senaste månaden
    ok = (m.shift(1) >= MINKURS) & mom.notna() & (liq.shift(1) >= minoms)
    ma10 = idx.rolling(10).mean()
    port, univ, forra = [], [], pd.Series(dtype=float)
    for t in range(L + 1, len(m) - 0):
        d = m.index[t]
        r = ret.iloc[t]; kandidater = ok.iloc[t] & r.notna()
        if kandidater.sum() < 40: continue
        s = mom.iloc[t][kandidater]; n = max(int(len(s) * andel), 5)
        val = s.nlargest(n).index
        vikt = pd.Series(1 / n, index=val)
        i_marknad = (not trendfilter) or (idx.iloc[t-1] > ma10.iloc[t-1])
        if not i_marknad: vikt = vikt * 0
        oms = vikt.sub(forra, fill_value=0).abs().sum()
        port.append((d, (vikt * r.reindex(val).fillna(0)).sum() - oms * kost))
        univ.append((d, r[kandidater].mean())); forra = vikt
    p = pd.Series(dict(port)); u = pd.Series(dict(univ)); i = idx.pct_change().reindex(p.index)
    if start: p, u, i = p[start:], u[start:], i[start:]
    return p, u, i

def sammanfatta(namn, x):
    ar = (1 + x).prod() ** (12 / len(x)) - 1
    dd = ((1 + x).cumprod() / (1 + x).cumprod().cummax() - 1).min()
    return f"{namn:<26} {ar:7.1%}/år  vol {x.std()*np.sqrt(12):5.1%}  max DD {dd:6.1%}  hit {(x>0).mean():4.0%}  n={len(x)}"

if __name__ == "__main__":
    import sys; mo = float(sys.argv[1]) if len(sys.argv) > 1 else 0
    for L, a, tf in ((12, .2, False), (12, .2, True), (6, .2, False), (12, .1, False)):
        p, u, i = kor(L, a, tf, minoms=mo)
        print(f"\n--- Moment {L} mån, topp {a:.0%}{', trendfilter' if tf else ''}, minoms {mo:,.0f}  ({p.index[0].date()}..{p.index[-1].date()})")
        print(sammanfatta("Strategi", p)); print(sammanfatta("Alla aktier (lika vikt)", u)); print(sammanfatta("OMXSPI", i))
        ex = p - i; print("Överavkastning vs index: %.2f%%/mån, t=%.2f; vs universum: %.2f%%/mån, t=%.2f" % (
            ex.mean()*100, ex.mean()/ex.std()*np.sqrt(len(ex)), (p-u).mean()*100, (p-u).mean()/(p-u).std()*np.sqrt(len(p))))
