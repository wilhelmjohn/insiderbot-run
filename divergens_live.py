"""Kurser under dagen till Divergensscreen (Yahoo Finance via yfinance, ca 15 min fördröjning).

  python divergens_live.py            # skriver divergens_live.json, avbryter med kod 3 om börsen är stängd
  python divergens_live.py --stangt   # efter stängning: tar dagens sista kurs (bekräftad körning)

Hämtar de ~100 mest omsatta svenska aktierna (byggaren väljer sedan topp 80), Lyko och de norska och danska
aktierna i divergens_nordic.csv. En kurs räknas bara om den är från i dag; under dagen dessutom högst 45 minuter
gammal. Om färre än 30 aktier har färsk kurs räknas börsen som stängd (helgdag, tekniskt fel) och inget byggs.
"""
import json, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf

STHLM = ZoneInfo("Europe/Stockholm")
nu = datetime.now(STHLM)
stangt = "--stangt" in sys.argv

k = pd.read_csv("kurser.csv", usecols=["Datum", "Ticker", "Stang", "Volym"])
k = k[k.Ticker.str.endswith(".ST") & (k.Datum >= (nu - timedelta(days=120)).strftime("%Y-%m-%d"))]
k["oms"] = k.Stang * k.Volym
oms = k.groupby("Ticker").oms.median().sort_values(ascending=False)
tickers = list(oms.index[:100]) + ["LYKO-A.ST"]
try:
    nord = pd.read_csv("divergens_nordic.csv", usecols=["Ticker"]).Ticker.unique()
    tickers += [t for t in nord if t.endswith((".OL", ".CO")) and t not in ("OBX.OL", "OSEBX.OL")]
except FileNotFoundError:
    pass
try:
    tickers += [t for t in pd.read_csv("divergens_usa_info.csv", usecols=["Ticker"]).Ticker]
except FileNotFoundError:
    pass
tickers = list(dict.fromkeys(tickers + ["^OMXSPI", "^OMXC25", "^GSPC"]))

h = yf.download(tickers, period="2d", interval="15m", group_by="ticker", auto_adjust=False, progress=False, threads=True)
live, tid = {}, {}
for t in tickers:
    try:
        s = h[t]["Close"].dropna()
    except KeyError:
        continue
    if s.empty:
        continue
    ts = s.index[-1]
    ts = (ts.tz_localize("UTC") if ts.tzinfo is None else ts).tz_convert(STHLM)
    if ts.date() != nu.date():
        continue
    # dagens senaste kurs används alltid (efter stängning är det stängningskursen); färskhet räknas för att se om någon börs är öppen
    live[t] = round(float(s.iloc[-1]), 4)
    tid[t] = ts.strftime("%H:%M")
farska = sum(1 for t, x in tid.items() if nu - pd.Timestamp(f"{nu.date()} {x}").tz_localize(STHLM) <= timedelta(minutes=45))
print(f"Kurser från i dag: {len(live)} av {len(tickers)}, varav {farska} färska (högst 45 min)")
if len(live) < 30 or (not stangt and farska < 30):
    print("Börsen verkar stängd eller kurserna är gamla. Bygger inte.")
    sys.exit(3)
json.dump(live, open("divergens_live.json", "w"), indent=0)
json.dump(dict(hamtad=nu.strftime("%Y-%m-%d %H:%M"), stangt=stangt, tid=tid), open("divergens_live_meta.json", "w"), indent=0)
