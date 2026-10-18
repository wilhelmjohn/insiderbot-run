"""Delgruppsanalys: har höjda riktkurser olika effekt beroende på bolagets profil?

Läser riktkurs_test_handelser.csv (riktkurs_test.py) och lägger på profil: insynsköp före, storlek, kursutveckling före,
avstånd från 52-veckorshögsta, sektor, rapportnärhet, hur många analytiker, tidigare sänkning. Skriver riktkurs_profil.txt.
Alla t-värden är klustrade per månad (en observation per månad) för att motverka överlappande händelser."""
import math, numpy as np, pandas as pd

E = pd.read_csv("riktkurs_test_handelser.csv", parse_dates=["Datum"])
E["m"] = E.Datum.dt.to_period("M")
kur = pd.read_csv("kurser.csv", parse_dates=["Datum"])
try:
    kur = pd.concat([kur, pd.read_csv("kurser_extra.csv", parse_dates=["Datum"])]).drop_duplicates(["Datum", "Ticker"])
except Exception:
    pass
px = kur.pivot(index="Datum", columns="Ticker", values="Stang").sort_index()
dates = px.index
mom, dist = [], []
for r in E.itertuples():
    i = dates.searchsorted(r.Datum)
    if r.Ticker not in px.columns or i < 130:
        mom.append(np.nan); dist.append(np.nan); continue
    s = px[r.Ticker].iloc[:i]            # till och med dagen före
    p = s.iloc[-1]
    mom.append((p / s.iloc[-61] - 1) * 100 if s.iloc[-61] > 0 else np.nan)
    dist.append((p / s.iloc[-252:].max() - 1) * 100)
E["mom60"] = mom; E["fran_topp"] = dist

# --- insynsköp (förvärv) före händelsen
tm = pd.read_csv("ticker_map.csv")
tm = tm[tm.Status.isin(["ok", "manuell"])][["Bolag_id", "Ticker"]].drop_duplicates()
ik = pd.read_csv("insiderkop.csv", usecols=["Publiceringsdatum", "Bolag_id", "Belopp", "Intern_overforing", "Delad_affar"], parse_dates=["Publiceringsdatum"])
ik = ik[(ik.Intern_overforing != True) & (ik.Belopp > 0)].merge(tm, on="Bolag_id")
ik = ik.groupby(["Ticker", "Publiceringsdatum"]).Belopp.sum().reset_index()
har_map = set(tm.Ticker)
def ins(r, dagar):
    x = ik[(ik.Ticker == r.Ticker) & (ik.Publiceringsdatum < r.Datum) & (ik.Publiceringsdatum >= r.Datum - pd.Timedelta(days=dagar))]
    return x.Belopp.sum()
E["ins90"] = [ins(r, 90) if r.Ticker in har_map else np.nan for r in E.itertuples()]
E["ins365"] = [ins(r, 365) if r.Ticker in har_map else np.nan for r in E.itertuples()]

# --- sektor
try:
    si = pd.read_csv("sektor_info.csv")[["Ticker", "sector"]]
    E = E.merge(si, on="Ticker", how="left")
except Exception:
    E["sector"] = np.nan

# --- rapportnärhet
rp = pd.read_csv("rapporter.csv", usecols=["Ticker", "Publicerat"], parse_dates=["Publicerat"])
rp["d"] = rp.Publicerat.dt.normalize()
rd = rp.groupby("Ticker").d.apply(lambda x: np.sort(x.values))
def nar_rapport(r):
    a = rd.get(r.Ticker)
    if a is None or len(a) == 0: return np.nan
    d = (a - np.datetime64(r.Datum)).astype("timedelta64[D]").astype(int)
    return bool(((d >= -3) & (d <= 1)).any())
E["rapport"] = [nar_rapport(r) for r in E.itertuples()]

# --- tidigare sänkning (samma bolag, 90 dagar före)
sank = E[E.pct < -2][["Ticker", "Datum"]]
def tidig_sank(r):
    x = sank[(sank.Ticker == r.Ticker) & (sank.Datum < r.Datum) & (sank.Datum >= r.Datum - pd.Timedelta(days=90))]
    return len(x) > 0
E["efter_sankning"] = [tidig_sank(r) for r in E.itertuples()]
E.to_csv("riktkurs_profil_handelser.csv", index=False)

def kl(x, col):
    g = x.dropna(subset=[col]).groupby("m")[col].mean()
    if len(g) < 12: return None
    return g.mean(), g.mean() / (g.std() / math.sqrt(len(g))), len(g), int(x[col].notna().sum())
def fmt(x, cols=("r0", "x20", "x60")):
    out = []
    for c in cols:
        k = kl(x, c)
        out.append(f"{c}: {k[0]:+5.2f} t={k[1]:+4.1f} n={k[3]}" if k else f"{c}: (för få)")
    return " | ".join(out)
def diff(a, b, col):
    ga = a.dropna(subset=[col]).groupby("m")[col].mean(); gb = b.dropna(subset=[col]).groupby("m")[col].mean()
    d = (ga - gb).dropna()
    if len(d) < 12: return None
    return d.mean(), d.mean() / (d.std() / math.sqrt(len(d)))

H = E[E.pct > 2].copy()
L = [f"PROFILANALYS av höjda riktkurser (ändring över 2 %): {len(H)} bolag-dagar, {H.Ticker.nunique()} bolag, {H.Datum.min().date()}..{H.Datum.max().date()}",
     "Överavkastning mot index i procentenheter; r0 = reaktionsdagen, x20/x60 = 20/60 handelsdagar efter publiceringsdagens stängning. t klustrat per månad."]
tests = []     # (namn, diff-x60 t) för att räkna hur många grupper som testas
def grupp(titel, kol, bins=None, labels=None, data=H):
    d = data.copy()
    if bins is not None:
        d["g"] = pd.cut(d[kol], bins, labels=labels)
    else:
        d["g"] = d[kol]
    L.append(f"\n{titel}")
    gs = [(k, g) for k, g in d.groupby("g", observed=True) if len(g) >= 40]
    for k, g in gs:
        L.append(f"  {str(k):<26}{fmt(g)}")
    if len(gs) >= 2:
        a, b = gs[-1][1], gs[0][1]
        for c in ("x20", "x60"):
            r = diff(a, b, c)
            if r: L.append(f"  skillnad {gs[-1][0]} minus {gs[0][0]}, {c}: {r[0]:+5.2f} (t={r[1]:+4.1f})"); tests.append((titel, c, r[1]))

H["insyn"] = pd.cut(H.ins90, [-1, 49_999, 999_999, 1e13], labels=["inga insynsköp 90 d", "insynsköp < 1 Mkr 90 d", "insynsköp ≥ 1 Mkr 90 d"])
H["insyn365"] = pd.cut(H.ins365, [-1, 499_999, 1e13], labels=["inga senaste året", "insynsköp senaste året"])
grupp("1. Insynsköp senaste 90 dagarna före höjningen", "insyn")
grupp("2. Insynsköp senaste året", "insyn365")
H["storl"] = pd.qcut(H.oms.rank(method="first"), 3, labels=["små (lägst omsättning)", "mellan", "stora"])
grupp("3. Storlek (daglig omsättning)", "storl")
grupp("4. Kursutveckling 60 dagar före (momentum)", "mom60", bins=[-100, -15, -5, 5, 15, 1000], labels=["< −15 %", "−15..−5", "−5..+5", "+5..+15", "> +15 %"])
grupp("5. Avstånd från 52-veckorshögsta dagen före", "fran_topp", bins=[-100, -40, -25, -10, 1], labels=["> 40 % under", "25–40 % under", "10–25 % under", "< 10 % under"])
H["rapp"] = H.rapport.map({True: "inom −3..+1 dagar från rapport", False: "inte nära rapport"})
grupp("6. Rapportnärhet", "rapp")
H["flera"] = np.where(H.n >= 3, "3+ analytiker samma dag", "1–2 analytiker")
grupp("7. Antal analytiker som höjer samma dag", "flera")
H["stor"] = pd.cut(H.pct, [2, 8, 20, 80], labels=["liten höjning (2–8 %)", "medel (8–20 %)", "stor (> 20 %)"])
grupp("8. Höjningens storlek", "stor")
H["vand"] = H.efter_sankning.map({True: "sänkning senaste 90 d (vändning)", False: "ingen sänkning före"})
grupp("9. Föregående sänkning (samma bolag)", "vand")
H["sek"] = H.sector
big = H.sector.value_counts(); big = big[big >= 150].index
grupp("10. Sektor (Yahoo)", "sek", data=H[H.sector.isin(big)])
H["uppg"] = pd.cut(H.uppsida, [-100, 0, 15, 30, 300], labels=["kurs över ny riktkurs", "0–15 % uppsida", "15–30 %", "> 30 % uppsida"])
grupp("11. Uppsida till nya riktkursen", "uppg")
# kombination
H["kombo"] = pd.Series(np.where(H.ins90.fillna(0) >= 1_000_000, "insynsköp", "inget insynsköp"), index=H.index) + " & " + pd.Series(np.where(H.mom60 < -5, "nedgång före", "inte nedgång"), index=H.index)
grupp("12. Kombination: insynsköp och nedgång före (mom60 < −5 %)", "kombo")

# --- regression med månadsklustrade standardfel
def ols_cluster(df, y, xs):
    d = df.dropna(subset=[y] + xs)
    X = np.column_stack([np.ones(len(d))] + [d[x].astype(float).values for x in xs]); Y = d[y].values
    b = np.linalg.lstsq(X, Y, rcond=None)[0]; u = Y - X @ b
    XtXi = np.linalg.inv(X.T @ X); meat = np.zeros((X.shape[1], X.shape[1]))
    for _, idx in d.groupby("m").indices.items():
        s = X[idx].T @ u[idx]; meat += np.outer(s, s)
    V = XtXi @ meat @ XtXi
    return b, np.sqrt(np.diag(V)), len(d)
R = H.copy()
R["ins_dum"] = (R.ins90.fillna(0) >= 1_000_000).astype(float)
R["ins_ok"] = R.ins90.notna().astype(float)
R["logoms"] = np.log(R.oms.clip(lower=1e5))
R["rapp_dum"] = R.rapport.fillna(False).astype(float)
R["sank_dum"] = R.efter_sankning.astype(float)
R["mom_s"] = R.mom60.clip(-50, 100) / 10
R["topp_s"] = R.fran_topp.clip(-80, 0) / 10
R["pct_s"] = R.pct.clip(0, 60) / 10
R["flera_dum"] = (R.n >= 3).astype(float)
xs = ["ins_dum", "logoms", "mom_s", "topp_s", "rapp_dum", "sank_dum", "pct_s", "flera_dum"]
nam = {"ins_dum": "insynsköp ≥ 1 Mkr (90 d)", "logoms": "log omsättning (större bolag)", "mom_s": "kursutv. 60 d (per 10 %)", "topp_s": "avstånd från topp (per 10 %, närmare topp = högre)",
       "rapp_dum": "nära rapport", "sank_dum": "sänkning före", "pct_s": "höjningens storlek (per 10 %)", "flera_dum": "3+ analytiker"}
for y in ("x20", "x60"):
    b, se, n = ols_cluster(R, y, xs)
    L.append(f"\n13. Regression på {y} (alla profiler samtidigt, n={n}); koefficient i procentenheter, t klustrat per månad")
    for k, x in enumerate(xs, 1):
        L.append(f"  {nam[x]:<52}{b[k]:+6.2f}  t={b[k] / se[k]:+4.1f}")
tt = [abs(t) for _, _, t in tests]
L.append(f"\nAntal gruppjämförelser (skillnad mellan första och sista gruppen, x20/x60): {len(tt)}; med |t| > 2: {sum(t > 2 for t in tt)}. Vid 5 % nivå väntar man sig {0.05 * len(tt):.1f} falska träffar bara av en slump, så enstaka t över 2 betyder lite.")
L.append("Förbehåll: överlevnadsfel, bolagskopplingen till insynsköp bygger på ticker_map.csv (6 % av händelserna saknar koppling), sektor saknas för en tiondel, och grupperna överlappar varandra.")
open("riktkurs_profil.txt", "w", encoding="utf-8").write("\n".join(L))
print("\n".join(L))
