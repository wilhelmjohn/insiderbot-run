"""Hämtar OHLCV för valfria tickers från Yahoo (körs i GitHub Actions) och sparar snabbkurs.csv.
Användning: python snabbkurs.py INFRA.ST,SAND.ST [period=6mo]"""
import sys
import pandas as pd
import yfinance as yf

tick = [t.strip() for t in sys.argv[1].split(",") if t.strip()]
per = sys.argv[2] if len(sys.argv) > 2 else "6mo"
d = []
for t in tick:
    h = yf.Ticker(t).history(period=per, interval="1d", auto_adjust=False)
    if h.empty:
        print(t, "ingen data"); continue
    h = h.reset_index()
    h["Datum"] = pd.to_datetime(h["Date"]).dt.tz_localize(None).dt.normalize()
    h["Ticker"] = t
    d.append(h[["Datum", "Ticker", "Open", "High", "Low", "Close", "Volume"]])
    print(t, h["Datum"].iloc[-1].date(), h["Close"].iloc[-1])
if d:
    pd.concat(d).to_csv("snabbkurs.csv", index=False)
