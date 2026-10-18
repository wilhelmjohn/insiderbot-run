"""Nyckeltal och höjda/sänkta riktkurser: spelar värdering (p/e, p/b), skuldsättning och lönsamhet roll?

Läser riktkurs_profil_handelser.csv, nyckel_ar.csv, nyckel_valuta.csv och rapporter.csv.
Varje händelse får det SENASTE årsbokslut som redan var offentligt dagen före händelsen (bokslutskommunikéns datum ur
rapporter.csv, annars bokslutsdatum + 75 dagar), så inga framtida siffror används. Bara bolag som redovisar i SEK får p/e och p/b.
Skriver riktkurs_nyckel.txt och riktkurs_nyckel_handelser.csv. t-värden klustrade per månad."""
import math, re, numpy as np, pandas as pd

E = pd.read_csv("riktkurs_profil_handelser.csv", parse_dates=["Datum"])
E["m"] = E.Datum.dt.to_period("M")
N = pd.read_csv("nyckel_ar.csv", parse_dates=["Period"])
V = pd.read_csv("nyckel_valuta.csv").set_index("Ticker").Valuta.to_dict()
W = N.pivot_table(index=["Ticker", "Period"], columns="Falt", values="Varde", aggfunc="first").reset_index()

# --- när blev årssiffrorna kända?
rp = pd.read_csv("rapporter.csv", usecols=["Ticker", "Publicerat", "Rubrik"], parse_dates=["Publicerat"])
rp = rp[rp.Rubrik.fillna("").str.contains(r"bokslut|årsredovisning|fjärde kvartalet|q4|helår|year-end|full[- ]year|oktober\s*[-–]\s*december", case=False, regex=True)]
rpd = {t: np.sort(g.Publicerat.dt.normalize().values) for t, g in rp.groupby("Ticker")}
def kand(t, per):
    a = rpd.get(t)
    if a is not None:
        c = a[(a >= np.datetime64(per)) & (a <= np.datetime64(per + pd.Timedelta(days=150)))]
        if len(c): return pd.Timestamp(c.min())
    return per + pd.Timedelta(days=75)
W["kand"] = [kand(t, p) for t, p in zip(W.Ticker, W.Period)]
W = W.sort_values(["Ticker", "Period"])
def kol(c): return W[c] if c in W else pd.Series(np.nan, index=W.index)
W["ni"] = kol("Net Income Common Stockholders").fillna(kol("Net Income"))
W["eps"] = kol("Diluted EPS").fillna(kol("Basic EPS"))
W["aktier"] = kol("Ordinary Shares Number").fillna(kol("Share Issued")).fillna(kol("Diluted Average Shares"))
W["eget"] = kol("Stockholders Equity").fillna(kol("Common Stock Equity"))
W["skuld"] = kol("Total Debt"); W["kassa"] = kol("Cash And Cash Equivalents")
W["oms"] = kol("Total Revenue"); W["rorelse"] = kol("Operating Income")
W["omsfg"] = W.groupby("Ticker").oms.shift(1)
W["tillv"] = (W.oms / W.omsfg - 1) * 100

rows = []
for r in E.itertuples():
    g = W[(W.Ticker == r.Ticker) & (W.kand < r.Datum) & (W.Period > r.Datum - pd.Timedelta(days=550))]
    if g.empty: rows.append({}); continue
    s = g.iloc[-1]; sek = V.get(r.Ticker) == "SEK"
    d = {"nyckel_per": s.Period, "valuta_ok": sek}
    if sek and pd.notna(s.eps):
        d["pe"] = r.kurs_fore / s.eps if s.eps > 0 else np.nan
        d["forlust"] = s.eps <= 0
    if sek and pd.notna(s.aktier) and pd.notna(s.eget) and s.eget > 0:
        d["pb"] = r.kurs_fore * s.aktier / s.eget
    if pd.notna(s.ni) and pd.notna(s.eget) and s.eget > 0: d["roe"] = s.ni / s.eget * 100
    if pd.notna(s.rorelse) and pd.notna(s.oms) and s.oms > 0: d["rorelsemarg"] = s.rorelse / s.oms * 100
    if pd.notna(s.skuld) and pd.notna(s.eget) and s.eget > 0: d["skuld_eget"] = s.skuld / s.eget * 100
    if pd.notna(s.skuld) and pd.notna(s.kassa) and pd.notna(s.eget) and s.eget > 0: d["nettoskuld_eget"] = (s.skuld - s.kassa) / s.eget * 100
    if pd.notna(s.tillv): d["tillvaxt"] = s.tillv
    if pd.notna(s.ni): d["ni_neg"] = s.ni < 0
    rows.append(d)
E = pd.concat([E.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
E.to_csv("riktkurs_nyckel_handelser.csv", index=False)

def kl(x, c):
    g = x.dropna(subset=[c]).groupby("m")[c].mean()
    if len(g) < 12: return None
    return g.mean(), g.mean() / (g.std() / math.sqrt(len(g))), int(x[c].notna().sum())
def fmt(x):
    o = []
    for c in ("r0", "x20", "x60"):
        k = kl(x, c); o.append(f"{c}: {k[0]:+5.2f} t={k[1]:+4.1f}" if k else f"{c}: (för få)")
    return " | ".join(o) + f" | n={len(x)}"
def diff(a, b, c):
    ga = a.dropna(subset=[c]).groupby("m")[c].mean(); gb = b.dropna(subset=[c]).groupby("m")[c].mean()
    d = (ga - gb).dropna()
    return (d.mean(), d.mean() / (d.std() / math.sqrt(len(d)))) if len(d) >= 12 else None

L = [f"NYCKELTAL OCH RIKTKURSER: {len(E)} bolag-dagar, med årssiffror {E.nyckel_per.notna().sum()} ({E.nyckel_per.notna().mean() * 100:.0f} %), SEK-redovisning {E.valuta_ok.fillna(False).sum()}",
     "Siffrorna är senast kända årsbokslut vid händelsen. Överavkastning mot index i procentenheter; r0 = reaktionsdagen, x20/x60 = 20/60 handelsdagar efter. t klustrat per månad."]
tester = []
def grupp(titel, d, kolumn, bins, labels, riktning):
    L.append(f"\n{titel}")
    x = d[d.pct > 2] if riktning == "höjd" else d[d.pct < -2]
    x = x.dropna(subset=[kolumn]).copy()
    x["g"] = pd.cut(x[kolumn], bins, labels=labels)
    gs = [(k, g) for k, g in x.groupby("g", observed=True) if len(g) >= 40]
    for k, g in gs: L.append(f"  {riktning:<5}{str(k):<24}{fmt(g)}")
    if len(gs) >= 2:
        for c in ("x20", "x60"):
            r = diff(gs[-1][1], gs[0][1], c)
            if r: L.append(f"       skillnad {gs[-1][0]} minus {gs[0][0]}, {c}: {r[0]:+5.2f} (t={r[1]:+4.1f})"); tester.append(abs(r[1]))

for rik in ("höjd", "sänkt"):
    grupp("1. P/E (senaste bokslut, kurs dagen före)", E, "pe", [0, 10, 15, 25, 1e5], ["p/e < 10", "10–15", "15–25", "p/e > 25"], rik)
    F = E[E.forlust == True]; X = F[F.pct > 2] if rik == "höjd" else F[F.pct < -2]
    if len(X) >= 40: L.append(f"  {rik:<5}{'förlust (eps ≤ 0)':<24}{fmt(X)}")
    grupp("2. P/B", E, "pb", [0, 1, 2, 4, 1e5], ["p/b < 1", "1–2", "2–4", "p/b > 4"], rik)
    grupp("3. Skuldsättning (nettoskuld/eget kapital)", E, "nettoskuld_eget", [-1e5, 0, 50, 150, 1e5], ["nettokassa", "0–50 %", "50–150 %", "> 150 %"], rik)
    grupp("4. Lönsamhet: avkastning på eget kapital", E, "roe", [-1e5, 0, 8, 15, 1e5], ["negativ", "0–8 %", "8–15 %", "> 15 %"], rik)
    grupp("5. Lönsamhet: rörelsemarginal", E, "rorelsemarg", [-1e5, 0, 5, 15, 1e5], ["förlust", "0–5 %", "5–15 %", "> 15 %"], rik)
    grupp("6. Omsättningstillväxt senaste året", E, "tillvaxt", [-1e5, -5, 5, 15, 1e5], ["krymper", "−5..+5 %", "5–15 %", "> 15 %"], rik)

# kombination: billig och lönsam, dyr och olönsam
E["kvalitet"] = np.where((E.roe > 8) & (E.nettoskuld_eget < 100), "lönsam & låg skuld", np.where((E.roe <= 0) | (E.nettoskuld_eget > 150), "olönsam eller högt skuldsatt", "mitt emellan"))
E.loc[E.roe.isna() | E.nettoskuld_eget.isna(), "kvalitet"] = np.nan
for rik, x in (("höjd", E[E.pct > 2]), ("sänkt", E[E.pct < -2])):
    L.append(f"\n7. Kvalitet (ROE > 8 % och nettoskuld < 100 % mot olönsam/skuldsatt), {rik}")
    gs = [(k, g) for k, g in x.dropna(subset=["kvalitet"]).groupby("kvalitet") if len(g) >= 40]
    for k, g in gs: L.append(f"  {rik:<5}{k:<32}{fmt(g)}")
    a = x[x.kvalitet == "lönsam & låg skuld"]; b = x[x.kvalitet == "olönsam eller högt skuldsatt"]
    for c in ("x20", "x60"):
        r = diff(a, b, c)
        if r: L.append(f"       skillnad lönsam minus olönsam, {c}: {r[0]:+5.2f} (t={r[1]:+4.1f})"); tester.append(abs(r[1]))

# regression på höjda
def ols(df, y, xs):
    d = df.dropna(subset=[y] + xs)
    X = np.column_stack([np.ones(len(d))] + [d[x].astype(float).values for x in xs]); Y = d[y].values
    b = np.linalg.lstsq(X, Y, rcond=None)[0]; u = Y - X @ b
    XtXi = np.linalg.inv(X.T @ X); meat = np.zeros((X.shape[1], X.shape[1]))
    for _, idx in d.groupby("m").indices.items():
        s = X[idx].T @ u[idx]; meat += np.outer(s, s)
    se = np.sqrt(np.diag(XtXi @ meat @ XtXi)); return b, se, len(d)
H = E[E.pct > 2].copy()
H["lpe"] = np.log(H.pe.clip(3, 100)); H["roe_s"] = H.roe.clip(-30, 40) / 10; H["nsk_s"] = H.nettoskuld_eget.clip(-100, 400) / 100
H["tv_s"] = H.tillvaxt.clip(-30, 60) / 10; H["pct_s"] = H.pct.clip(0, 60) / 10; H["topp_s"] = H.fran_topp.clip(-80, 0) / 10
H["logoms"] = np.log(H.oms.clip(lower=1e5)); H["ins"] = (H.ins90.fillna(0) >= 1e6).astype(float)
xs = ["lpe", "roe_s", "nsk_s", "tv_s", "pct_s", "topp_s", "logoms", "ins"]
nam = {"lpe": "log p/e (högre = dyrare)", "roe_s": "ROE per 10 procentenheter", "nsk_s": "nettoskuld/eget kapital per 100 %", "tv_s": "omsättningstillväxt per 10 %", "pct_s": "höjningens storlek per 10 %",
       "topp_s": "närhet till årshögsta per 10 %", "logoms": "log omsättning (storlek)", "ins": "insynsköp ≥ 1 Mkr (90 d)"}
for y in ("x20", "x60"):
    try:
        b, se, n = ols(H, y, xs)
        L.append(f"\n8. Regression på {y} för höjda (n={n})")
        for k, x in enumerate(xs, 1): L.append(f"  {nam[x]:<42}{b[k]:+6.2f}  t={b[k] / se[k]:+4.1f}")
    except Exception as e:
        L.append(f"\n8. Regression på {y}: kunde inte köras ({e})")
L.append(f"\nAntal gruppjämförelser: {len(tester)}; med |t| > 2: {sum(t > 2 for t in tester)}; väntat av slumpen vid 5 %: {0.05 * len(tester):.1f}.")
L.append("Förbehåll: årssiffror som Yahoo visar idag (kan vara omräknade), bokslut från 2021 och framåt så händelser före våren 2022 saknar siffror, bara bolag som finns kvar (överlevnadsfel), banker och fastighetsbolag har andra nyckeltal, p/e bygger på årsvinst som kan vara 15 månader gammal.")
open("riktkurs_nyckel.txt", "w", encoding="utf-8").write("\n".join(L))
print("\n".join(L))
