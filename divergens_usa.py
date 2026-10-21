"""Dagskurser för amerikanska storbolag till Divergensscreen: alla aktier i Nasdaq-100 och Dow Jones (yfinance).
Skriver divergens_usa.csv (Datum,Ticker,Stang,Volym) och divergens_usa_info.csv (Ticker,Namn,Sektor,Bors,Index,Hamtad).
Hämtar även S&P 500, Nasdaq-100, Dow Jones och USDSEK. Fyller på från senaste datum; nya tickers från 2016-01-01.
Indexlistorna läses från Wikipedia en gång i veckan (reservlista nedan om det misslyckas)."""
import io, time
from datetime import date, timedelta
from pathlib import Path
import pandas as pd, requests, yfinance as yf

KURSER, INFO = Path("divergens_usa.csv"), Path("divergens_usa_info.csv")
EXTRA = ["^GSPC", "^NDX", "^DJI", "USDSEK=X"]
START = "2016-01-01"
H = {"User-Agent": "Mozilla/5.0 (insiderbot; privat bevakning)"}

RESERV_NDX = """AAPL MSFT NVDA AMZN META GOOGL GOOG AVGO TSLA COST NFLX TMUS ASML CSCO AMD PEP LIN ADBE ISRG INTU QCOM TXN AMGN
BKNG CMCSA HON AMAT PDD ARM GILD VRTX PANW ADP SBUX MU LRCX ADI MELI KLAC INTC CRWD MDLZ CTAS APP PLTR MSTR REGN SNPS CDNS ORLY
MAR CEG FTNT PYPL DASH ASTS ABNB WDAY ADSK CSX ROP AEP CHTR MNST PCAR NXPI CPRT TTWO PAYX FAST ROST KDP DDOG AZN EA BKR XEL VRSK
EXC CTSH FANG CCEP ZS TEAM KHC GEHC IDXX LULU ODFL TTD CSGP DXCM ANSS CDW ON BIIB GFS MRVL WBD MDB ARM""".split()
RESERV_DJI = """AAPL AMGN AMZN AXP BA CAT CRM CSCO CVX DIS GS HD HON IBM JNJ JPM KO MCD MMM MRK MSFT NKE NVDA PG SHW TRV UNH V VZ WMT""".split()


def wiki(url, kolumner):
    r = requests.get(url, headers=H, timeout=30); r.raise_for_status()
    for t in pd.read_html(io.StringIO(r.text)):
        for k in kolumner:
            if k in t.columns and 25 <= len(t) <= 120:
                return [str(x).strip().replace(".", "-") for x in t[k].dropna()]
    raise ValueError("ingen tabell")


info = pd.read_csv(INFO) if INFO.exists() else pd.DataFrame(columns=["Ticker", "Namn", "Sektor", "Bors", "Index", "Hamtad"])
farsk = len(info) and (pd.Timestamp.today() - pd.to_datetime(info.Hamtad).max()).days < 7
if farsk:
    ndx = info.loc[info.Index.str.contains("NDX", na=False), "Ticker"].tolist()
    dji = info.loc[info.Index.str.contains("DJI", na=False), "Ticker"].tolist()
else:
    try: ndx = wiki("https://en.wikipedia.org/wiki/Nasdaq-100", ["Ticker", "Symbol"])
    except Exception as e: print("Nasdaq-100 från reservlistan:", e); ndx = RESERV_NDX
    try: dji = wiki("https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average", ["Symbol", "Ticker"])
    except Exception as e: print("Dow Jones från reservlistan:", e); dji = RESERV_DJI
ndx, dji = list(dict.fromkeys(ndx)), list(dict.fromkeys(dji))
TICKERS = list(dict.fromkeys(ndx + dji))
print(f"Nasdaq-100: {len(ndx)}, Dow Jones: {len(dji)}, totalt {len(TICKERS)}")

gamla = pd.read_csv(KURSER) if KURSER.exists() else pd.DataFrame(columns=["Datum", "Ticker", "Stang", "Volym"])
senaste = gamla.groupby("Ticker")["Datum"].max() if len(gamla) else pd.Series(dtype=str)
slut = (date.today() + timedelta(days=1)).isoformat()
delar, fel = [], []
for t in TICKERS + EXTRA:
    start = (pd.Timestamp(senaste[t]) - pd.Timedelta(days=5)).date().isoformat() if t in senaste.index else START
    try:
        h = yf.Ticker(t).history(start=start, end=slut, auto_adjust=True)
    except Exception as e:
        fel.append(f"{t}: {e.__class__.__name__}"); continue
    if h is None or h.empty: fel.append(f"{t}: tomt"); continue
    h.index = pd.to_datetime(h.index).tz_localize(None)
    delar.append(pd.DataFrame({"Datum": h.index.strftime("%Y-%m-%d"), "Ticker": t, "Stang": h["Close"].values,
                               "Volym": h["Volume"].values if "Volume" in h else float("nan")}).dropna(subset=["Stang"]))
    time.sleep(0.15)
if delar:
    alla = pd.concat([gamla] + delar, ignore_index=True).drop_duplicates(["Datum", "Ticker"], keep="last")
    alla.sort_values(["Ticker", "Datum"]).to_csv(KURSER, index=False)

behall = {r["Ticker"]: r for r in info.to_dict("records")} if farsk else {}
rader = []
for t in TICKERS:
    idx = ",".join(x for x, l in (("NDX", ndx), ("DJI", dji)) if t in l)
    if t in behall:
        r = behall[t]; r["Index"] = idx; rader.append(r); continue
    try:
        i = yf.Ticker(t).info
        bors = {"NMS": "Nasdaq", "NGM": "Nasdaq", "NCM": "Nasdaq", "NYQ": "NYSE", "NYS": "NYSE", "ASE": "NYSE"}.get(i.get("exchange"), i.get("exchange", ""))
        rader.append(dict(Ticker=t, Namn=i.get("shortName") or i.get("longName") or t, Sektor=i.get("sector", ""), Bors=bors, Index=idx,
                          Hamtad=date.today().isoformat()))
    except Exception:
        rader.append(dict(Ticker=t, Namn=t, Sektor="", Bors="", Index=idx, Hamtad=date.today().isoformat()))
    time.sleep(0.15)
pd.DataFrame(rader).to_csv(INFO, index=False)
print("SAKNAS:", "; ".join(fel) if fel else "inga")
