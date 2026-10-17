"""Hämtar 3 år OHLCV, nyckeltal och rapportdatum för valfria tickers (Yahoo, körs i Actions).
Användning: python volhamta.py T1.ST,T2.ST  -> volscreen_ohlc.csv, volscreen_info.csv"""
import sys, pandas as pd, yfinance as yf
tick = [t.strip() for t in sys.argv[1].split(",") if t.strip()]
SUF = ("_" + sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else ""
oh, inf, dv = [], [], []
F = ["longName","sector","industry","marketCap","profitMargins","operatingMargins","debtToEquity","returnOnEquity",
     "trailingEps","forwardEps","earningsGrowth","revenueGrowth","bid","ask","bidSize","askSize","averageVolume","totalDebt","totalCash","ebitda","netIncomeToCommon","currency"]
for t in tick:
    try:
        tk = yf.Ticker(t)
        h = tk.history(period="3y", interval="1d", auto_adjust=False)
        if not h.empty:
            h = h.reset_index(); h["Datum"] = pd.to_datetime(h["Date"]).dt.tz_localize(None).dt.normalize(); h["Ticker"] = t
            if "Dividends" in h.columns:  # avskiljandedagar: kursen faller med utdelningen och ska inte tolkas som ett fall
                for d_, v_ in h.loc[h["Dividends"] > 0, ["Datum", "Dividends"]].itertuples(index=False):
                    dv.append(dict(Ticker=t, ExDatum=str(d_.date()), Belopp=float(v_), Typ="historik"))
            oh.append(h[["Datum","Ticker","Open","High","Low","Close","Volume"]])
        i = tk.info or {}
        r = {"Ticker": t, **{f: i.get(f) for f in F}}
        try:
            c = tk.calendar or {}
            e = c.get("Earnings Date") or []
            r["Nasta_rapport"] = min(e) if e else None
        except Exception as ex:
            r["Nasta_rapport"] = None
        try:  # kommande avskiljandedag (Yahoo: kalendern eller info). Belopp lämnas tomt: lastDividendValue är ibland i annan valuta.
            nu_ = pd.Timestamp.now().normalize(); ex_ = None
            c2 = tk.calendar or {}
            e1 = c2.get("Ex-Dividend Date")
            if e1 is not None: ex_ = pd.Timestamp(e1)
            if (ex_ is None or ex_ < nu_) and i.get("exDividendDate"): ex_ = pd.Timestamp(int(i["exDividendDate"]), unit="s")
            if ex_ is not None and ex_ >= nu_:
                dv.append(dict(Ticker=t, ExDatum=str(ex_.date()), Belopp=None, Typ="kommande"))
        except Exception as ex:
            pass
        inf.append(r); print(t, len(h), r.get("marketCap"), r.get("Nasta_rapport"))
    except Exception as ex:
        print(t, "FEL", ex)
if oh: pd.concat(oh).to_csv(f"volscreen{SUF}_ohlc.csv", index=False)
if inf: pd.DataFrame(inf).to_csv(f"volscreen{SUF}_info.csv", index=False)
if inf: pd.DataFrame(dv, columns=["Ticker", "ExDatum", "Belopp", "Typ"]).drop_duplicates(["Ticker", "ExDatum", "Typ"]).to_csv(f"volscreen{SUF}_utdelning.csv", index=False)
