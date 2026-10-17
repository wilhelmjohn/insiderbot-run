#!/usr/bin/env python3
"""
Hämtar dagliga Open/High/Low/Close (justerade, som i kurser.csv) för Stockholmsbolagen i bevakning.csv de senaste
~420 dagarna och sparar ohlc.csv (Datum, Ticker, Open, High, Low, Close). Används bara för ljusstakar (candlesticks)
i graferna; saknas filen eller bolaget ritas grafen med slutkurslinje som förut.
Körs i GitHub Actions (Yahoo nås inte från andra miljöer); kraschar aldrig.

  python ohlc.py
"""
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

MAPP = Path(__file__).resolve().parent
UT = MAPP / "ohlc.csv"
DAGAR = 420


def main():
    import yfinance as yf
    b = pd.read_csv(MAPP / "bevakning.csv")
    tick = sorted({t for t in b["Ticker"].astype(str) if t.endswith(".ST")})
    start = (date.today() - timedelta(days=DAGAR)).isoformat()
    rader, ok = [], 0
    for i in range(0, len(tick), 40):
        grupp = tick[i:i + 40]
        try:
            d = yf.download(grupp, start=start, auto_adjust=True, progress=False, group_by="ticker", threads=False)
        except Exception as e:
            print(f"  grupp {i}: {e.__class__.__name__}")
            continue
        for t in grupp:
            try:
                x = d[t][["Open", "High", "Low", "Close"]].dropna()
            except Exception:
                print(f"  {t}: ingen OHLC")
                continue
            if x.empty:
                continue
            x = x.round(4).reset_index()
            x.columns = ["Datum", "Open", "High", "Low", "Close"]
            x.insert(1, "Ticker", t)
            rader.append(x)
            ok += 1
        print(f"OHLC {min(i + 40, len(tick))}/{len(tick)} klara, {ok} bolag hämtade", flush=True)
    if not rader:
        print("Inget hämtat, behåller befintlig ohlc.csv")
        return
    pd.concat(rader).to_csv(UT, index=False)
    print(f"Sparade ohlc.csv för {ok} bolag")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ohlc.py misslyckades:", e)
