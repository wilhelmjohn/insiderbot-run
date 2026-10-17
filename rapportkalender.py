#!/usr/bin/env python3
"""
Rapportkalender från Yahoo Finance för bolagen i bevakningslistan: tidigare och kommande rapportdatum.
Fyller luckor där Nasdaq-nyheterna (rapporter.csv) saknar bolaget eller där nästa rapport inte går att uppskatta.

Sparar rapportkalender.csv (Ticker, Datum, Kalla, Framtid). Yahoos kommande datum är ofta uppskattade av Yahoo
men oftast rätt inom några dagar; kom ihåg att bolagens egna finansiella kalendrar är den säkra källan.
Körs i den dagliga körningen (GitHub Actions); kraschar aldrig. Hämtar bara bolagen i bevakning.csv.

  python rapportkalender.py
"""
import time
from pathlib import Path

import pandas as pd

MAPP = Path(__file__).resolve().parent
UT = MAPP / "rapportkalender.csv"


def hamta(tickers):
    import yfinance as yf
    rader = []
    for i, t in enumerate(tickers, 1):
        try:
            df = yf.Ticker(t).get_earnings_dates(limit=16)
        except Exception as e:                      # saknas ofta för småbolag
            print(f"  {t}: ingen kalender ({e.__class__.__name__})")
            continue
        if df is None or df.empty:
            continue
        idag = pd.Timestamp.today().normalize()
        for d in df.index:
            dd = pd.Timestamp(d).tz_localize(None).normalize()
            rader.append({"Ticker": t, "Datum": dd.date().isoformat(), "Kalla": "yahoo", "Framtid": bool(dd > idag)})
        if i % 20 == 0:
            print(f"  {i}/{len(tickers)} bolag", flush=True)
        time.sleep(0.3)
    return pd.DataFrame(rader)


def main():
    b = pd.read_csv(MAPP / "bevakning.csv")
    tick = sorted(t for t in b["Ticker"].dropna().unique() if str(t).endswith(".ST"))
    ny = hamta(tick)
    if ny.empty:
        print("Inga kalenderdata hämtades; behåller befintlig fil."); return
    if UT.exists():
        gammal = pd.read_csv(UT)
        gammal = gammal[~gammal["Ticker"].isin(ny["Ticker"].unique())]    # nytt ersätter per bolag
        ny = pd.concat([gammal, ny], ignore_index=True)
    ny.drop_duplicates(["Ticker", "Datum"]).sort_values(["Ticker", "Datum"]).to_csv(UT, index=False)
    print(f"Sparade {len(ny)} rader för {ny['Ticker'].nunique()} bolag i {UT.name}")


if __name__ == "__main__":
    main()
