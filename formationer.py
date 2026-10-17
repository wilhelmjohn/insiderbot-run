"""Enkel formationsigenkänning på stängningskurser: dubbelbotten/-topp, omvänd huvud-skuldra/huvud-skuldra, rektangel.
Varje träff är en formation som redan gett en signal (stängning genom nivån). Reglerna är medvetet strikta."""
import numpy as np
import pandas as pd
from scipy.signal import find_peaks


def _pivots(y: np.ndarray, hi=None, lo=None):
    prom = 0.04 * float(np.median(y))
    t, _ = find_peaks(y, prominence=prom, distance=5)
    b, _ = find_peaks(-y, prominence=prom, distance=5)
    p = sorted([(int(i), float(y[i]), "T") for i in t] + [(int(i), float(y[i]), "B") for i in b])
    if hi is not None and lo is not None:  # nivåerna sätts av dagens högsta/lägsta kurs i närheten av pivoten
        n = len(y)
        p = [(i, float(np.nanmax(hi[max(0, i - 2):i + 3])) if k == "T" else float(np.nanmin(lo[max(0, i - 2):i + 3])), k) for i, v, k in p]
        p = [(i, (v if np.isfinite(v) else y[i]), k) for i, v, k in p]
    ut = []
    for q in p:  # slå ihop efterföljande av samma sort (behåll extremen)
        if ut and ut[-1][2] == q[2]:
            if (q[2] == "T" and q[1] > ut[-1][1]) or (q[2] == "B" and q[1] < ut[-1][1]):
                ut[-1] = q
        else:
            ut.append(q)
    return ut


def _brott(y, start, niva, upp, motsatt):
    """Första index efter start där kursen stänger genom nivån. Ogiltigt om motsatt nivå stängs genom först."""
    for i in range(start + 1, len(y)):
        if (upp and y[i] > niva) or (not upp and y[i] < niva):
            return i
        if (upp and y[i] < motsatt) or (not upp and y[i] > motsatt):
            return None
    return None


def hitta(px: pd.Series, max_dagar: int = 40, ohlc: pd.DataFrame = None) -> list:
    s = px.dropna()
    if len(s) < 80:
        return []
    y = s.values.astype(float)
    hi = lo = None
    if ohlc is not None and len(ohlc):
        o = ohlc.reindex(s.index)
        if o[["High", "Low"]].notna().all(axis=1).mean() > 0.8:
            hi, lo = o["High"].values.astype(float), o["Low"].values.astype(float)
    P = _pivots(y, hi, lo)
    ut = []

    def lagg(namn, typ, i, niva, hojd, start):
        if len(y) - 1 - i > max_dagar:
            return
        ut.append(dict(namn=namn, typ=typ, dag=s.index[i], dagar_sedan=len(y) - 1 - i, niva=niva,
                       utfall=(y[-1] / y[i] - 1) * 100 * (1 if typ == "köp" else -1), start=s.index[start],
                       mal=niva + hojd if typ == "köp" else niva - hojd))

    for k in range(len(P)):
        # dubbelbotten / dubbeltopp: tre pivoter A M B
        if k + 2 < len(P):
            (i1, v1, t1), (i2, v2, t2), (i3, v3, t3) = P[k:k + 3]
            if t1 == t3 and 15 <= i3 - i1 <= 100 and abs(v1 / v3 - 1) <= 0.03:
                if t1 == "B" and v2 / max(v1, v3) - 1 >= 0.06:
                    j = _brott(y, i3, v2, True, min(v1, v3) * 0.98)
                    if j: lagg("dubbelbotten", "köp", j, v2, v2 - (v1 + v3) / 2, i1)
                if t1 == "T" and min(v1, v3) / v2 - 1 >= 0.06:
                    j = _brott(y, i3, v2, False, max(v1, v3) * 1.02)
                    if j: lagg("dubbeltopp", "sälj", j, v2, (v1 + v3) / 2 - v2, i1)
        # huvud-skuldra: fem pivoter
        if k + 4 < len(P):
            a, b, c, d, e = P[k:k + 5]
            if e[0] - a[0] <= 160 and min(c[0] - a[0], e[0] - c[0]) >= 8:
                if a[2] == "B" and abs(a[1] / e[1] - 1) <= 0.05 and c[1] < min(a[1], e[1]) * 0.985 and abs(b[1] / d[1] - 1) <= 0.04:
                    n = max(b[1], d[1]); j = _brott(y, e[0], n, True, c[1] * 0.99)
                    if j: lagg("omvänd huvud-skuldra", "köp", j, n, n - c[1], a[0])
                if a[2] == "T" and abs(a[1] / e[1] - 1) <= 0.05 and c[1] > max(a[1], e[1]) * 1.015 and abs(b[1] / d[1] - 1) <= 0.04:
                    n = min(b[1], d[1]); j = _brott(y, e[0], n, False, c[1] * 1.01)
                    if j: lagg("huvud-skuldra", "sälj", j, n, c[1] - n, a[0])
        # rektangel: minst fyra pivoter, topparna lika höga, bottnarna lika höga
        for m in (4, 5, 6):
            if k + m <= len(P):
                W = P[k:k + m]
                tv = [q[1] for q in W if q[2] == "T"]; bv = [q[1] for q in W if q[2] == "B"]
                if len(tv) < 2 or len(bv) < 2 or W[-1][0] - W[0][0] < 25 or W[-1][0] - W[0][0] > 150:
                    continue
                if max(tv) / min(tv) - 1 <= 0.03 and max(bv) / min(bv) - 1 <= 0.03:
                    top, bot = float(max(tv)), float(min(bv))
                    if 0.05 <= top / bot - 1 <= 0.25:
                        i0 = W[-1][0]
                        j = _brott(y, i0, top, True, bot * 0.99)
                        if j: lagg("rektangel", "köp", j, top, top - bot, W[0][0])
                        else:
                            j = _brott(y, i0, bot, False, top * 1.01)
                            if j: lagg("rektangel", "sälj", j, bot, top - bot, W[0][0])
    # unika och senaste först
    seen, res = set(), []
    for f in sorted(ut, key=lambda f: f["dagar_sedan"]):
        nyckel = (f["namn"], f["dag"])
        if nyckel not in seen:
            seen.add(nyckel); res.append(f)
    return res
