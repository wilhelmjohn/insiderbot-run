"""Dagskurser för norska och danska storbolag till Divergensscreen (via Yahoo Finance / yfinance).
Skriver divergens_nordic.csv (Datum,Ticker,Stang,Volym) och divergens_nordic_info.csv (Ticker,Namn,Sektor,Valuta).
Hämtar även valutakurser (NOKSEK=X, DKKSEK=X) och index (OSEBX, OMXC25) till samma kursfil.
Fyller på: tickers som redan finns hämtas från senaste datum minus 5 dagar, nya från 2016-01-01."""
import time
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
import yfinance as yf

NORGE = """EQNR DNB NHY MOWI TEL ORK YAR AKRBP SALM KOG STB GJF SUBC TOM NOD FRO AKER VAR NAS AUTO BAKKA LSG SCATC TGS HAFNI""".split()
DANMARK = """NOVO-B DSV MAERSK-B NSIS-B VWS ORSTED CARL-B DANSKE COLO-B GMAB PNDORA TRYG DEMANT GN ROCK-B ISS JYSK NKT RBREW NETC AMBU-B BAVA FLS ZEAL SYDB""".split()
TICKERS = [t + ".OL" for t in NORGE] + [t + ".CO" for t in DANMARK]
EXTRA = ["NOKSEK=X", "DKKSEK=X", "^OSEBX", "OBX.OL", "^OMXC25"]
KURSER, INFO = Path("divergens_nordic.csv"), Path("divergens_nordic_info.csv")
START = "2016-01-01"

gamla = pd.read_csv(KURSER) if KURSER.exists() else pd.DataFrame(columns=["Datum", "Ticker", "Stang", "Volym"])
senaste = gamla.groupby("Ticker")["Datum"].max() if len(gamla) else pd.Series(dtype=str)
slut = (date.today() + timedelta(days=1)).isoformat()
delar, ok, fel = [], [], []
for t in TICKERS + EXTRA:
    start = (pd.Timestamp(senaste[t]) - pd.Timedelta(days=5)).date().isoformat() if t in senaste.index else START
    try:
        h = yf.Ticker(t).history(start=start, end=slut, auto_adjust=True)
    except Exception as e:
        fel.append(f"{t}: {e.__class__.__name__}"); continue
    if h is None or h.empty:
        fel.append(f"{t}: tomt svar"); continue
    h.index = pd.to_datetime(h.index).tz_localize(None)
    delar.append(pd.DataFrame({"Datum": h.index.strftime("%Y-%m-%d"), "Ticker": t, "Stang": h["Close"].values,
                               "Volym": h["Volume"].values if "Volume" in h else float("nan")}).dropna(subset=["Stang"]))
    ok.append(t)
    time.sleep(0.2)
if delar:
    alla = pd.concat([gamla] + delar, ignore_index=True).drop_duplicates(["Datum", "Ticker"], keep="last")
    alla.sort_values(["Ticker", "Datum"]).to_csv(KURSER, index=False)

# namn/sektor/valuta hämtas om en gång i veckan
info = pd.read_csv(INFO) if INFO.exists() else pd.DataFrame(columns=["Ticker", "Namn", "Sektor", "Valuta", "Hamtad"])
gammal = set(info.loc[pd.to_datetime(info.Hamtad) > pd.Timestamp.today() - pd.Timedelta(days=7), "Ticker"]) if len(info) else set()
rader = [r for r in info.to_dict("records") if r["Ticker"] in gammal]
for t in TICKERS:
    if t in gammal: continue
    try:
        i = yf.Ticker(t).info
        rader.append(dict(Ticker=t, Namn=i.get("shortName") or i.get("longName") or t, Sektor=i.get("sector", ""),
                          Valuta=i.get("currency", "NOK" if t.endswith(".OL") else "DKK"), Hamtad=date.today().isoformat()))
    except Exception:
        rader.append(dict(Ticker=t, Namn=t, Sektor="", Valuta="NOK" if t.endswith(".OL") else "DKK", Hamtad=date.today().isoformat()))
    time.sleep(0.2)
pd.DataFrame(rader).to_csv(INFO, index=False)
print(f"Hämtade {len(ok)} av {len(TICKERS) + len(EXTRA)}")
print("SAKNAS:", "; ".join(fel) if fel else "inga")
