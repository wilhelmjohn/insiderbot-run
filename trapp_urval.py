"""Veckovis urval av trappkandidater ur hela Stockholmsbörsen (körs i Actions, Yahoo nås därifrån).

1. Alla .ST-tickers i ticker_map.csv: 3 års kurser, grovfilter på omsättning.
2. Nyckeltal och rapportdatum för de som klarar filtret.
3. Samma poängsättning som sidan (trapp_bygg.analysera), därefter urval av de bästa.
4. Skriver trapp_urval.csv (alla poängsatta) och trapp_tickers.txt (urval + trapp_fasta.txt).
Fasta tickers (trapp_fasta.txt) är aktier du har positioner i eller vill följa; de tas aldrig bort.
Användning: python trapp_urval.py [antal=40]
"""
import sys, time
from pathlib import Path
import pandas as pd, yfinance as yf
import trapp_bygg as tb

HER = Path(__file__).resolve().parent
N_URVAL = int(sys.argv[1]) if len(sys.argv) > 1 else 40
MIN_OMS, MAX_BRANSCH = 15e6, 8
F = ["longName","sector","industry","marketCap","profitMargins","operatingMargins","debtToEquity","returnOnEquity",
     "trailingEps","forwardEps","earningsGrowth","revenueGrowth","bid","ask","bidSize","askSize","averageVolume","totalDebt","totalCash","ebitda","netIncomeToCommon","currency"]

def fasta():
    p = HER / "trapp_fasta.txt"
    return [t.strip() for t in p.read_text().split() if t.strip()] if p.exists() else []

def historik(tick, bit=40):
    """Hämtar dagskurser i omgångar. Returnerar ett långt DataFrame."""
    res = []
    for i in range(0, len(tick), bit):
        grp = tick[i:i + bit]
        try:
            d = yf.download(grp, period="3y", interval="1d", auto_adjust=False, group_by="ticker", threads=True, progress=False)
        except Exception as ex:
            print("FEL omgång", i, ex); continue
        for t in grp:
            try:
                x = d[t] if len(grp) > 1 else d
                x = x.dropna(subset=["Close"]).reset_index()
                if x.empty: continue
                x["Datum"] = pd.to_datetime(x["Date"]).dt.tz_localize(None).dt.normalize(); x["Ticker"] = t
                res.append(x[["Datum", "Ticker", "Open", "High", "Low", "Close", "Volume"]])
            except Exception:
                pass
        print(f"historik {min(i + bit, len(tick))}/{len(tick)}", flush=True)
    return pd.concat(res) if res else pd.DataFrame()

def info(t):
    tk = yf.Ticker(t); i = tk.info or {}
    r = {"Ticker": t, **{f: i.get(f) for f in F}}
    try:
        e = (tk.calendar or {}).get("Earnings Date") or []
        r["Nasta_rapport"] = min(e) if e else None
    except Exception:
        r["Nasta_rapport"] = None
    return r

def main():
    tm, bl, h, ik = tb.las_stod()
    allt = sorted({t for t in tm.index if isinstance(t, str) and t.endswith(".ST")} | set(fasta()))
    print(len(allt), "tickers i universumet")
    o = historik(allt)
    if o.empty: sys.exit("ingen kursdata")
    o = o.sort_values(["Ticker", "Datum"])
    keep = []
    for t, g in o.groupby("Ticker"):
        if len(g) < 500: continue
        if (g.Close * g.Volume).tail(60).median() >= MIN_OMS or t in fasta(): keep.append(t)
    print(len(keep), "klarar omsättningsfiltret")
    rows = []
    for k, t in enumerate(keep):
        try: rows.append(info(t))
        except Exception as ex: print(t, "info-fel", ex)
        if k % 25 == 0: print(f"info {k}/{len(keep)}", flush=True)
    inf = pd.DataFrame(rows)
    inf["Nasta_rapport"] = pd.to_datetime(inf["Nasta_rapport"], errors="coerce")
    inf = inf.dropna(subset=["marketCap"]).set_index("Ticker")
    out = tb.analysera(o[o.Ticker.isin(inf.index)], inf, tm, bl, h, ik)
    df = pd.DataFrame([dict(Ticker=z["t"], Namn=z["namn"], Sektor=z["sektor"], Fit=z["fit"], Rng=z["rng"], Dips=z["dips"], Aterh=z["aterh"],
                            Krasch=z["krasch"], Hard=sum(w["sev"] == "hard" for w in z["ned"]), Mc=z["mc"], Oms=z["oms"]) for z in out])
    df = df.sort_values("Fit", ascending=False)
    df.to_csv(HER / "trapp_urval.csv", index=False)
    val, br = [], {}
    for r in df.itertuples():
        if len(val) >= N_URVAL: break
        if r.Fit < 60 or r.Dips == 0 or r.Aterh / r.Dips < 0.5 or r.Krasch / r.Dips > 0.35: continue
        if br.get(r.Sektor, 0) >= MAX_BRANSCH: continue
        br[r.Sektor] = br.get(r.Sektor, 0) + 1; val.append(r.Ticker)
    fast = [t for t in fasta() if t not in val]
    (HER / "trapp_tickers.txt").write_text("\n".join(val + fast) + "\n")
    print(f"{len(df)} poängsatta, {len(val)} valda + {len(fast)} fasta -> trapp_tickers.txt")

if __name__ == "__main__":
    main()
