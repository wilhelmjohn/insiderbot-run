"""Kommande rapportdatum för alla aktier i Divergensscreen (yfinance). Skriver divergens_rapportdatum.csv (Ticker,Datum,Kalla).
Manuella datum i rapport_manuell.csv (Ticker,Datum) går alltid före. Körs av workflowen Divergens nordiska kurser."""
import time
from datetime import date, timedelta
import pandas as pd, yfinance as yf

k = pd.read_csv("kurser.csv", usecols=["Datum", "Ticker", "Stang", "Volym"])
k = k[k.Ticker.str.endswith(".ST") & (k.Datum >= (date.today() - timedelta(days=120)).isoformat())]
k["oms"] = k.Stang * k.Volym
tickers = list(k.groupby("Ticker").oms.median().sort_values(ascending=False).index[:100]) + ["LYKO-A.ST"]
for f, kol in (("divergens_nordic_info.csv", "Ticker"), ("divergens_usa_info.csv", "Ticker")):
    try: tickers += list(pd.read_csv(f)[kol])
    except FileNotFoundError: pass
tickers = list(dict.fromkeys(tickers))
rader, idag = [], pd.Timestamp(date.today())
for t in tickers:
    try:
        cal = yf.Ticker(t).calendar or {}
        ds = cal.get("Earnings Date") or []
        for d_ in ds:
            d_ = pd.Timestamp(d_)
            if idag - pd.Timedelta(days=3) <= d_ <= idag + pd.Timedelta(days=120):
                rader.append(dict(Ticker=t, Datum=d_.strftime("%Y-%m-%d"), Kalla="yahoo")); break
    except Exception:
        pass
    time.sleep(0.15)
df = pd.DataFrame(rader, columns=["Ticker", "Datum", "Kalla"])
try:
    man = pd.read_csv("rapport_manuell.csv").assign(Kalla="manuell")
    df = pd.concat([man[["Ticker", "Datum", "Kalla"]], df[~df.Ticker.isin(man.Ticker)]], ignore_index=True)
except FileNotFoundError:
    pass
df.sort_values(["Datum", "Ticker"]).to_csv("divergens_rapportdatum.csv", index=False)
print(f"Rapportdatum: {len(df)} av {len(tickers)} aktier")
