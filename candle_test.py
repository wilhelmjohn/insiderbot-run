#!/usr/bin/env python3
"""
Candlestick-test på regelträffarna (köp >=50 tkr, över MA200, ej efter fall, omsättning >=2 MSEK/dag).

Hämtar OHLC (Yahoo, justerat) för händelsebolagen. För varje händelse ses ljusen FÖRE inträdesdagen
(point-in-time: ingen framtida data) och mönstren flaggas. Därefter jämförs överavkastningen
(AR_1m/3m/6m ur backtest_handelser.csv) för händelser med respektive mönster mot de utan.
Resultat: candle_resultat.csv, candle_handelser.csv.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

MAPP = Path(__file__).resolve().parent
UT = MAPP / "candle_resultat.csv"


def handelser():
    h = pd.read_csv(MAPP / "regelhandelser.csv", parse_dates=["Publicerat", "Inträde"])
    h = h[(h.Signal == "belopp_50k") & (h.Trend == "over_MA200") & (h.Villkor == "ej_fall")
          & (h.Likviditet == "over_2_MSEK")].drop_duplicates(["Ticker", "Publicerat"]).copy()
    return h.reset_index(drop=True)


def ohlc(tickers):
    ut = {}
    t = sorted(tickers)
    for i in range(0, len(t), 40):
        d = yf.download(t[i:i + 40], start="2020-06-01", auto_adjust=True, progress=False,
                        group_by="ticker", threads=False)
        for k in t[i:i + 40]:
            try:
                x = d[k][["Open", "High", "Low", "Close"]].dropna()
            except Exception:
                continue
            if len(x) > 250:
                x.columns = ["o", "h", "l", "c"]
                ut[k] = x
        print("OHLC", min(i + 40, len(t)), "/", len(t), "hämtade", len(ut), flush=True)
    return ut


def monster(x, i):
    """Flaggor för ljus i (positionell), bara data till och med i."""
    o, h, l, c = (x[k].values for k in "ohlc")
    rng = h[i] - l[i]
    if rng <= 0:
        return {}
    kropp = abs(c[i] - o[i])
    ovre = h[i] - max(o[i], c[i])
    nedre = min(o[i], c[i]) - l[i]
    pos = (c[i] - l[i]) / rng          # var stängningen ligger i dagens spann
    f = {}
    f["stangning_topp25"] = pos >= 0.75
    f["stangning_botten25"] = pos <= 0.25
    f["gron"] = c[i] > o[i]
    f["hammare"] = nedre >= 2 * kropp and ovre <= 0.3 * rng and kropp > 0 and pos >= 0.6
    f["fallande_stjarna"] = ovre >= 2 * kropp and nedre <= 0.3 * rng and kropp > 0 and pos <= 0.4
    f["doji"] = kropp <= 0.1 * rng
    if i >= 1:
        f["bullish_engulfing"] = (c[i - 1] < o[i - 1] and c[i] > o[i] and o[i] <= c[i - 1] and c[i] >= o[i - 1])
        f["bearish_engulfing"] = (c[i - 1] > o[i - 1] and c[i] < o[i] and o[i] >= c[i - 1] and c[i] <= o[i - 1])
        f["inside_day"] = h[i] < h[i - 1] and l[i] > l[i - 1]
    if i >= 2:
        r2 = h[i - 2] - l[i - 2]
        f["morning_star"] = (c[i - 2] < o[i - 2] and r2 > 0 and abs(c[i - 1] - o[i - 1]) <= 0.3 * (h[i - 1] - l[i - 1] + 1e-9)
                             and c[i] > o[i] and c[i] > (o[i - 2] + c[i - 2]) / 2)
        f["tre_garna"] = all(c[j] > o[j] for j in (i, i - 1, i - 2))
        f["tre_roda"] = all(c[j] < o[j] for j in (i, i - 1, i - 2))
    return f


def main():
    h = handelser()
    data = ohlc(h.Ticker.unique())
    rader = []
    for _, r in h.iterrows():
        x = data.get(r.Ticker)
        if x is None:
            continue
        i = x.index.searchsorted(r["Inträde"]) - 1   # sista ljuset före inträdesdagen
        if i < 3:
            continue
        f = monster(x, i)
        # samma mönster senaste 3 ljusen (vilket som helst)
        for k in ("hammare", "bullish_engulfing", "morning_star"):
            f[k + "_3d"] = any(monster(x, j).get(k, False) for j in (i, i - 1, i - 2))
        f["Ticker"], f["Publicerat"], f["Period"] = r.Ticker, r.Publicerat, r.Period
        for k in ("AR_1m", "AR_3m", "AR_6m", "AR_12m"):
            f[k] = r[k]
        rader.append(f)
    d = pd.DataFrame(rader)
    d.to_csv(MAPP / "candle_handelser.csv", index=False)
    print("händelser med OHLC:", len(d), "av", len(h))
    res = []
    flaggor = [c for c in d.columns if c not in ("Ticker", "Publicerat", "Period") and not c.startswith("AR_")]
    for per in ("alla", "före", "efter"):
        s = d if per == "alla" else d[d.Period == per]
        for k in flaggor:
            m = s[k].fillna(False).astype(bool)
            for hor in ("AR_1m", "AR_3m", "AR_6m"):
                a, b = s.loc[m, hor].dropna(), s.loc[~m, hor].dropna()
                if len(a) < 15:
                    continue
                res.append(dict(Period=per, Monster=k, Horisont=hor, N_med=len(a), N_utan=len(b),
                                Snitt_med=round(a.mean() * 100, 2), Snitt_utan=round(b.mean() * 100, 2),
                                Median_med=round(a.median() * 100, 2), Median_utan=round(b.median() * 100, 2),
                                Diff_snitt=round((a.mean() - b.mean()) * 100, 2),
                                Andel_pos_med=round((a > 0).mean() * 100, 1), Andel_pos_utan=round((b > 0).mean() * 100, 1),
                                t=round((a.mean() - b.mean()) / np.sqrt(a.var() / len(a) + b.var() / len(b)), 2)))
    r = pd.DataFrame(res)
    r.to_csv(UT, index=False)
    pd.set_option("display.width", 250, "display.max_rows", 500)
    print(r[r.Period == "alla"].sort_values(["Horisont", "t"]).to_string(index=False))


if __name__ == "__main__":
    main()
