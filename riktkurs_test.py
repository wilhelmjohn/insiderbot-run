"""Empiriskt test: hur gick aktier efter att analytiker höjt/sänkt riktkursen (Placeras dagliga rekar, 2021-)?

Läser riktkurs_nyhet.csv (placera_arkiv.py), kurser.csv, marknad.csv och rapporter.csv (namn -> ticker).
Skriver riktkurs_test.txt (sammanfattning) och riktkurs_test_handelser.csv (en rad per bolag och dag).

Viktiga förbehåll (skrivs även i utdata): kurser.csv innehåller bara bolag som finns kvar idag (överlevnadsfel), dagens kurser är
stängningskurser (analysen publiceras före öppning, så reaktionen sker dag 0), händelser överlappar och bolag som tas upp ofta
är storbolag.
"""
import re, sys, math
import numpy as np, pandas as pd

H = [1, 5, 20, 60]           # handelsdagar efter publiceringsdagens stängning
MIN_PCT = 2.0                # ändring i procent som räknas som höjd/sänkt

ev = pd.read_csv("riktkurs_nyhet.csv")
ev = ev[(ev.Valuta == "SEK") & ev.Gammal.notna() & (ev.Typ == "rk")].copy()
ev["Datum"] = pd.to_datetime(ev.Datum)
ev["pct"] = (ev.Ny / ev.Gammal - 1) * 100
ev = ev[(ev.pct.abs() < 80) & (ev.Gammal > 0)]

# --- namn -> ticker
STOPP = {"ab", "publ", "group", "holding", "holdings", "ser", "class", "a", "b", "c", "sweden", "nordic", "international", "intl", "the", "and", "och", "asa", "oyj", "plc", "inc", "nv", "sa", "se", "fastigheter", "fastighets", "bolaget", "co"}
def nn(s):
    s = re.sub(r"\(publ\)", " ", str(s).lower())
    s = re.sub(r"[^a-zåäöéüø0-9 ]", " ", s)
    return " ".join(t for t in s.split() if t not in STOPP)
kur = pd.read_csv("kurser.csv", parse_dates=["Datum"])
try:
    kur = pd.concat([kur, pd.read_csv("kurser_extra.csv", parse_dates=["Datum"])]).drop_duplicates(["Datum", "Ticker"])
except Exception:
    pass
kur = kur[kur.Datum >= "2020-06-01"]
liq = (kur.assign(oms=kur.Stang * kur.Volym.fillna(0)).groupby("Ticker").oms.mean())
nam = pd.read_csv("rapporter.csv", usecols=["Ticker", "Bolag"]).drop_duplicates()
nam = nam[nam.Ticker.str.endswith(".ST")]
extra = {}
try:
    bv = pd.read_csv("bevakning.csv", usecols=["Bolag", "Ticker"], encoding="utf-8-sig").drop_duplicates()
    nam = pd.concat([nam, bv[bv.Ticker.str.endswith(".ST")]]).drop_duplicates()
except Exception:
    pass
try:
    alias = pd.read_csv("placera_alias.csv")   # Placera-namn,Ticker (handgjorda rättningar)
    extra = dict(zip(alias.iloc[:, 0].map(nn), alias.iloc[:, 1]))
except Exception:
    pass
full = {}
for t, b in zip(nam.Ticker, nam.Bolag):
    full.setdefault(nn(b), set()).add(t)
def valj(c):
    c = [t for t in c if t in liq.index]
    return max(c, key=lambda t: liq[t]) if c else None
def rensa_namn(n):
    n = re.sub(r",.*$", "", str(n))
    n = re.sub(r"\s+(i )?två steg.*$", "", n)
    n = re.sub(r"\s+för den långsiktiga.*$", "", n)
    return n.strip()
def ticker(namn):
    k = nn(rensa_namn(namn))
    if k in extra: return extra[k]
    if k in full: return valj(full[k])
    tk = k.split()
    if not tk: return None
    cand = set()
    for kk, ts in full.items():
        t2 = kk.split()
        if t2[:len(tk)] == tk: cand |= ts
    return valj(cand)
namn_lista = ev.Bolag.unique()
mapp = {n: ticker(n) for n in namn_lista}
ev["Ticker"] = ev.Bolag.map(mapp)
omatchade = ev[ev.Ticker.isna()].Bolag.value_counts()
ev = ev[ev.Ticker.notna()]

# --- en rad per bolag och dag (median av analytikernas ändring)
d = ev.groupby(["Ticker", "Datum"]).agg(Bolag=("Bolag", "first"), n=("pct", "size"), pct=("pct", "median"), ny=("Ny", "median"), gammal=("Gammal", "median")).reset_index()

# --- priser och index
px = kur.pivot(index="Datum", columns="Ticker", values="Stang").sort_index()
mk = pd.read_csv("marknad.csv", parse_dates=["Datum"]).set_index("Datum").Index.sort_index()
px = px.join(mk.rename("__IDX__"), how="left")
px["__IDX__"] = px["__IDX__"].ffill()
dates = px.index
rows = []
for r in d.itertuples():
    if r.Ticker not in px.columns: continue
    i = dates.searchsorted(r.Datum)           # första handelsdag >= publiceringsdagen
    if i >= len(dates) or i < 1: continue
    if dates[i] != r.Datum and (dates[i] - r.Datum).days > 4: continue
    s = px[r.Ticker]; ix = px["__IDX__"]
    p_fore, p0 = s.iloc[i - 1], s.iloc[i]
    if not (p_fore > 0 and p0 > 0): continue
    row = dict(Ticker=r.Ticker, Bolag=r.Bolag, Datum=r.Datum, n=r.n, pct=r.pct, uppsida=(r.ny / p_fore - 1) * 100, kurs_fore=p_fore, oms=liq.get(r.Ticker, np.nan))
    row["r0"] = (p0 / p_fore - 1) * 100 - (ix.iloc[i] / ix.iloc[i - 1] - 1) * 100       # reaktionsdagen, överavkastning
    for h in H:
        j = i + h
        if j < len(dates) and s.iloc[j] > 0 and ix.iloc[j] > 0:
            row[f"x{h}"] = ((s.iloc[j] / p0) - (ix.iloc[j] / ix.iloc[i])) * 100
    rows.append(row)
E = pd.DataFrame(rows)
E.to_csv("riktkurs_test_handelser.csv", index=False)

def sam(g, kol):
    x = g[kol].dropna()
    if len(x) < 5: return f"{'n=' + str(len(x)):>7}"
    se = x.std() / math.sqrt(len(x))
    return f"{x.mean():+6.2f} ({x.median():+5.2f}) t={x.mean() / se:+5.1f} n={len(x)}"
def tabell(titel, grupper, kolumner):
    out = [f"\n{titel}", "  (snitt, median i procentenheter över index; t = snitt/standardfel)"]
    for namn, g in grupper:
        out.append(f"  {str(namn):<22}" + " | ".join(f"{k}: {sam(g, k)}" for k in kolumner))
    return out

kol = ["r0"] + [f"x{h}" for h in H]
L = []
L.append(f"RIKTKURSTEST  {pd.Timestamp.today().date()}  händelser (bolag-dag): {len(E)}, bolag: {E.Ticker.nunique()}, period {E.Datum.min().date()}..{E.Datum.max().date()}")
L.append(f"Namn som inte kunde kopplas till ticker: {omatchade.sum()} analytikerrader, {len(omatchade)} namn. Vanligaste: " + ", ".join(f"{n} ({c})" for n, c in omatchade.head(15).items()))
L.append("r0 = reaktionen publiceringsdagen; x5/x20/x60 = överavkastning mot index från publiceringsdagens stängning (handelsdagar).")

E["grupp"] = pd.cut(E.pct, [-100, -MIN_PCT, MIN_PCT, 100], labels=["sänkt", "oförändrad", "höjd"])
L += tabell("A. Riktning på ändringen", [(k, g) for k, g in E.groupby("grupp", observed=True)], kol)
h = E[E.pct > MIN_PCT].copy()
if len(h) > 30:
    h["tercil"] = pd.qcut(h.pct, 3, labels=["liten höjning", "mellan", "stor höjning"])
    L += tabell("B. Storlek på höjningen", [(k, g) for k, g in h.groupby("tercil", observed=True)], kol)
s_ = E[E.pct < -MIN_PCT].copy()
if len(s_) > 30:
    s_["tercil"] = pd.qcut(s_.pct, 3, labels=["stor sänkning", "mellan", "liten sänkning"])
    L += tabell("B2. Storlek på sänkningen", [(k, g) for k, g in s_.groupby("tercil", observed=True)], kol)
u = E[E.uppsida.between(-60, 300)].copy()
u["kvint"] = pd.qcut(u.uppsida, 5, labels=["1 lägst uppsida", "2", "3", "4", "5 högst uppsida"])
L += tabell("C. Nivå: uppsida till nya riktkursen mot kursen dagen före", [(k, g) for k, g in u.groupby("kvint", observed=True)], ["x5", "x20", "x60"])
E["storlek"] = pd.qcut(E.oms.rank(method="first"), 3, labels=["minst omsatta", "mellan", "mest omsatta"])
E["ar"] = E.Datum.dt.year
hh = E[E.pct > MIN_PCT]; ss = E[E.pct < -MIN_PCT]
L += tabell("D1. Höjda riktkurser efter handelsvolym", [(k, g) for k, g in hh.groupby("storlek", observed=True)], kol)
L += tabell("D2. Sänkta riktkurser efter handelsvolym", [(k, g) for k, g in ss.groupby("storlek", observed=True)], kol)
L += tabell("E1. Höjda per år", [(k, g) for k, g in hh.groupby("ar")], ["r0", "x20", "x60"])
L += tabell("E2. Sänkta per år", [(k, g) for k, g in ss.groupby("ar")], ["r0", "x20", "x60"])
# handelsbara delen: höjda minus sänkta, x20 per år
L.append("\nF. Spread (höjd minus sänkt), x20 och x60 per år (snitt):")
for ar in sorted(E.ar.unique()):
    a, b = hh[hh.ar == ar], ss[ss.ar == ar]
    if len(a) > 5 and len(b) > 5:
        L.append(f"  {ar}: x20 {a.x20.mean() - b.x20.mean():+5.2f}  x60 {a.x60.mean() - b.x60.mean():+5.2f}  (n höjd {len(a)}, sänkt {len(b)})")
L.append("\nFörbehåll: bara bolag som finns kvar idag i kurser.csv (överlevnadsfel), analytiker följer ofta kursen (riktkursen sänks efter fall), överlappande händelser gör t-värdena för optimistiska, och 'uppsida' mäts mot dagen före publicering.")
open("riktkurs_test.txt", "w", encoding="utf-8").write("\n".join(L))
print("\n".join(L))
