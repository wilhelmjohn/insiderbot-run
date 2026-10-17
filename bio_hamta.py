"""Hämtar kurser och biotech-nyckeltal från Yahoo (körs i Actions).
  python bio_hamta.py        -> uppdaterar bio_ohlc.csv / bio_info.csv för tickers i bio_tickers.txt
  python bio_hamta.py ny     -> först ny genomgång av hela Stockholmsbörsen (ticker_map.csv) för att hitta biotech/läkemedel
Biotech = Yahoo-bransch Biotechnology eller Drug Manufacturers, omsättning minst 0,3 MSEK/dag."""
import sys, datetime as dt
from pathlib import Path
import pandas as pd, yfinance as yf

HER = Path(__file__).resolve().parent
BRANSCH = ("Biotechnology", "Drug Manufacturers")
F = ["longName","sector","industry","marketCap","enterpriseValue","totalCash","totalDebt","operatingCashflow","freeCashflow","totalRevenue",
     "netIncomeToCommon","profitMargins","sharesOutstanding","floatShares","heldPercentInsiders","heldPercentInstitutions",
     "averageVolume","bid","ask","currency","fiftyTwoWeekHigh","fiftyTwoWeekLow","targetMeanPrice","numberOfAnalystOpinions","recommendationKey"]

def historik(tick, bit=40):
    res = []
    for i in range(0, len(tick), bit):
        grp = tick[i:i + bit]
        try: d = yf.download(grp, period="3y", interval="1d", auto_adjust=False, group_by="ticker", threads=True, progress=False)
        except Exception as ex: print("FEL omgång", i, ex); continue
        for t in grp:
            try:
                x = (d[t] if len(grp) > 1 else d).dropna(subset=["Close"]).reset_index()
                if x.empty: continue
                x["Datum"] = pd.to_datetime(x["Date"]).dt.tz_localize(None).dt.normalize(); x["Ticker"] = t
                res.append(x[["Datum", "Ticker", "Open", "High", "Low", "Close", "Volume"]])
            except Exception: pass
        print(f"historik {min(i + bit, len(tick))}/{len(tick)}", flush=True)
    return pd.concat(res) if res else pd.DataFrame()

def info(t):
    tk = yf.Ticker(t); i = tk.info or {}
    r = {"Ticker": t, **{f: i.get(f) for f in F}}
    try:
        e = (tk.calendar or {}).get("Earnings Date") or []
        r["Nasta_rapport"] = min(e) if e else None
    except Exception: r["Nasta_rapport"] = None
    # utspädning: antal aktier nu mot för 1 och 3 år sedan
    try:
        s = tk.get_shares_full(start=(dt.date.today() - dt.timedelta(days=3 * 365 + 10)).isoformat())
        s = s.dropna(); s.index = pd.to_datetime(s.index).tz_localize(None)
        nu = float(s.iloc[-1])
        def pa(d):
            x = s[s.index <= pd.Timestamp.now() - pd.Timedelta(days=d)]
            return float(x.iloc[-1]) if len(x) else None
        a1, a3 = pa(365), pa(3 * 365)
        r["Utspadning_1ar"] = round((nu / a1 - 1) * 100, 1) if a1 else None
        r["Utspadning_3ar"] = round((nu / a3 - 1) * 100, 1) if a3 else None
    except Exception: r["Utspadning_1ar"] = r["Utspadning_3ar"] = None
    return r

def upptack():
    tm = pd.read_csv(HER / "ticker_map.csv").drop_duplicates("Ticker")
    allt = sorted({t for t in tm.Ticker if isinstance(t, str) and t.endswith(".ST")} | set((HER / "bio_fasta.txt").read_text().split() if (HER / "bio_fasta.txt").exists() else []))
    print(len(allt), "tickers"); o = historik(allt)
    keep = [t for t, g in o.groupby("Ticker") if len(g) >= 120 and (g.Close * g.Volume).tail(60).median() >= 3e5]
    print(len(keep), "klarar omsättningsfiltret"); bio = []
    for k, t in enumerate(keep):
        try:
            ind = (yf.Ticker(t).info or {}).get("industry") or ""
            if ind.startswith(BRANSCH): bio.append(t)
        except Exception as ex: print(t, "fel", ex)
        if k % 50 == 0: print(f"bransch {k}/{len(keep)}", flush=True)
    fasta = (HER / "bio_fasta.txt").read_text().split() if (HER / "bio_fasta.txt").exists() else []
    bio = sorted(set(bio) | set(fasta)); (HER / "bio_tickers.txt").write_text("\n".join(bio) + "\n"); print(len(bio), "biotech/läkemedel:", " ".join(bio))
    return bio, o

def main():
    if "ny" in sys.argv[1:] or not (HER / "bio_tickers.txt").exists():
        bio, o = upptack(); o = o[o.Ticker.isin(bio)]
    else:
        bio = (HER / "bio_tickers.txt").read_text().split(); o = historik(bio)
    if o.empty: sys.exit("ingen kursdata")
    o.to_csv(HER / "bio_ohlc.csv", index=False)
    rows = []
    for k, t in enumerate(bio):
        try: rows.append(info(t)); print(t, rows[-1].get("totalCash"), rows[-1].get("Utspadning_1ar"), flush=True)
        except Exception as ex: print(t, "info-fel", ex)
    pd.DataFrame(rows).to_csv(HER / "bio_info.csv", index=False)

if __name__ == "__main__":
    main()
