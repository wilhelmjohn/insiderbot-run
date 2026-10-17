"""Hämtar underlag till Sektortracker (körs i Actions, Yahoo nås därifrån):
1. Sektor och bransch för likvida .ST-bolag i kurser.csv (median omsättning senaste ~90 dagar minst MIN_OMS och minst 40 handelsdagar).
   Sparas i sektor_info.csv; bara saknade eller gamla rader hämtas om (sektorer ändras sällan).
2. Dagskurser (justerade) för sektor-ETF:er i USA, som jämförelse. Sparas i sektor_etf.csv.
Användning: python sektor_hamta.py [info|etf]   (utan argument båda)"""
import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

HER = Path(__file__).resolve().parent
MIN_OMS = 0.5e6          # SEK per dag (median)
MAX_ALDER = 60           # dagar innan en rad hämtas om
ETF = {"SPY": "S&P 500 (jämförelse)", "XLK": "Teknik", "XLV": "Hälsovård", "XLI": "Industri", "XLF": "Finans", "XLE": "Energi",
       "XLY": "Sällanköpsvaror", "XLP": "Dagligvaror", "XLU": "Kraft och vatten", "XLB": "Material", "XLRE": "Fastigheter",
       "XLC": "Media och telekom", "XBI": "Biotech", "SMH": "Halvledare"}
LOGG = HER / "sektor_logg.txt"


def logg(s):
    print(s, flush=True)
    with LOGG.open("a", encoding="utf-8") as f:
        f.write(f"{pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M')} {s}\n")


def likvida():
    k = pd.read_csv(HER / "kurser.csv", usecols=["Datum", "Ticker", "Stang", "Volym"], parse_dates=["Datum"])
    k = k[k.Ticker.str.endswith(".ST")].copy()
    k["Oms"] = k.Stang * k.Volym
    r = k[k.Datum > k.Datum.max() - pd.Timedelta(days=90)]
    med, n = r.groupby("Ticker").Oms.median(), r.groupby("Ticker").Datum.count()
    return sorted(med[(med >= MIN_OMS) & (n.reindex(med.index) >= 40)].index)


def info():
    p = HER / "sektor_info.csv"
    gammal = pd.read_csv(p) if p.exists() else pd.DataFrame(columns=["Ticker", "longName", "sector", "industry", "marketCap", "currency", "Hamtad"])
    idag = pd.Timestamp.now().normalize()
    ny = gammal.set_index("Ticker").to_dict("index") if len(gammal) else {}
    behov = [t for t in likvida() if t not in ny or pd.isna(ny[t].get("sector")) or (idag - pd.Timestamp(ny[t]["Hamtad"])).days > MAX_ALDER]
    logg(f"info: {len(likvida())} likvida bolag, {len(behov)} att hämta")
    ok = fel = 0
    for t in behov:
        for forsok in range(3):
            try:
                i = yf.Ticker(t).info or {}
                ny[t] = dict(longName=i.get("longName"), sector=i.get("sector"), industry=i.get("industry"), marketCap=i.get("marketCap"),
                             currency=i.get("currency"), Hamtad=str(idag.date()))
                ok += 1
                break
            except Exception as ex:  # nätverk eller begränsning: vänta och försök igen
                time.sleep(2 + 3 * forsok)
                if forsok == 2:
                    fel += 1
                    logg(f"info FEL {t}: {str(ex)[:80]}")
        time.sleep(0.25)
    df = pd.DataFrame.from_dict(ny, orient="index").reset_index(names="Ticker")
    df.to_csv(p, index=False)
    logg(f"info: {ok} hämtade, {fel} fel, {df.sector.notna().sum()} av {len(df)} har sektor")


def etf():
    rader = []
    for t in ETF:
        for forsok in range(3):
            try:
                h = yf.Ticker(t).history(period="max", interval="1d", auto_adjust=True)
                if h.empty:
                    raise ValueError("tomt")
                h = h.reset_index()
                h["Datum"] = pd.to_datetime(h["Date"]).dt.tz_localize(None).dt.normalize()
                rader.append(pd.DataFrame({"Datum": h["Datum"], "Ticker": t, "Close": h["Close"].round(4)}).query("Datum >= '2003-01-01'"))
                break
            except Exception as ex:
                time.sleep(2 + 3 * forsok)
                if forsok == 2:
                    logg(f"etf FEL {t}: {str(ex)[:80]}")
    if rader:
        df = pd.concat(rader)
        df.to_csv(HER / "sektor_etf.csv", index=False)
        logg(f"etf: {df.Ticker.nunique()} fonder, senaste dag {df.Datum.max().date()}")


if __name__ == "__main__":
    vad = sys.argv[1] if len(sys.argv) > 1 else ""
    if vad in ("", "info"):
        info()
    if vad in ("", "etf"):
        etf()
