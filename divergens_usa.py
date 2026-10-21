"""Dagskurser för amerikanska storbolag till Divergensscreen: alla aktier i Nasdaq-100 och Dow Jones (yfinance).
Skriver divergens_usa.csv (Datum,Ticker,Stang,Volym) och divergens_usa_info.csv (Ticker,Namn,Sektor,Bors,Index,Hamtad).
Hämtar även S&P 500, Nasdaq-100, Dow Jones och USDSEK. Fyller på från senaste datum; nya tickers från 2016-01-01.
Indexlistorna läses från Wikipedia en gång i veckan (reservlista nedan om det misslyckas)."""
import io, re, time
from datetime import date, timedelta
from pathlib import Path
import pandas as pd, requests, yfinance as yf

KURSER, INFO = Path("divergens_usa.csv"), Path("divergens_usa_info.csv")
EXTRA = ["^GSPC", "^NDX", "^DJI", "USDSEK=X"]
START = "2016-01-01"
H = {"User-Agent": "insiderbot/1.0 (https://github.com/wilhelmjohn/insiderbot-run; privat bevakning)"}

# Reservlistor (Wikipedia 2026-10-21). Används om hämtningen av indexlistorna misslyckas.
RESERV_NDX = """ADBE AMD ABNB ALNY GOOGL GOOG AMZN AEP AMGN ADI AAPL AMAT APP ARM ASML ALAB ADSK ADP AXON BKR BKNG AVGO CDNS CTAS
CSCO CCEP CMCSA CEG CPRT CRWV COST CRWD CSX DDOG DXCM FANG DASH EA EXC FAST FER FTNT GEHC GILD HON IDXX INTC INTU ISRG KDP KLAC
KHC LRCX LIN LITE MAR MRVL MELI META MCHP MU MSFT MSTR MDLZ MPWR MNST NBIS NFLX NVDA NXPI ORLY ODFL PCAR PLTR PANW PAYX PYPL PDD
PEP QCOM REGN RKLB ROP ROST SNDK STX SHOP SBUX SNPS TMUS TTWO TER TSLA TXN TRI VRTX WMT WBD WDC WDAY XEL""".split()
RESERV_DJI = """MMM GOOGL AXP AMGN AMZN AAPL BA CAT CVX CSCO KO DIS GS HD HON IBM JNJ JPM MCD MRK MSFT NKE NVDA PG CRM SHW TRV UNH V
WMT""".split()


def wiki(url, kolumner):
    r = requests.get(url, headers=H, timeout=30); r.raise_for_status()
    tabeller = pd.read_html(io.StringIO(r.text))
    print(f"  {url}: {len(r.text)} tecken, {len(tabeller)} tabeller, storlekar {[len(t) for t in tabeller][:12]}")
    basta = (0, None, None)
    for n_, t in enumerate(tabeller):
        if not 25 <= len(t) <= 120: continue
        for c in t.columns:
            v = [str(x).strip() for x in t[c].dropna()]
            ok = [x.replace(".", "-") for x in v if re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", x)]
            andel = len(ok) / max(len(t), 1)
            if andel > basta[0]: basta = (andel, ok, f"tabell {n_}, kolumn {c!r}, {len(t)} rader")
    print(f"    bästa: {basta[2]}, andel {basta[0]:.2f}, t.ex. {(basta[1] or [])[:5]}")
    if basta[0] >= 0.9: return basta[1]
    raise ValueError("ingen tabell")


info = pd.read_csv(INFO) if INFO.exists() else pd.DataFrame(columns=["Ticker", "Namn", "Sektor", "Bors", "Index", "Hamtad"])
farsk = len(info) and (pd.Timestamp.today() - pd.to_datetime(info.Hamtad).max()).days < 7
forra_ndx = info.loc[info.Index.str.contains("NDX", na=False), "Ticker"].tolist() if len(info) else []
forra_dji = info.loc[info.Index.str.contains("DJI", na=False), "Ticker"].tolist() if len(info) else []
try: ndx = wiki("https://en.wikipedia.org/wiki/Nasdaq-100", ["Ticker", "Symbol"])
except Exception as e: print("Nasdaq-100: Wikipedia misslyckades,", e); ndx = RESERV_NDX
try: dji = wiki("https://en.wikipedia.org/wiki/List_of_Dow_Jones_Industrial_Average_companies", ["Symbol", "Ticker"])
except Exception as e: print("Dow Jones: Wikipedia misslyckades,", e); dji = RESERV_DJI
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
