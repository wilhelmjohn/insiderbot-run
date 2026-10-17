#!/usr/bin/env python3
"""
Kurshistorik för bolagen i insiderkop.csv (via Yahoo Finance / yfinance).

Gör fyra saker:
  1. Väljer ett ISIN per bolag ur insiderkop.csv.
  2. Hittar Yahoo-ticker för varje bolag och sparar i ticker_map.csv.
     Du kan rätta rader där Status inte är "ok" för hand: sätt Ticker och
     Status = "manuell", så skrivs den aldrig över.
  3. Hämtar dagliga justerade slutkurser (inkl. utdelningar/splittar) och volym och
     sparar dem i kurser.csv (Datum, Ticker, Stang, Volym). Körs om: fyller på nya dagar.
     Saknar en ticker volym (äldre kurser.csv) hämtas hela historiken om.
  4. Hämtar antal aktier (nu) till aktier.csv för en grov uppskattning av börsvärde.
     Antalet är dagens, inte historiskt: emissioner i efterhand gör börsvärdet
     för tidigare år för högt eller lågt. Används bara för grov storleksindelning.

Kör:  python kurser.py              # alla bolag
      python kurser.py --max 20     # prova med 20 bolag först

Krav: pip install yfinance pandas

VIKTIGT (survivorship bias): Yahoo saknar ofta kurser för avnoterade och
uppköpta bolag. Insiderköp i bolag som sedan försvann, ofta efter stora
kursrörelser, hamnar då utanför backtestet. Kontrollera ticker_map.csv för
bolag med Status "ej_hittad" och hantera dem innan du litar på resultatet.
"""
import argparse
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

MAPP = Path(__file__).resolve().parent
KOPLISTA = MAPP / "insiderkop.csv"
TICKERMAP = MAPP / "ticker_map.csv"
KURSER = MAPP / "kurser.csv"
AKTIER = MAPP / "aktier.csv"
STARTDATUM = "2015-07-01"  # ett år före tidigaste insiderdata (2016-07, MAR) så att 52-veckors- och 200-dagarsmått finns från start
INDEX = ["^OMX", "^OMXSPI", "^OMXSSCPI"]  # OMXS30, OMX Stockholm PI och Small Cap PI (^OMXSSCPI är overifierad hos Yahoo; saknas den hoppas den över)

MAP_KOLUMNER = ["Bolag_id", "Bolag", "ISIN", "Ticker", "Status"]


# ---------------------------------------------------------------- bolagslista
def bolagslista() -> pd.DataFrame:
    """Ett ISIN och ett visningsnamn per Bolag_id (det vanligaste)."""
    k = pd.read_csv(KOPLISTA, keep_default_na=False, dtype=str)
    k = k[~k["Indirekt"].isin(["True", "true"])]
    def vanligast(x):
        x = x[x != ""]
        return x.value_counts().idxmax() if len(x) else ""
    return (
        k.groupby("Bolag_id")
        .agg(Bolag=("Emittent", vanligast), ISIN=("ISIN", vanligast))
        .reset_index()
    )


def las_tickermap() -> pd.DataFrame:
    if TICKERMAP.exists():
        return pd.read_csv(TICKERMAP, keep_default_na=False, dtype=str)
    return pd.DataFrame(columns=MAP_KOLUMNER)


# ---------------------------------------------------------------- ticker-sökning
def hitta_ticker(yf, isin: str, namn: str) -> str:
    """Söker Yahoo på ISIN och sedan på namn. Föredrar Stockholmsbörsen (.ST)."""
    for fraga in [isin, f"{namn}"]:
        if not fraga:
            continue
        try:
            traffar = yf.Search(fraga, max_results=8).quotes
        except Exception:
            traffar = []
        time.sleep(0.3)
        aktier = [t for t in traffar if t.get("quoteType") in ("EQUITY", None)]
        for t in aktier:
            sym = t.get("symbol", "")
            if sym.endswith(".ST"):
                return sym
        if fraga == isin and aktier:
            return aktier[0].get("symbol", "")
    return ""


def uppdatera_tickermap(yf, maxantal: int) -> pd.DataFrame:
    bolag = bolagslista()
    mapp = las_tickermap()
    kanda = set(mapp["Bolag_id"])
    nya = bolag[~bolag["Bolag_id"].isin(kanda)]
    if maxantal:
        nya = nya.head(maxantal)
    rader = []
    for i, r in enumerate(nya.itertuples(), 1):
        t = hitta_ticker(yf, r.ISIN, r.Bolag)
        rader.append({"Bolag_id": r.Bolag_id, "Bolag": r.Bolag, "ISIN": r.ISIN,
                      "Ticker": t, "Status": "ok" if t else "ej_hittad"})
        if i % 25 == 0:
            print(f"  ticker-sökning: {i}/{len(nya)}")
    if rader:
        mapp = pd.concat([mapp, pd.DataFrame(rader)], ignore_index=True)
        mapp.to_csv(TICKERMAP, index=False)
    okand = (mapp["Ticker"] == "").sum()
    print(f"Tickers: {len(mapp)} bolag, {okand} utan ticker (se ticker_map.csv)")
    return mapp


# ---------------------------------------------------------------- kurser
def hamta_kurser(yf, tickers: list) -> None:
    """Hämtar dagliga justerade slutkurser och volym. Fyller på: nya tickers får full
    historik, befintliga bara de senaste dagarna. Tickers som saknar volym i den
    sparade filen (skapad före volym lades till) hämtas om i sin helhet."""
    if KURSER.exists():
        gamla = pd.read_csv(KURSER, dtype={"Ticker": str})
        if "Volym" not in gamla.columns:
            gamla["Volym"] = float("nan")
        senaste = gamla.groupby("Ticker")["Datum"].max()
        # index har ingen volym; för övriga tickers betyder tom volym att den måste hämtas
        utan_volym = set(gamla.loc[gamla["Volym"].isna(), "Ticker"]) - {t for t in gamla["Ticker"] if t.startswith("^")}
        har_volym = set(gamla.loc[gamla["Volym"].notna(), "Ticker"])
        omhamta = utan_volym - har_volym
        # index sparades tidigare bara från 2023-12: hämta om hela historiken en gång
        tidigast = gamla.groupby("Ticker")["Datum"].min()
        grans = (pd.Timestamp(STARTDATUM) + pd.Timedelta(days=45)).strftime("%Y-%m-%d")
        omhamta |= {t for t in tidigast.index if t.startswith("^") and tidigast[t] > grans}
        # tickers som hämtades när historiken började 2019-12 hämtas om från den nya starten (2015-07)
        omhamta |= {t for t in tidigast.index if "2019-12-01" <= tidigast[t] <= "2019-12-10"}
    else:
        gamla = pd.DataFrame(columns=["Datum", "Ticker", "Stang", "Volym"])
        senaste = pd.Series(dtype=str)
        omhamta = set()

    slut = (date.today() + timedelta(days=1)).isoformat()
    nya_delar = []
    fellista = []
    for i in range(0, len(tickers), 40):
        grupp = tickers[i:i + 40]
        for t in grupp:
            start = STARTDATUM
            if t in senaste.index and t not in omhamta:
                start = (pd.Timestamp(senaste[t]) - pd.Timedelta(days=5)).date().isoformat()
            try:
                d = yf.download(t, start=start, end=slut, auto_adjust=True,
                                progress=False, threads=False)
            except Exception as e:
                print(f"  {t}: fel ({e.__class__.__name__})")
                fellista.append(f"{t}: fel {e.__class__.__name__}: {str(e)[:150]}")
                continue
            if d is None or d.empty:
                # reservväg: Ticker.history med hela perioden (ibland svarar download tomt men history inte)
                try:
                    h = yf.Ticker(t).history(start=start, end=slut, auto_adjust=True)
                    if h is not None and not h.empty:
                        h.index = h.index.tz_localize(None)
                        d = h
                        fellista.append(f"{t}: download tomt, men Ticker.history gav {len(h)} dagar")
                except Exception as e:
                    fellista.append(f"{t}: history fel {e.__class__.__name__}: {str(e)[:100]}")
            if d is None or d.empty:
                fellista.append(f"{t}: tomt svar från Yahoo (start {start})")
                continue
            close = d["Close"]
            if isinstance(close, pd.DataFrame):  # nyare yfinance ger en kolumn per ticker
                close = close.iloc[:, 0]
            volym = d["Volume"] if "Volume" in d else None
            if isinstance(volym, pd.DataFrame):
                volym = volym.iloc[:, 0]
            ut = pd.DataFrame({"Datum": close.index.strftime("%Y-%m-%d"), "Ticker": t,
                               "Stang": close.values,
                               "Volym": volym.values if volym is not None else float("nan")})
            nya_delar.append(ut.dropna(subset=["Stang"]))
            time.sleep(0.2)
        print(f"  kurser: {min(i + 40, len(tickers))}/{len(tickers)} tickers")
        # spara löpande så att en avbruten körning inte förlorar allt
        if nya_delar:
            samlat = pd.concat([gamla] + nya_delar, ignore_index=True)
            samlat = samlat.drop_duplicates(["Datum", "Ticker"], keep="last")
            samlat.sort_values(["Ticker", "Datum"]).to_csv(KURSER, index=False)
            gamla, nya_delar = samlat, []
    (KURSER.parent / "kurser_fel.txt").write_text("\n".join(fellista) + "\n")
    print(f"Kurser sparade i {KURSER.name}; {len(fellista)} tickers utan svar (se kurser_fel.txt)")


def hamta_aktier(yf, tickers: list, max_alder_dagar: int = 30) -> None:
    """Antal utestående aktier (dagens) per ticker till aktier.csv. Hämtas om var
    30:e dag. Misslyckas en ticker hoppas den över; filen är valfri i backtestet."""
    idag = date.today()
    gamla = pd.read_csv(AKTIER, dtype={"Ticker": str}) if AKTIER.exists() else pd.DataFrame(columns=["Ticker", "Aktier", "Hamtad"])
    fran = {r.Ticker: r for r in gamla.itertuples()}
    rader = []
    for t in tickers:
        if t.startswith("^") or t.endswith(".F"):   # index och euroaktier på Frankfurt
            continue
        g = fran.get(t)
        if g is not None and pd.notna(g.Aktier) and (idag - date.fromisoformat(str(g.Hamtad))).days < max_alder_dagar:
            rader.append({"Ticker": t, "Aktier": g.Aktier, "Hamtad": g.Hamtad})
            continue
        try:
            n = yf.Ticker(t).fast_info["shares"]
        except Exception:
            n = None
        time.sleep(0.2)
        rader.append({"Ticker": t, "Aktier": n if n else float("nan"), "Hamtad": idag.isoformat()})
    pd.DataFrame(rader).to_csv(AKTIER, index=False)
    ok = sum(1 for r in rader if pd.notna(r["Aktier"]))
    print(f"Antal aktier: {ok} av {len(rader)} tickers i {AKTIER.name}")


SOKNAMN = {"ADDV-B.ST": ["ADDvise", "SE0007464862"], "RESURS.ST": ["Resurs Holding", "SE0007665823"],
           "ARBO-B.ST": ["Arbona"], "TFBANK.ST": ["TF Bank", "SE0007331608"]}


def prova_tickers(tickers: list) -> None:
    """Diagnos: provar olika sätt att hämta kurser för tickers som Yahoo svarar tomt på. Skriver kurser_prov.txt."""
    import yfinance as yf, requests
    rader = [f"yfinance {getattr(yf, '__version__', '?')}"]
    def kor(namn, f):
        try:
            d = f()
            n = 0 if d is None else len(d)
            ex = "" if not n else f", första {d.index[0]}, sista {d.index[-1]}"
            rader.append(f"  {namn}: {n} rader{ex}")
        except Exception as e:
            rader.append(f"  {namn}: FEL {e.__class__.__name__}: {str(e)[:120]}")
    for t in tickers:
        rader.append(t)
        kor("download period=max", lambda: yf.download(t, period="max", auto_adjust=True, progress=False, threads=False))
        kor("download start=2021-01-01", lambda: yf.download(t, start="2021-01-01", auto_adjust=True, progress=False, threads=False))
        kor("download period=1mo", lambda: yf.download(t, period="1mo", auto_adjust=True, progress=False, threads=False))
        kor("Ticker.history period=max", lambda: yf.Ticker(t).history(period="max"))
        kor("Ticker.history period=1mo", lambda: yf.Ticker(t).history(period="1mo"))
        try:
            r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?range=1mo&interval=1d",
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            rader.append(f"  rå chart-API: HTTP {r.status_code}, {r.text[:260]!r}")
        except Exception as e:
            rader.append(f"  rå chart-API: FEL {e.__class__.__name__}")
        for q in SOKNAMN.get(t, []):
            try:
                r = requests.get("https://query2.finance.yahoo.com/v1/finance/search",
                                 params={"q": q, "quotesCount": 8, "newsCount": 0}, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
                tr = [(x.get("symbol"), x.get("shortname") or x.get("longname"), x.get("exchDisp")) for x in r.json().get("quotes", [])]
                rader.append(f"  sök '{q}': HTTP {r.status_code}: {tr}")
            except Exception as e:
                rader.append(f"  sök '{q}': FEL {e.__class__.__name__}: {str(e)[:80]}")
        try:
            rader.append(f"  fast_info: {dict(yf.Ticker(t).fast_info)!s:.200}")
        except Exception as e:
            rader.append(f"  fast_info: FEL {e.__class__.__name__}")
    (KURSER.parent / "kurser_prov.txt").write_text("\n".join(rader) + "\n")
    print("\n".join(rader))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prova", nargs="+", help="Diagnos för angivna tickers, skriver kurser_prov.txt")
    ap.add_argument("--max", type=int, default=0, help="Sök ticker för max N nya bolag (test)")
    args = ap.parse_args()
    if args.prova:
        prova_tickers(args.prova)
        return

    import yfinance as yf

    if not KOPLISTA.exists():
        print("insiderkop.csv saknas. Kör insider_bot.py först.")
        return
    mapp = uppdatera_tickermap(yf, args.max)
    tickers = sorted(set(mapp.loc[mapp["Ticker"] != "", "Ticker"]) | set(INDEX))
    hamta_kurser(yf, tickers)
    try:
        hamta_aktier(yf, tickers)
    except Exception as e:
        print(f"Antal aktier misslyckades ({e.__class__.__name__}), hoppar över.")


if __name__ == "__main__":
    main()
