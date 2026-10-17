"""Rullande beslutstest: första handelsdagen varje månad tas regelträffarna enligt bevakning.py
med bara den information som fanns då (köp publicerade före dagen, kurser före dagen).
Entry = stängning beslutsdagen, utfall efter 21 och 63 handelsdagar, mot medianaktien och OMXSPI."""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import backtest as bt, bevakning

MAPP = Path(__file__).resolve().parent
kop_all, _ = bt.las_kop(MAPP / "insiderkop.csv", MAPP / "ticker_map.csv")
px_all = bt.las_kurser(MAPP / "kurser.csv")
BESLUT = {"d": None}
bt.las_kop = lambda *a, **k: (kop_all[kop_all["Publicerat"] < BESLUT["d"]].copy(), {})
bt.las_kurser = lambda *a, **k: px_all[px_all.index < BESLUT["d"]]

aktier = [c for c in px_all.columns if c.endswith(".ST")]
dagar = px_all[bt.INDEX].dropna().index
rader = []
for m in pd.date_range("2021-03-01", "2026-08-01", freq="MS"):
    pos = dagar.searchsorted(m); d = dagar[pos]
    BESLUT["d"] = d
    res = bevakning.bevaka(MAPP / "insiderkop.csv", MAPP / "ticker_map.csv", MAPP / "kurser.csv",
                           dagar=30, idag=d, nyheter_fil=None)
    if res.empty: continue
    r = res[res.Regel == "ja"]
    for h in (21, 63):
        if pos + h >= len(dagar): continue
        d1 = dagar[pos + h]
        u = px_all.loc[[d, d1], aktier]; mr = (u.iloc[1] / u.iloc[0] - 1).dropna(); mr = mr[mr.abs() < 1]
        idx = px_all.loc[d1, bt.INDEX] / px_all.loc[d, bt.INDEX] - 1
        for _, x in r.iterrows():
            s = px_all[x.Ticker]
            if pd.isna(s.get(d)) or pd.isna(s.get(d1)): continue
            ut = s.loc[d:d1].dropna()
            rader.append(dict(Manad=d.strftime("%Y-%m"), Bolag=x.Bolag, Ticker=x.Ticker, H=h, Ret=ut.iloc[-1] / ut.iloc[0] - 1,
                              Median=mr.median(), Index=idx, Stoppad=bool((ut < x.Stop).any()), Bryt=bool((ut > x.Brytkurs).any()),
                              Min=ut.min() / ut.iloc[0] - 1))
    print(m.date(), len(r), flush=True)
df = pd.DataFrame(rader); df["AR"] = df.Ret - df.Median; df["ARi"] = df.Ret - df.Index
df.to_csv("/tmp/pit/rullande.csv", index=False)
