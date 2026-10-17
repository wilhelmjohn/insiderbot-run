#!/usr/bin/env python3
"""
Vändningssignal för knivar (insiderköp efter kursfall): notis när en bevakad kniv visar en vändning.

Signal (testad mot slumpade dagar i samma fall, 1 485 fall 2021-2026, håller före och efter 2024):
  volymspik: någon av de senaste 5 dagarna med minst 2x normal volym (20-dagars medel),
  RSI(14) korsar upp över 35 på signaldagen, och stängningen över gårdagens (uppdag).
Signalen räknas första gången den slår till 3-60 handelsdagar efter första publiceringen.

EDGE ÄR LITEN och kortsiktig: ca +1,0 % median och +1,3 % medel efter 10 dagar (efter kostnad),
mot -1,2 % för en slumpad dag i samma fall; 54 % upp, 10 % av fallen under -10 %. Efter 20 dagar
ca 0 %. Det är en uppstudsvarning, inte ett köpråd. Avnoterade bolag saknas i datan.

Körs i den dagliga körningen efter att kurserna uppdaterats. Dubbletter undviks via notis_skickade.txt
(rader "VANDNING|TICKER|signaldatum").

  python vandning.py --prov [--dagar 10]   # visa signaler de senaste N dagarna utan att skicka
"""
import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd

import backtest as bt
import bevakning
import notis

MAPP = Path(__file__).resolve().parent
MIN_DAG, MAX_DAG = 3, 60


def _rsi(c, n=14):
    x = c.diff()
    u = x.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    v = (-x.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + u / v)


def signaler(c: pd.Series, v: pd.Series, forst, tillbaka: int = 3) -> list:
    """Signaldagar (Timestamp, volymkvot) bland de senaste `tillbaka` handelsdagarna. Bara den första signalen
    i fönstret (MIN_DAG..MAX_DAG handelsdagar efter första publiceringen) räknas."""
    c = c.dropna()
    if len(c) < 60:
        return []
    v = v.reindex(c.index)
    if v.notna().mean() < 0.8:
        return []
    vr = v / v.rolling(20).mean()
    R = _rsi(c)
    sig = (vr.rolling(5).max() >= 2) & (R > 35) & (R.shift(1) <= 35) & (c > c.shift(1))
    i0 = c.index.searchsorted(pd.Timestamp(forst))
    ut = []
    lo, hi = i0 + MIN_DAG, min(i0 + MAX_DAG, len(c) - 1)
    traff = [i for i in range(lo, hi + 1) if bool(sig.iloc[i])]
    if traff and traff[0] >= len(c) - tillbaka:
        i = traff[0]
        ut.append((c.index[i], float(vr.rolling(5).max().iloc[i])))
    return ut


def meddelande(r, dag, vkvot, c) -> dict:
    namn = notis._namn(r)
    under = r.get("Under_52v_hogsta_%")
    rader = [f"VÄNDNINGSSIGNAL på kniv: volymspik ({vkvot:.1f}x normal), RSI över 35 och uppdag ({dag.date()}).",
             f"Kurs {c.iloc[-1]:.2f}. " + ("" if pd.isna(under) else f"Fallet vid köpet: {under:.0f} % från 52v-toppen. ") +
             f"Insiderköp: {int(r['Antal_personer'])} personer, {notis._tal(r['Belopp'])} kr, första publ {r['Forsta_publ']}.",
             str(r.get("Personer", ""))[:160],
             "Liten kortsiktig edge i backtestet: ca +1 % median (+1,3 % medel) efter 10 dagar, ca 0 % efter 20. "
             "54 % upp, 10 % av fallen under -10 %. Slumpdag i samma fall: -1,2 %. Ingen köpsignal, ingen testad edge över längre tid."]
    if str(r.get("Teknik", "")) not in ("", "nan"):
        rader.append("Teknik: " + str(r["Teknik"]).replace(" | ", "\n"))
    return {"title": f"Vändning: {namn}", "message": "\n".join(x for x in rader if x), "priority": 3,
            "tags": ["chart_with_upwards_trend", "insiderbot"], "bild": None}


def _favoriter() -> set:
    """Tickers i favoriter.txt (en per rad, med eller utan .ST, # = kommentar). Favoriter ger vändningsnotis även om
    de inte är knivar. Favoriterna i sidan sparas bara i webbläsaren, så listan förs in här för hand."""
    f = MAPP / "favoriter.txt"
    if not f.exists():
        return set()
    ut = set()
    for rad in f.read_text(encoding="utf-8").splitlines():
        t = rad.split("#")[0].strip().upper()
        if t:
            ut.add(t if t.endswith(".ST") else t + ".ST")
    return ut


def kor(prov=False, dagar=3, skickade=notis.SKICKADE):
    topic = os.environ.get("NTFY_TOPIC", "").strip() or None
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    b = bevakning.bevaka(MAPP / "insiderkop.csv", MAPP / "ticker_map.csv", MAPP / "kurser.csv", dagar=MAX_DAG + 30,
                         nyheter_fil=MAPP / "nyheter.csv")
    if b.empty:
        print("Inga fall."); return 0
    fav = _favoriter()
    kn = b[((b["Tillstand"].isin(["fall", "snabbt_fall"]) & (b["Kategori"] != "Regelträff")) | b["Ticker"].isin(fav))
           & b["Ticker"].str.endswith(".ST")]
    kurser = bt.las_kurser(MAPP / "kurser.csv"); kurser.index = pd.to_datetime(kurser.index)
    kr = pd.read_csv(MAPP / "kurser.csv", usecols=["Datum", "Ticker", "Volym"], parse_dates=["Datum"])
    vol = kr.pivot_table(index="Datum", columns="Ticker", values="Volym", aggfunc="last")
    kanda = set(Path(skickade).read_text().split()) if Path(skickade).exists() else set()
    att = []
    for _, r in kn.iterrows():
        t = r["Ticker"]
        if t not in kurser or t not in vol:
            continue
        for dag, vk in signaler(kurser[t], vol[t], r["Forsta_publ"], tillbaka=dagar):
            id_ = f"VANDNING|{t}|{dag.date()}"
            if id_ in kanda:
                continue
            att.append((id_, meddelande(r, dag, vk, kurser[t].dropna())))
    print(f"{len(kn)} knivar/favoriter, {len(att)} nya vändningssignaler")
    for _, m in att:
        print("-", m["title"], "|", m["message"].split("\n")[0])
    if prov or not att:
        return len(att)
    if not topic:
        print("NTFY_TOPIC saknas: inget skickades."); return len(att)
    notis.skicka([m for _, m in att], topic, server)
    with open(skickade, "a") as f:
        f.write("\n".join(i for i, _ in att) + "\n")
    return len(att)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prov", action="store_true")
    ap.add_argument("--dagar", type=int, default=3, help="signal inom så många senaste handelsdagar")
    a = ap.parse_args()
    kor(prov=a.prov, dagar=a.dagar)


if __name__ == "__main__":
    main()
