"""Oromått till Börsläget: kreditspreadar (FRED) och AI-/teknikaktier mot breda marknaden (Yahoo).

  python oro_data.py          # skriver oro.csv (Datum,Serie,Varde), senaste ~3 åren

FRED (ICE BofA option-adjusted spread, procentenheter över statsräntan):
  HY = BAMLH0A0HYM2  amerikanska högräntebolag (sämre kreditbetyg)
  IG = BAMLC0A0CM    amerikanska bolag med hög kreditvärdighet
Yahoo: ^NDX (Nasdaq-100), ^SOX (halvledarindex), ^GSPC (S&P 500), ^VIX.
Fel för en källa stoppar inte de andra.
"""
import io, sys
from datetime import date, timedelta
from pathlib import Path
import pandas as pd, requests

UT = Path("oro.csv")
H = {"User-Agent": "insiderbot/1.0 (https://github.com/wilhelmjohn/insiderbot-run; privat bevakning)"}
start = date.today() - timedelta(days=3 * 365)
delar, fel = [], []

for serie, sid in (("HY", "BAMLH0A0HYM2"), ("IG", "BAMLC0A0CM")):
    try:
        r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start.isoformat()}", headers=H, timeout=60)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = ["Datum", "Varde"]
        df["Varde"] = pd.to_numeric(df.Varde, errors="coerce")
        df = df.dropna(); df["Serie"] = serie
        delar.append(df[["Datum", "Serie", "Varde"]]); print(f"{serie}: {len(df)} dagar, senast {df.Datum.max()} = {df.Varde.iloc[-1]}")
    except Exception as e:
        fel.append(f"{serie}: {e.__class__.__name__}: {str(e)[:120]}")

try:
    import yfinance as yf
    for serie, t in (("NDX", "^NDX"), ("SOX", "^SOX"), ("SPX", "^GSPC"), ("VIX", "^VIX")):
        try:
            h = yf.Ticker(t).history(start=start.isoformat(), auto_adjust=False)
            h.index = pd.to_datetime(h.index).tz_localize(None)
            df = pd.DataFrame({"Datum": h.index.strftime("%Y-%m-%d"), "Serie": serie, "Varde": h.Close.values}).dropna()
            delar.append(df); print(f"{serie}: {len(df)} dagar, senast {df.Datum.max()} = {df.Varde.iloc[-1]:.2f}")
        except Exception as e:
            fel.append(f"{serie}: {e.__class__.__name__}: {str(e)[:120]}")
except ImportError as e:
    fel.append(f"yfinance: {e}")

if delar:
    ny = pd.concat(delar, ignore_index=True)
    gammal = pd.read_csv(UT) if UT.exists() else ny.iloc[:0]
    alla = pd.concat([gammal, ny]).drop_duplicates(["Datum", "Serie"], keep="last").sort_values(["Serie", "Datum"])
    alla[alla.Datum >= start.isoformat()].to_csv(UT, index=False)
print("FEL:", "; ".join(fel) if fel else "inga")
sys.exit(0 if delar else 1)
