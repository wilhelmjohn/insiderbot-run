"""Rapportdrift: köper aktier med stark kursreaktion på rapportdagen och mäter överavkastning mot index därefter.
python rapportdrift.py <datamapp>"""
import sys, math, numpy as np, pandas as pd
D = sys.argv[1].rstrip("/") + "/"
r = pd.read_csv(D + "rapporter.csv", parse_dates=["Reaktionsdag"])
pat = r.Rubrik.str.contains(r"delårsrapport|kvartalsrapport|bokslutskommuniké|halvårsrapport|interim report|year-end report|bokslut|q[1-4]", case=False, regex=True)
inv = r.Rubrik.str.contains(r"inbjud|bjuder in|presentation|webcast|webbsändning|telefonkonferens|kallelse|datum för|invitation|publicera|rättelse|correction", case=False, regex=True)
ev = r[pat & ~inv][["Ticker", "Reaktionsdag"]].rename(columns={"Reaktionsdag": "d"})
k = pd.read_csv(D + "kurser.csv", parse_dates=["Datum"]); k = k[k.Ticker.str.endswith(".ST")]
px = k.pivot_table(index="Datum", columns="Ticker", values="Stang").sort_index()
vo = k.pivot_table(index="Datum", columns="Ticker", values="Volym").sort_index().reindex_like(px)
px = px[px.index >= "2019-06-01"]; vo = vo.reindex(px.index)
idx = pd.read_csv(D + "marknad.csv", parse_dates=["Datum"]).set_index("Datum").Index.reindex(px.index).ffill()
dates = px.index; pos = {d: i for i, d in enumerate(dates)}
turn = (px * vo).rolling(60, min_periods=40).median()
rank = turn.rank(axis=1, ascending=False)
ma200 = idx.rolling(200).mean(); dd = idx / idx.rolling(250, min_periods=200).max() - 1
P = px.values; I = idx.values
rows = []
ev = ev.sort_values(["Ticker", "d"]).drop_duplicates()
senast = {}
for t, d in ev.itertuples(index=False):
    if t not in px.columns: continue
    j = dates.searchsorted(d)
    if j >= len(dates) or j < 1: continue
    if t in senast and j - senast[t] < 20: continue      # dubbletter av samma rapport
    senast[t] = j
    c = P[:, px.columns.get_loc(t)]
    if not (np.isfinite(c[j]) and np.isfinite(c[j - 1])): continue
    reak = (c[j] / c[j - 1] - 1) - (I[j] / I[j - 1] - 1)
    oms = turn[t].iloc[j - 1]
    rk = rank[t].iloc[j - 1]
    o = dict(t=t, d=dates[j], reak=reak, oms=oms, rk=rk, upp=bool(I[j] > ma200.iloc[j]) if np.isfinite(ma200.iloc[j]) else None,
             press=bool(dd.iloc[j] <= -0.10) if np.isfinite(dd.iloc[j]) else None)
    e = j + 1                                            # köp dagen efter på stängningen
    for h in (5, 20, 60):
        x = e + h
        if x < len(dates) and np.isfinite(c[e]) and np.isfinite(c[x]):
            o[f"r{h}"] = (c[x] / c[e] - 1) - (I[x] / I[e] - 1)
        else: o[f"r{h}"] = np.nan
    rows.append(o)
df = pd.DataFrame(rows)
df = df[(df.oms >= 2e6)]                                 # minst 2 Mkr median omsättning per dag
df["per"] = np.where(df.d < "2024-01-01", "2021-23", "2024-26")
df["ym"] = df.d.dt.to_period("M")
df.to_csv("rapportdrift.csv", index=False)
def stat(g, col):
    g = g.dropna(subset=[col])
    if len(g) < 10: return f"n={len(g)}"
    m = g.groupby("ym")[col].mean()                      # månadssnitt, tar hand om överlapp och klustring
    t = m.mean() / (m.std(ddof=1) / math.sqrt(len(m))) if len(m) > 2 else float("nan")
    return f"n={len(g):4d} snitt {100*g[col].mean():+5.2f} % median {100*g[col].median():+5.2f} % träff {100*(g[col]>0).mean():3.0f} % t={t:+.2f}"
pd.set_option("display.width", 200)
print(f"Rapporter: {len(df)} ({df.t.nunique()} bolag, {df.d.min().date()}–{df.d.max().date()}), minst 2 Mkr/dag")
df["dec"] = pd.qcut(df.reak, 10, labels=False) + 1
print("\nReaktion (mot index) per tiondel, överavkastning efter köp dagen efter:")
for q, g in df.groupby("dec"):
    print(f"  {q:2d}  reaktion {100*g.reak.min():+6.1f}..{100*g.reak.max():+6.1f} %   20 d: {stat(g,'r20')}   60 d: {stat(g,'r60')}")
for namn, sel in (("Stark uppgång ≥ +5 %", df.reak >= 0.05), ("Stark nedgång ≤ −5 %", df.reak <= -0.05), ("Alla rapporter", df.reak.notna())):
    g = df[sel]; print(f"\n{namn}")
    for h in ("r5", "r20", "r60"): print(f"  {h[1:]:>2} d: {stat(g,h)}")
    for per, gg in g.groupby("per"): print(f"  {per} 20 d: {stat(gg,'r20')} | 60 d: {stat(gg,'r60')}")
    for lab, gg in g.groupby("upp"): print(f"  index {'över' if lab else 'under'} 200-dagars 20 d: {stat(gg,'r20')} | 60 d: {stat(gg,'r60')}")
    for lab, gg in g.groupby("press"): print(f"  index {'≥10 % under topp' if lab else 'nära topp'} 20 d: {stat(gg,'r20')} | 60 d: {stat(gg,'r60')}")
    for lab, gg in g.groupby(g.rk <= 80): print(f"  {'80 största' if lab else 'mindre bolag'} 20 d: {stat(gg,'r20')} | 60 d: {stat(gg,'r60')}")
