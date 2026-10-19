"""Divergensscreen: Bagge-inspirerad screen (RSI(9)-divergens + stochastic) för storbolag + Lyko.

Användning:
  python divergens_bygg.py <datamapp> <ut.html> [--live live.json] [--bekraftat]
<datamapp> = klon av wilhelmjohn/insiderbot (kurser.csv, insiderkop.csv, insiderhandel.csv, ticker_map.csv,
sektor_info.csv, blankning.csv, nyheter.csv, rapportkalender.csv, marknad.csv).
--live: JSON {"TICKER": pris, ...} med kurser under dagen. Då räknas dagens kurs in som om den vore
stängningskurs och statusen märks som preliminär. Med --bekraftat (körningen efter stängning) räknas
dagens sista kurs som stängning och statusen märks som bekräftad.
Skriver också <ut>.json med status per bolag (för notiser).

Regler (samma som i backtestet 2016-2026):
  Köpläge: kursen har gjort en lägre botten än en botten 5-40 dagar tidigare medan RSI(9) gjort en högre botten
  (tidigare botten RSI < 35), bottnen är bekräftad av två högre stängningar, aktien ligger >= 15 % under sitt
  rullande 1-årstopp och stochastic %K var under 25 någon av de senaste 4 dagarna. Köp på stängningen,
  stopp 1 % under bottnen (risk 1,5-8 %), mål 2R (sälj halva, flytta stopp till köpkurs) och 3R, max 10 dagar.
"""
import sys, os, json, math, re, datetime as dt
import numpy as np, pandas as pd

DATA, UT = sys.argv[1].rstrip("/") + "/", sys.argv[2]
LIVE = {}
if "--live" in sys.argv:
    LIVE = json.load(open(sys.argv[sys.argv.index("--live") + 1]))
RISK_KR = 1000
EXTRA = ["LYKO-A.ST"]
TOPP_N = 80

# ---------- kurser ----------
k = pd.read_csv(DATA + "kurser.csv", parse_dates=["Datum"])
k = k[k.Ticker.str.endswith(".ST")]
# Norge och Danmark (divergens_kurser.py i insiderbot-run): aktier, valutor och index i samma fil
NORD = pd.read_csv(DATA + "divergens_nordic.csv", parse_dates=["Datum"]) if os.path.exists(DATA + "divergens_nordic.csv") else k.iloc[:0]
NORD_INFO = pd.read_csv(DATA + "divergens_nordic_info.csv").set_index("Ticker") if os.path.exists(DATA + "divergens_nordic_info.csv") else pd.DataFrame(columns=["Namn", "Sektor", "Valuta"])
nord_px = NORD.pivot_table(index="Datum", columns="Ticker", values="Stang").sort_index()
NORD_AKTIER = [t for t in nord_px.columns if t.endswith((".OL", ".CO")) and t != "OBX.OL" and nord_px[t].last_valid_index() is not None
               and nord_px[t].last_valid_index() >= nord_px.index[-1] - pd.Timedelta(days=10)]
k = pd.concat([k, NORD[NORD.Ticker.isin(NORD_AKTIER)]], ignore_index=True)
LAND = lambda t: "NO" if t.endswith(".OL") else "DK" if t.endswith(".CO") else "SE"
VALUTA = {"SE": "SEK", "NO": "NOK", "DK": "DKK"}
def fx_sek(land):
    """Senaste växelkurs till SEK (1 NOK = x SEK). Används bara för antal aktier vid 1000 kr risk."""
    if land == "SE": return 1.0
    p = {"NO": "NOKSEK=X", "DK": "DKKSEK=X"}[land]
    return float(nord_px[p].dropna().iloc[-1]) if p in nord_px.columns and nord_px[p].notna().any() else {"NO": 0.95, "DK": 1.45}[land]
px = k.pivot_table(index="Datum", columns="Ticker", values="Stang").sort_index()
vo = k.pivot_table(index="Datum", columns="Ticker", values="Volym").sort_index().reindex_like(px)
px = px[(px.index >= "2015-06-01") & (px.notna().sum(axis=1) > 50)]
vo = vo.reindex(px.index)
px = px.ffill(limit=3)
senast_stang = px.index[-1]
prel = False
if LIVE:
    from zoneinfo import ZoneInfo
    idag = pd.Timestamp(dt.datetime.now(ZoneInfo("Europe/Stockholm")).date())
    rad = px.iloc[-1].copy()
    for t, p in LIVE.items():
        if t in rad.index and p: rad[t] = float(p)
    if idag > px.index[-1]:
        px.loc[idag] = rad; vo.loc[idag] = np.nan
    else:
        px.iloc[-1] = rad
    prel = "--bekraftat" not in sys.argv
dates = px.index
turn = (px * vo).rolling(60, min_periods=40).median().ffill()
rank = turn.iloc[-1][[t for t in px.columns if t.endswith(".ST")]].rank(ascending=False)
univ = list(rank[rank <= TOPP_N].index) + [t for t in EXTRA if t in px.columns] + NORD_AKTIER
univ = list(dict.fromkeys(univ))

# ---------- stöddata ----------
sek = pd.read_csv(DATA + "sektor_info.csv").set_index("Ticker")
NAMN_NORD = {"EQNR.OL": "Equinor", "DNB.OL": "DNB Bank", "NHY.OL": "Norsk Hydro", "MOWI.OL": "Mowi", "TEL.OL": "Telenor", "ORK.OL": "Orkla",
    "YAR.OL": "Yara", "AKRBP.OL": "Aker BP", "SALM.OL": "SalMar", "KOG.OL": "Kongsberg Gruppen", "STB.OL": "Storebrand",
    "GJF.OL": "Gjensidige", "SUBC.OL": "Subsea 7", "TOM.OL": "Tomra", "NOD.OL": "Nordic Semiconductor", "FRO.OL": "Frontline",
    "AKER.OL": "Aker", "VAR.OL": "Vår Energi", "NAS.OL": "Norwegian Air Shuttle", "AUTO.OL": "AutoStore", "BAKKA.OL": "Bakkafrost",
    "LSG.OL": "Lerøy Seafood", "SCATC.OL": "Scatec", "TGS.OL": "TGS", "HAFNI.OL": "Hafnia", "NOVO-B.CO": "Novo Nordisk",
    "DSV.CO": "DSV", "MAERSK-B.CO": "Mærsk", "NSIS-B.CO": "Novonesis", "VWS.CO": "Vestas", "ORSTED.CO": "Ørsted",
    "CARL-B.CO": "Carlsberg", "DANSKE.CO": "Danske Bank", "COLO-B.CO": "Coloplast", "GMAB.CO": "Genmab", "PNDORA.CO": "Pandora",
    "TRYG.CO": "Tryg", "DEMANT.CO": "Demant", "GN.CO": "GN Store Nord", "ROCK-B.CO": "Rockwool", "ISS.CO": "ISS",
    "JYSK.CO": "Jyske Bank", "NKT.CO": "NKT", "RBREW.CO": "Royal Unibrew", "NETC.CO": "Netcompany", "AMBU-B.CO": "Ambu",
    "BAVA.CO": "Bavarian Nordic", "FLS.CO": "FLSmidth", "ZEAL.CO": "Zealand Pharma"}
def namn(t):
    if t in NAMN_NORD: return NAMN_NORD[t]
    if LAND(t) != "SE":
        n = str(NORD_INFO.Namn.get(t, t))
        n = n.title() if n.isupper() else n
        return re.sub(r"\s+(Asa|ASA|A/S|As|AS|Ltd|Plc|PLC|Holdings?|Group)\b\.?", "", n).strip()
    return re.sub(r"\s*\(publ\.?\)|\s+AB\b|\s+SE\b", "", str(sek.longName.get(t, t))).strip()
def sektor(t):
    v = NORD_INFO.Sektor.get(t) if LAND(t) != "SE" else sek.sector.get(t)
    return v if isinstance(v, str) and v else None
tm = pd.read_csv(DATA + "ticker_map.csv")
isin2t = tm.dropna(subset=["ISIN"]).drop_duplicates("ISIN").set_index("ISIN").Ticker
bid2t = tm.set_index("Bolag_id").Ticker
kop = pd.read_csv(DATA + "insiderkop.csv", parse_dates=["Publiceringsdatum"])
kop = kop[(kop["Karaktär"] == "Förvärv") & (kop["Instrumenttyp"] == "Aktie") & (~kop["Intern_overforing"].fillna(False).astype(bool))
          & (kop["Valuta"] == "SEK")]
kop["Ticker"] = kop.Bolag_id.map(bid2t)
han = pd.read_csv(DATA + "insiderhandel.csv", parse_dates=["Publiceringsdatum"])
salj = han[(han["Karaktär"] == "Avyttring") & (han["Instrumenttyp"] == "Aktie") & (han["Valuta"] == "SEK")].copy()
salj["Ticker"] = salj.ISIN.map(isin2t)
salj["Belopp"] = salj.Volym * salj.Pris
nyh = pd.read_csv(DATA + "nyheter.csv", parse_dates=["Publicerat"])
nyh["Ticker"] = nyh.Bolag_id.map(bid2t)
rk = pd.read_csv(DATA + "rapportkalender.csv", parse_dates=["Datum"])
bl = pd.read_csv(DATA + "blankning.csv")
norm = lambda s: re.sub(r"[^a-z0-9]", "", re.sub(r"\(publ\.?\)|\bab\b|\baktiebolag(et)?\b|\bgroup\b|\bholding\b", "", str(s).lower()))
bl_map = {norm(r.Emittent): r for r in bl.itertuples()}
mk = pd.read_csv(DATA + "marknad.csv", parse_dates=["Datum"]).set_index("Datum").Index
mk = mk[~mk.index.duplicated()]
mk_topp = mk.rolling(250, min_periods=200).max()
mk_dd = float(mk.iloc[-1] / mk_topp.iloc[-1] - 1)
def idx_dd(sym):
    if sym not in nord_px.columns: return None
    s = nord_px[sym].dropna()
    if len(s) < 200: return None
    return float(s.iloc[-1] / s.rolling(250, min_periods=200).max().iloc[-1] - 1)
def snitt_dd(suffix):
    """Reserv när indexet saknar historik: likaviktat index av landets aktier i screenen."""
    cols = [t for t in NORD_AKTIER if t.endswith(suffix)]
    if not cols: return None
    r = nord_px[cols].ffill(limit=3).pct_change(fill_method=None).mean(axis=1).fillna(0)
    s = (1 + r).cumprod()
    return float(s.iloc[-1] / s.rolling(250, min_periods=200).max().iloc[-1] - 1)
def land_mk(kandidater, suffix, reservnamn):
    for sym, namn_ in kandidater:
        v = idx_dd(sym)
        if v is not None: return dict(namn=namn_, dd=v)
    return dict(namn=reservnamn, dd=snitt_dd(suffix))
MARKNAD = {"SE": dict(namn="OMXSPI", dd=mk_dd),
           "NO": land_mk([("^OSEBX", "OSEBX"), ("OSEBX.OL", "OSEBX"), ("^OSEAX", "OSEAX"), ("OBX.OL", "OBX")], ".OL", "Norska aktier, snitt"),
           "DK": land_mk([("^OMXC25", "OMXC25")], ".CO", "Danska aktier, snitt")}

# ---------- indikatorer ----------
def rsi(c, n=9):
    d = np.diff(c, prepend=np.nan)
    up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    ru = pd.Series(up).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().values
    rd = pd.Series(dn).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().values
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100 - 100 / (1 + ru / rd)


def ind(t):
    c = px[t].values.astype(float)
    s = pd.Series(c)
    lo, hi = s.rolling(14).min().values, s.rolling(14).max().values
    with np.errstate(divide="ignore", invalid="ignore"):
        K = 100 * (c - lo) / (hi - lo)
    D = pd.Series(K).rolling(3).mean().values
    m = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    hist = (m - m.ewm(span=9, adjust=False).mean()).values
    hi250 = s.rolling(250, min_periods=200).max().values
    return c, rsi(c), K, D, hist, hi250


def pivots(c):
    T = len(c)
    return [i for i in range(2, T - 2) if np.isfinite(c[i - 2:i + 3]).all()
            and c[i] < c[i - 1] and c[i] < c[i - 2] and c[i] < c[i + 1] and c[i] < c[i + 2]]


def s1_signals(c, r, K, hi250):
    out, piv = [], pivots(c)
    for b in piv:
        i = b + 2
        for p in piv:
            if 5 <= b - p <= 40 and c[b] < c[p] and r[b] > r[p] and r[p] < 35 and c[i] <= 0.85 * hi250[i] \
                    and np.nanmin(K[i - 3:i + 1]) < 25:
                e = c[i]; risk = (e - c[b] * 0.99) / e
                if np.isfinite(risk) and risk <= 0.08:
                    out.append(dict(i=i, b=b, p=p, e=e, risk=max(risk, 0.015)))
                break
    return out


def hist_utfall(c, sigs):
    """Hur tidigare signaler i aktien gick (mål 2R / stopp / tid, 10 dagar). Max en signal per 15 handelsdagar."""
    n = hit = stop = 0; senast = -999
    for s in sigs:
        if s["i"] - senast < 15: continue
        senast = s["i"]
        if s["i"] + 10 >= len(c) - 1: continue
        n += 1; e, R = s["e"], s["risk"]
        for d in range(1, 11):
            x = c[s["i"] + d]
            if not np.isfinite(x): continue
            if x <= e * (1 - R): stop += 1; break
            if x >= e * (1 + 2 * R): hit += 1; break
    return n, hit, stop


def stoch_text(K, D):
    k, d, kmin = K[-1], D[-1], np.nanmin(K[-4:])
    if k >= 80: return "Överköpt"
    if k < 25 and k <= d: return "Översåld, ej vänt"
    if k > d and kmin < 25: return "Vänt upp från översålt"
    if k < 25: return "Översåld, vänder"
    return "Neutral"


def macd_text(h):
    if not np.isfinite(h[-1]): return "–"
    if h[-1] < 0 and h[-1] > h[-2] > h[-3]: return "Vänder upp"
    if h[-1] > 0 and h[-2] <= 0: return "Köpsignal"
    if h[-1] > 0: return "Positiv"
    return "Fallande"


def roll(s):
    s = str(s)
    for pat, kort in ((r"Verkställande direktör|^VD", "VD"), (r"Vice VD|vice verkst", "Vice VD"), (r"finans|Ekonomichef|CFO", "CFO"),
                      (r"ordförande", "Ordförande"), (r"Styrelse", "Styrelse"), (r"ledande befattning", "Ledning"),
                      (r"Arbetstagar", "Arb.tagarrep."), (r"Annan medlem", "Ledning/kontroll")):
        if re.search(pat, s, re.I): return kort
    return s[:20]


def kr(x):
    return f"{x/1e6:.1f} MSEK".replace(".", ",") if abs(x) >= 1e6 else f"{x/1e3:.0f} tkr"


T = len(dates)
idag_ts = dates[-1]
# sektorernas fall (median) bland universum
dd_now = {}
for t in univ:
    c = px[t].values; h = pd.Series(c).rolling(250, min_periods=200).max().values[-1]
    dd_now[t] = c[-1] / h - 1 if np.isfinite(h) else np.nan
sek_dd = {}
for t in univ:
    s = sektor(t)
    if s is None: continue
    sek_dd.setdefault(s, []).append(dd_now[t])

rows = []
for t in univ:
    c, r, K, D, h, hi250 = ind(t)
    if not np.isfinite(c[-1]) or not np.isfinite(hi250[-1]): continue
    now = c[-1]; dd = now / hi250[-1] - 1
    top_i = len(c) - 250 + int(np.nanargmax(c[-250:]))
    sigs = s1_signals(c, r, K, hi250)
    status, niv, pair, note = None, None, None, ""
    last = sigs[-1] if sigs else None
    if last and last["i"] >= T - 8:
        e, R = last["e"], last["risk"]; stop = e * (1 - R)
        pair = (last["p"], last["b"])
        fx = fx_sek(LAND(t)); antal = int(RISK_KR // ((e - stop) * fx))
        niv = dict(signaldag=dates[last["i"]].strftime("%Y-%m-%d"), kop=e, stopp=stop, m2=e * (1 + 2 * R), m3=e * (1 + 3 * R),
                   risk=R, antal=antal, varde=antal * e * fx)
        if now <= stop: status, note = "Utgår", "Stoppen nådd"
        elif now >= e * (1 + 2 * R): status, note = "Utgår", "Målet 2R nått"
        elif K[-1] >= 80: status, note = "Utgår", "Stochastic överköpt, läget passerat"
        elif now <= e * (1 + R): status = "Köpläge"
        else: status, note = "Utgår", "Kursen har dragit iväg mer än 1R"
    if status is None and dd <= -0.15:
        m = len(c) - 10 + int(np.nanargmin(c[-10:]))
        piv = pivots(c[:m])
        prior = [p for p in piv if 5 <= m - p <= 40 and c[m] < c[p] and r[p] < 35]
        divp = [p for p in prior if r[m] > r[p]]
        if divp and now <= 1.04 * c[m] and np.nanmin(K[m - 3:]) < 30:
            p = divp[-1]; pair = (p, m)
            ups = sum(1 for x in c[m + 1:] if x > c[m])
            status = "Närmar sig"
            note = "Bottnen ej bekräftad, behöver två stängningar över bottnen" if ups < 2 else "Väntar på att stochastic varit översåld"
        elif now <= 1.06 * np.nanmin(c[-20:]) and (K[-1] < 25 or r[-1] < 35):
            status = "Bevaka"; note = "Översåld men ingen positiv divergens än"
            if prior: pair = (prior[-1], m)
    if status is None: continue
    # undvik
    nn = nyh[(nyh.Ticker == t) & (nyh.Publicerat >= idag_ts - pd.Timedelta(days=120))]
    tunga = nn[nn.Typ.isin(["nyemission", "vinstvarning"])]
    if status != "Utgår" and (dd <= -0.80 or len(tunga)):
        note = ("Fallit mer än 80 % från toppen" if dd <= -0.80 else f"{tunga.Typ.iloc[-1].capitalize()} {tunga.Publicerat.iloc[-1]:%-d %b}") \
            + ". Marknaden har oftast rätt i den typen av fall."
        status = "Undvik"
    # insyn 90 dagar
    k90 = kop[(kop.Ticker == t) & (kop.Publiceringsdatum >= idag_ts - pd.Timedelta(days=90))]
    s90 = salj[(salj.Ticker == t) & (salj.Publiceringsdatum >= idag_ts - pd.Timedelta(days=90))]
    aff = []
    for x in k90.itertuples():
        aff.append(dict(d=x.Publiceringsdatum.strftime("%Y-%m-%d"), p=x._3, roll=roll(x.Befattning), typ="Köp", belopp=float(x.Belopp), pris=float(x.Pris)))
    for x in s90.itertuples():
        aff.append(dict(d=x.Publiceringsdatum.strftime("%Y-%m-%d"), p=x._3, roll=roll(x.Befattning), typ="Sälj", belopp=float(x.Belopp), pris=float(x.Pris)))
    aff.sort(key=lambda a: a["d"], reverse=True)
    vdcfo = lambda s: s in ("VD", "CFO")
    ins = dict(kop_n=int(k90["Person i ledande ställning"].nunique()), kop_kr=float(k90.Belopp.sum()),
               salj_n=int(s90["Person i ledande ställning"].nunique()), salj_kr=float(s90.Belopp.sum()),
               vd=any(vdcfo(a["roll"]) for a in aff if a["typ"] == "Köp"), aff=aff[:12])
    b = bl_map.get(norm(sek.longName.get(t, ""))) if LAND(t) == "SE" else None
    blank = dict(niva=float(b.Niva), f20=float(b.Forandring_20d)) if b is not None else None
    rap = rk[(rk.Ticker == t) & (rk.Datum >= idag_ts.normalize()) & (rk.Datum <= idag_ts + pd.Timedelta(days=14))]
    rapport = rap.Datum.min().strftime("%Y-%m-%d") if len(rap) else None
    n30 = nyh[(nyh.Ticker == t) & (nyh.Publicerat >= idag_ts - pd.Timedelta(days=30))].sort_values("Publicerat", ascending=False)
    nyheter = [dict(d=x.Publicerat.strftime("%Y-%m-%d"), typ=x.Typ, rubrik=x.Rubrik) for x in n30.head(5).itertuples()]
    bolagsnyhet = bool(len(n30[n30.Typ.isin(["rapport", "vinstvarning", "nyemission", "ledning", "m_and_a"])]))
    s = sektor(t); sdd = float(np.nanmedian([dd_now[x] for x in univ if sektor(x) == s and x != t])) if s in sek_dd and len(sek_dd[s]) > 2 else None
    v = vo[t].values
    vol = float(np.nanmean(v[-10:]) / np.nanmean(v[-60:-10]) - 1) if np.isfinite(np.nanmean(v[-60:-10])) and np.nanmean(v[-60:-10]) > 0 else None
    hn, hhit, hstop = hist_utfall(c, sigs[:-1] if (last and last["i"] >= T - 8) else sigs)
    W = 90
    w0 = len(c) - W
    ch = dict(d=[x.strftime("%Y-%m-%d") for x in dates[w0:]], c=[round(float(x), 2) if np.isfinite(x) else None for x in c[w0:]],
              r=[round(float(x), 1) if np.isfinite(x) else None for x in r[w0:]],
              lows=[int(i - w0) for i in pair if i >= w0] if pair else [],
              ins=[dict(d=a["d"], typ=a["typ"]) for a in aff if a["d"] >= dates[w0].strftime("%Y-%m-%d")],
              rap=[x.strftime("%Y-%m-%d") for x in rk[(rk.Ticker == t) & (rk.Datum >= dates[w0])].Datum])
    lowinfo = None
    if pair:
        p, q = pair
        lowinfo = dict(d1=dates[p].strftime("%Y-%m-%d"), c1=float(c[p]), r1=float(r[p]), d2=dates[q].strftime("%Y-%m-%d"), c2=float(c[q]), r2=float(r[q]))
    flags = []
    if sdd is not None and sdd <= -0.15: flags.append(("Sektorfall", "pos"))
    if ins["kop_n"]: flags.append((f"Insynsköp {ins['kop_n']} pers · {kr(ins['kop_kr'])}", "pos"))
    if ins["salj_n"]: flags.append((f"Insynssälj {ins['salj_n']} pers · {kr(ins['salj_kr'])}", "neg"))
    if blank and blank["niva"] >= 3: flags.append((f"Blankad {blank['niva']:.1f} %".replace(".", ",") + (" ↑" if blank["f20"] > 0.2 else " ↓" if blank["f20"] < -0.2 else ""), "neu"))
    if rapport: flags.append((f"Rapport {pd.Timestamp(rapport):%-d/%-m}", "neg"))
    if bolagsnyhet: flags.append(("Bolagsnyhet 30 d", "neg"))
    # motivering
    mot = []
    if lowinfo and status in ("Köpläge", "Närmar sig"):
        f2 = lambda x: f"{x:.2f}".replace(".", ",")
        mot.append(f"Kursen gjorde lägre botten ({f2(lowinfo['c2'])} mot {f2(lowinfo['c1'])}) men RSI(9) högre ({lowinfo['r2']:.0f} mot {lowinfo['r1']:.0f}).")
    mot.append(f"Stochastic: {stoch_text(K, D).lower()}. MACD: {macd_text(h).lower()}.")
    if sdd is not None: mot.append("Hela sektorn har fallit (median " + f"{100*sdd:.0f} %)." if sdd <= -0.15 else f"Sektorn har inte fallit lika mycket (median {100*sdd:.0f} %), så fallet är mer bolagsspecifikt.")
    if note: mot.append(note + ".")
    land = LAND(t)
    if land != "SE": mot.append("Insyn, blankning och pressmeddelanden finns bara för svenska bolag, och reglerna är backtestade på Stockholmsbörsen.")
    rows.append(dict(t=t, namn=namn(t), sektor=s, land=land, valuta=VALUTA[land], mk_filter=(MARKNAD[land]["dd"] or 0) <= -0.10, status=status, kurs=float(now), dd=float(dd), topp=float(c[top_i]),
                     toppdag=dates[top_i].strftime("%Y-%m-%d"), rsi=float(r[-1]), K=float(K[-1]), D=float(D[-1]),
                     stoch=stoch_text(K, D), macd=macd_text(h), vol=vol, sektor_dd=sdd, ins=ins, blank=blank, rapport=rapport,
                     nyheter=nyheter, niv=niv, low=lowinfo, hist=dict(n=hn, mal=hhit, stopp=hstop), flags=flags,
                     mot=" ".join(mot), not_=note, live=t in LIVE, ch=ch))

ORD = {"Köpläge": 0, "Närmar sig": 1, "Bevaka": 2, "Undvik": 3, "Utgår": 4}
rows.sort(key=lambda x: (ORD[x["status"]], x["dd"]))
from zoneinfo import ZoneInfo
meta = dict(uppdaterad=dt.datetime.now(ZoneInfo("Europe/Stockholm")).strftime("%Y-%m-%d %H:%M"), kursdag=dates[-1].strftime("%Y-%m-%d"),
            senast_stang=senast_stang.strftime("%Y-%m-%d"), prel=prel, mk_dd=mk_dd, mk_filter=mk_dd <= -0.10,
            n_univ=len(univ), n_se=sum(1 for t in univ if LAND(t) == "SE"), n_no=sum(1 for t in univ if LAND(t) == "NO"),
            n_dk=sum(1 for t in univ if LAND(t) == "DK"), marknad=MARKNAD, risk_kr=RISK_KR)
data = dict(meta=meta, rows=rows)
json.dump({r["t"]: dict(status=r["status"], namn=r["namn"], kurs=r["kurs"], valuta=r["valuta"], niv=r["niv"]) for r in rows},
          open(UT.rsplit(".", 1)[0] + "_status.json", "w"), ensure_ascii=False, indent=1)
mall = open(__file__.rsplit("/", 1)[0] + "/divergens_mall.html", encoding="utf-8").read()
open(UT, "w", encoding="utf-8").write(mall.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, default=float)))
antal = ", ".join(f"{s}: {sum(1 for r in rows if r['status'] == s)}" for s in ORD)
print(f"{len(rows)} kandidater ({antal}), kursdag {meta['kursdag']}, OMXSPI {100*mk_dd:.1f} % från topp" + (" [PRELIMINÄRT]" if prel else ""))
