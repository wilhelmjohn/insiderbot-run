"""Marknadsmått för OMX Stockholm PI: långsamt trendmått (-10..+10) + separat vändningsvarning.

Trendmåttet är ett viktat genomsnitt (HMM-regim, MA50/MA200, 3 mån momentum).
Vändningsvarningen är snabb men oprövad: upp om index stigit >=5 % från 20-dagarslägsta
(lägsta inom 10 dagar), ner om index fallit >=5 % från 20-dagarshögsta (högsta inom 10 dagar).
Varningen räknas som bekräftad när trendmåttet passerar +3 / -3 inom 15 handelsdagar efter den.
Kör: python marknad.py  -> marknad.csv, marknad.png
"""
import numpy as np, pandas as pd
import hmm_modell as hm

BEKRAFTA_DAGAR = 15
INDEX = "^OMXSPI"; GRANS = 0.05; FONSTER = 20; NYLIG = 10

def _cl(x): return x.clip(-1, 1)

def berakna(px):
    r = (np.log(px).diff().dropna() * 100)
    m = hm.fit_hmm(r.values, 2)
    f, _ = hm.filtered_smoothed(r.values, m)
    p = pd.Series(f[:, int(np.argmax(m["sd"]))], index=r.index).reindex(px.index).fillna(0)
    ma50, ma200 = px.rolling(50).mean(), px.rolling(200).mean()
    tr = (_cl((px/ma50-1)/.05)*.2 + _cl((px/ma200-1)/.10)*.25 + _cl(px.pct_change(63)/.10)*.25
          + _cl((ma50/ma200-1)/.05)*.15 - p*.15) * 10
    lag = px.rolling(FONSTER).min(); hog = px.rolling(FONSTER).max()
    nylag = px.rolling(FONSTER).apply(lambda a: len(a)-1-np.argmin(a), raw=True) <= NYLIG
    nyhog = px.rolling(FONSTER).apply(lambda a: len(a)-1-np.argmax(a), raw=True) <= NYLIG
    upp = (px/lag-1 >= GRANS) & nylag
    ner = (px/hog-1 <= -GRANS) & nyhog
    v = pd.Series("", index=px.index, dtype=object)
    v[upp] = "uppvändning"; v[ner] = "nedvändning"
    # bekräftad: trendmåttet passerar +3/-3 inom BEKRAFTA_DAGAR efter att varningen utlösts
    upp_nyl = upp.astype(float).rolling(BEKRAFTA_DAGAR, min_periods=1).max() > 0
    ner_nyl = ner.astype(float).rolling(BEKRAFTA_DAGAR, min_periods=1).max() > 0
    bek = (upp_nyl & (tr > 3)) | (ner_nyl & (tr < -3))
    v[bek & upp_nyl & (tr > 3)] = "uppvändning"; v[bek & ner_nyl & (tr < -3)] = "nedvändning"
    return pd.DataFrame({"Index": px, "Trend": tr.round(2), "P_orolig": p.round(3),
                         "Vandning": v, "Bekraftad": bek}).dropna(subset=["Trend"]), m

def status(d):
    s = d.iloc[-1]; t = s.Trend
    niva = "bull" if t > 3 else "bear" if t < -3 else "sidlängs"
    txt = f"Trend {t:+.1f}/10 ({niva}), orolig regim {s.P_orolig:.0%}"
    if s.Vandning: txt += f"; varning: {s.Vandning}" + (" (bekräftad)" if s.Bekraftad else " (obekräftad)")
    return txt

def kor():
    k = pd.read_csv("kurser.csv", usecols=["Datum", "Ticker", "Stang"], parse_dates=["Datum"])
    px = k[k.Ticker == INDEX].set_index("Datum").Stang.sort_index()
    d, _ = berakna(px); d.to_csv("marknad.csv"); print(status(d)); return d

if __name__ == "__main__":
    kor()
