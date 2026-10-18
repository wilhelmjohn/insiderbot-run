#!/usr/bin/env python3
"""
Samlar analytikernas riktkurser för alla svenska bolag i kurser.csv (Yahoo Finance) och sparar två filer i datamappen:

  riktkurs_logg.csv   en ögonblicksbild per bolag och dag (Datum, Ticker, Kurs, RK_snitt, RK_hog, RK_lag, Antal, RekSnitt).
                      Läggs till vid varje körning (en rad per bolag och datum), så att ett backtest senare kan använda
                      uppgifter som de såg ut vid respektive datum, utan efterhandsfel.
  riktkurs_hist.csv   historiska riktkursändringar per analyshus (Datum, Ticker, Hus, Atgard, Fran, Till, RK_ny, RK_gammal,
                      RK_atgard) från Yahoos upgrades_downgrades, där de finns. Täckningen för svenska småbolag är tunn.

Körs i GitHub Actions (Yahoo nås inte från andra miljöer); kraschar aldrig. Skriver sammanfattning av täckningen.

  python riktkurs_data.py
"""
import time
from datetime import date
from pathlib import Path

import pandas as pd

MAPP = Path(__file__).resolve().parent
LOGG = MAPP / "riktkurs_logg.csv"
HIST = MAPP / "riktkurs_hist.csv"
LK = ["Datum", "Ticker", "Kurs", "RK_snitt", "RK_hog", "RK_lag", "Antal", "RekSnitt"]
HK = ["Datum", "Ticker", "Hus", "Atgard", "Fran", "Till", "RK_ny", "RK_gammal", "RK_atgard"]


def num(v):
    try:
        v = float(v)
        return None if v != v else v
    except Exception:
        return None


def main():
    import yfinance as yf
    k = pd.read_csv(MAPP / "kurser.csv", usecols=["Ticker"])
    tick = sorted(k["Ticker"].astype(str).unique())
    idag = date.today().isoformat()
    logg, hist, med_rk, med_hist = [], [], 0, 0
    for n, t in enumerate(tick, 1):
        try:
            y = yf.Ticker(t)
            info = y.info or {}
            rk = num(info.get("targetMeanPrice"))
            logg.append([idag, t, num(info.get("currentPrice") or info.get("regularMarketPrice")), rk,
                         num(info.get("targetHighPrice")), num(info.get("targetLowPrice")),
                         info.get("numberOfAnalystOpinions"), num(info.get("recommendationMean"))])
            med_rk += rk is not None
        except Exception as e:
            print(f"  {t}: info {e.__class__.__name__}")
        try:
            ud = y.upgrades_downgrades
            if ud is not None and len(ud):
                u = ud.reset_index()
                dc = u.columns[0]
                for _, r in u.iterrows():
                    hist.append([str(r[dc])[:10], t, r.get("Firm"), r.get("Action"), r.get("FromGrade"), r.get("ToGrade"),
                                 num(r.get("currentPriceTarget")), num(r.get("priorPriceTarget")), r.get("priceTargetAction")])
                med_hist += 1
        except Exception:
            pass
        if n % 50 == 0:
            print(f"{n}/{len(tick)} bolag, {med_rk} med riktkurs, {med_hist} med historik")
        time.sleep(0.25)
    new = pd.DataFrame(logg, columns=LK)
    old = pd.read_csv(LOGG) if LOGG.exists() else pd.DataFrame(columns=LK)
    out = pd.concat([old, new]).drop_duplicates(["Datum", "Ticker"], keep="last")
    out.to_csv(LOGG, index=False)
    h = pd.DataFrame(hist, columns=HK)
    if HIST.exists():
        h = pd.concat([pd.read_csv(HIST), h])
    h.drop_duplicates(["Datum", "Ticker", "Hus", "RK_ny", "RK_gammal"]).sort_values(["Ticker", "Datum"]).to_csv(HIST, index=False)
    print(f"KLAR: {len(tick)} bolag, {med_rk} med riktkurs idag, {med_hist} med historik, {len(h)} historikrader")


if __name__ == "__main__":
    main()
