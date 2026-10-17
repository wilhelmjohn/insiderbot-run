#!/usr/bin/env python3
"""
Rapportdagar och marknadens reaktion.

Tar alla rapporter (Typ = rapport i nyheter.csv: delårs-, kvartals-, halvårs- och
bokslutsrapporter) och mäter hur aktien reagerade jämfört med index:

  Reaktion_dag  = aktiens dagsavkastning på reaktionsdagen minus indexets (procentenheter)
  Reaktion_2d   = samma över reaktionsdagen och dagen efter
  Forsprang_10d = aktiens avvikelse mot index de 10 handelsdagarna före reaktionsdagen
                  (har aktien redan stigit/fallit inför rapporten?)
  Utfall        = positiv / negativ / neutral (gräns --gräns, standard 3 procentenheter)

Reaktionsdagen är publiceringsdagen om rapporten kom före 17:30 (svensk tid), annars
nästa handelsdag. Rapporter före börsöppning och under dagen får samma dag.

OBS: detta är marknadens reaktion, inte en jämförelse mot analytikernas förväntningar
(konsensus finns inte gratis). Ett överraskande bra rapport som redan var inprisad kan
därför visa neutral eller negativ reaktion. Reaktionen är det mest objektiva mått som
går att räkna fram ur kurserna.

Användning:
  python rapporter.py                      # skriver rapporter.csv
  python rapporter.py --ticker ASKER.ST    # visar ett bolag
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

MAPP = Path(__file__).resolve().parent
NYHETER = MAPP / "nyheter.csv"
KURSER = MAPP / "kurser.csv"
TICKERMAP = MAPP / "ticker_map.csv"
UT = MAPP / "rapporter.csv"
INDEX = "^OMXSPI"
EFTER_STANGNING = pd.Timedelta(hours=17, minutes=30)


def berakna(nyheter: pd.DataFrame, px: pd.DataFrame, bolag_ticker: dict,
            index: str = INDEX, grans: float = 3.0) -> pd.DataFrame:
    """En rad per rapport med reaktion mot index. px: datum x ticker (stängningskurser)."""
    r = nyheter[nyheter["Typ"] == "rapport"].copy()
    r["Ticker"] = r["Bolag_id"].map(bolag_ticker)
    r = r[r["Ticker"].isin(px.columns)]
    if r.empty or index not in px.columns:
        return pd.DataFrame(columns=["Ticker", "Bolag", "Publicerat", "Reaktionsdag", "Reaktion_dag",
                                     "Reaktion_2d", "Forsprang_10d", "Utfall", "Rubrik"])
    dagar = px.index
    avk = px.pct_change(fill_method=None)
    rad = []
    for x in r.itertuples():
        tid = x.Publicerat
        dag = tid.normalize()
        if (tid - dag) >= EFTER_STANGNING:
            dag += pd.Timedelta(days=1)
        i = dagar.searchsorted(dag, side="left")          # första handelsdag >= dag
        if i >= len(dagar):
            continue
        a = avk[x.Ticker]
        b = avk[index]
        d0 = a.iloc[i] - b.iloc[i]
        d2 = (1 + a.iloc[i:i + 2]).prod() - (1 + b.iloc[i:i + 2]).prod() if i + 1 < len(dagar) else float("nan")
        if pd.isna(d0):
            continue
        d0, d2 = d0 * 100, d2 * 100
        # försprång: avvikelse mot index de 10 handelsdagarna FÖRE reaktionsdagen
        fore = (1 + a.iloc[max(i - 10, 0):i]).prod() - (1 + b.iloc[max(i - 10, 0):i]).prod() if i >= 5 else float("nan")
        fore = None if pd.isna(fore) else round(fore * 100, 1)
        utfall = "positiv" if d0 >= grans else "negativ" if d0 <= -grans else "neutral"
        rad.append((x.Ticker, x.Bolag, tid, dagar[i].date().isoformat(), round(d0, 1),
                    None if pd.isna(d2) else round(d2, 1), fore, utfall, x.Rubrik))
    ut = pd.DataFrame(rad, columns=["Ticker", "Bolag", "Publicerat", "Reaktionsdag", "Reaktion_dag",
                                    "Reaktion_2d", "Forsprang_10d", "Utfall", "Rubrik"])
    return ut.sort_values(["Ticker", "Publicerat"]).reset_index(drop=True)


RAPPORT_FONSTER = 20          # handelsdagar efter reaktionsdagen som en rapport räknas som "nyligen"
FORSPRANG_GRANS = 5.0         # procentenheter
RAPPORT_ETIKETT = {-1: "okand", 0: "ingen_rapport_20d", 1: "negativ_reaktion", 2: "neutral_reaktion", 3: "positiv_reaktion"}
FORSPRANG_ETIKETT = {-1: "okand", 0: "ingen_rapport_20d", 1: "fall_fore_rapport", 2: "flat_fore_rapport", 3: "uppgang_fore_rapport"}


def rapportstillstand(px: pd.DataFrame, nyheter: pd.DataFrame, bolag_ticker: dict,
                      fonster: int = RAPPORT_FONSTER, grans: float = 3.0, fgrans: float = FORSPRANG_GRANS):
    """Dimensioner för backtestet (datum x ticker): reaktionen på senaste rapporten och
    försprånget före den, så länge rapporten är högst `fonster` handelsdagar gammal.
    Reaktionen är känd först dagen EFTER reaktionsdagen (inget läckage in i inträdet).
    -1 = bolaget saknas i nyhetsdatan (t.ex. Spotlight/NGM), 0 = ingen rapport nyligen."""
    ut = berakna(nyheter, px, bolag_ticker, grans=grans)
    dagar = px.index
    kol = {t: i for i, t in enumerate(px.columns)}
    rea = np.zeros(px.shape, dtype=int)
    fsp = np.zeros(px.shape, dtype=int)
    for x in ut.sort_values("Publicerat").itertuples():
        i = dagar.searchsorted(pd.Timestamp(x.Reaktionsdag), side="left")
        j = kol[x.Ticker]
        a, b = i + 1, min(i + 1 + fonster, len(dagar))
        if a >= b:
            continue
        rea[a:b, j] = {"negativ": 1, "neutral": 2, "positiv": 3}[x.Utfall]
        f = x.Forsprang_10d
        fsp[a:b, j] = -1 if pd.isna(f) else (1 if f <= -fgrans else 3 if f >= fgrans else 2)
    tackta = set(nyheter["Bolag_id"].map(bolag_ticker).dropna())
    okanda = [t for t in px.columns if t not in tackta]
    if okanda:
        rea[:, [kol[t] for t in okanda]] = -1
        fsp[:, [kol[t] for t in okanda]] = -1
    mk = lambda m: pd.DataFrame(m, index=dagar, columns=px.columns)
    return {"Rapport_reaktion": (mk(rea), RAPPORT_ETIKETT), "Rapport_forsprang": (mk(fsp), FORSPRANG_ETIKETT)}


def las_kurser(path=KURSER) -> pd.DataFrame:
    k = pd.read_csv(path, usecols=["Datum", "Ticker", "Stang"], parse_dates=["Datum"])
    return k.pivot_table(index="Datum", columns="Ticker", values="Stang").sort_index()


def las_ticker(path=TICKERMAP) -> dict:
    m = pd.read_csv(path, keep_default_na=False, dtype=str)
    m = m[(m["Status"] == "ok") & (m["Ticker"] != "")]
    return dict(zip(m["Bolag_id"], m["Ticker"]))


def kor(grans: float = 3.0, ticker: str = None, nyheter=NYHETER, kurser=KURSER,
        tickermap=TICKERMAP, ut=UT) -> pd.DataFrame:
    if not Path(nyheter).exists():
        print("nyheter.csv saknas: kör nyheter.py först (rapportdatum kommer därifrån).")
        return pd.DataFrame()
    n = pd.read_csv(nyheter, keep_default_na=False, dtype=str)
    n["Publicerat"] = pd.to_datetime(n["Publicerat"])
    res = berakna(n, las_kurser(kurser), las_ticker(tickermap), grans=grans)
    if ticker is None:
        res.to_csv(ut, index=False)
        print(f"{len(res)} rapporter i {Path(ut).name}: " + ", ".join(f"{k} {v}" for k, v in res["Utfall"].value_counts().items()))
    else:
        res = res[res["Ticker"] == ticker]
        print(res.drop(columns="Ticker").to_string(index=False))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker")
    ap.add_argument("--grans", type=float, default=3.0, help="procentenheter över/under index för positiv/negativ")
    a = ap.parse_args()
    kor(a.grans, a.ticker)


if __name__ == "__main__":
    main()
