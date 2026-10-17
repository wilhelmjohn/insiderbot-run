#!/usr/bin/env python3
"""Hämtar OHLC (justerade) för bolagen i ohlc_test_tickers.txt från 2024-08-01 och sparar ohlc_test.csv.
Används för ljusstakar i blindtesten (köptesterna). Körs i GitHub Actions; kraschar aldrig.
"""
from pathlib import Path
import pandas as pd

MAPP = Path(__file__).resolve().parent


def main():
    import yfinance as yf
    tick = [t.strip() for t in (MAPP / "ohlc_test_tickers.txt").read_text().split() if t.strip()]
    rader, ok = [], 0
    for i in range(0, len(tick), 30):
        grupp = tick[i:i + 30]
        try:
            d = yf.download(grupp, start="2024-08-01", auto_adjust=True, progress=False, group_by="ticker", threads=False)
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
        print(f"OHLC {min(i + 30, len(tick))}/{len(tick)} klara, {ok} bolag", flush=True)
    if rader:
        pd.concat(rader).to_csv(MAPP / "ohlc_test.csv", index=False)
        print(f"Sparade ohlc_test.csv för {ok} bolag")
    else:
        print("Inget hämtat")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ohlc_test.py misslyckades:", e)
