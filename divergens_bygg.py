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
    _lf = sys.argv[sys.argv.index("--live") + 1]
    LIVE = json.load(open(_lf))
    _mf = os.path.join(os.path.dirname(os.path.abspath(_lf)), "divergens_live_meta.json")
    if os.path.exists(_mf):
        from zoneinfo import ZoneInfo as _Z
        if json.load(open(_mf)).get("hamtad", "")[:10] != dt.datetime.now(_Z("Europe/Stockholm")).strftime("%Y-%m-%d"):
            print("Varning: kursfilen under dagen är inte från i dag, används inte."); LIVE = {}
RISK_KR = 500
VALUTA_AVG = 0.005               # Nordnets valutaväxlingsavgift, dras vid köp och sälj i aktier som inte handlas i SEK
UPPF_START = "2026-10-12"         # uppföljning: alla signaler från och med den här dagen, oavsett status
MAX_ALDER, MAX_OVER_R = 2, 0.5   # köpläge bara signaldagen + 2 handelsdagar, och högst 0,5R över signalkursen (test 2016-2026)
EXTRA = ["LYKO-A.ST"]
TOPP_N = 80

# ---------- kurser ----------
k = pd.read_csv(DATA + "kurser.csv", parse_dates=["Datum"])
k = k[k.Ticker.str.endswith(".ST")]
# Norge och Danmark (divergens_kurser.py i insiderbot-run): aktier, valutor och index i samma fil
NORD = pd.read_csv(DATA + "divergens_nordic.csv", parse_dates=["Datum"]) if os.path.exists(DATA + "divergens_nordic.csv") else k.iloc[:0]
NORD_INFO = pd.read_csv(DATA + "divergens_nordic_info.csv").set_index("Ticker") if os.path.exists(DATA + "divergens_nordic_info.csv") else pd.DataFrame(columns=["Namn", "Sektor", "Valuta"])
nord_px = NORD.pivot_table(index="Datum", columns="Ticker", values="Stang").sort_index()
NORD_AKTIER = [t for t in nord_px.columns if t.endswith((".OL", ".CO")) and t not in ("OBX.OL", "OSEBX.OL") and nord_px[t].last_valid_index() is not None
               and nord_px[t].last_valid_index() >= nord_px.index[-1] - pd.Timedelta(days=10)]
# USA: Nasdaq-100 och Dow Jones (divergens_usa.py), samt S&P 500, Nasdaq-100- och Dow-index och USDSEK
USA = pd.read_csv(DATA + "divergens_usa.csv", parse_dates=["Datum"]) if os.path.exists(DATA + "divergens_usa.csv") else k.iloc[:0]
USA_INFO = pd.read_csv(DATA + "divergens_usa_info.csv").set_index("Ticker") if os.path.exists(DATA + "divergens_usa_info.csv") else pd.DataFrame(columns=["Namn", "Sektor", "Bors", "Index"])
usa_px = USA.pivot_table(index="Datum", columns="Ticker", values="Stang").sort_index() if len(USA) else pd.DataFrame()
USA_AKTIER = []   # amerikanska aktier borttagna ur screenen 2026-10-22 (bara Stockholm, Oslo och Köpenhamn)
k = pd.concat([k, NORD[NORD.Ticker.isin(NORD_AKTIER)], USA[USA.Ticker.isin(USA_AKTIER)]], ignore_index=True)
LAND = lambda t: "NO" if t.endswith(".OL") else "DK" if t.endswith(".CO") else "SE" if t.endswith(".ST") else "US"
VALUTA = {"SE": "SEK", "NO": "NOK", "DK": "DKK", "US": "USD"}
def bors(t):
    l_ = LAND(t)
    if l_ != "US": return {"SE": "Stockholm", "NO": "Oslo", "DK": "Köpenhamn"}[l_]
    b_ = USA_INFO.Bors.get(t) if "Bors" in USA_INFO.columns else None
    return b_ if isinstance(b_, str) and b_ in ("Nasdaq", "NYSE") else "Nasdaq"
def fx_sek(land):
    """Senaste växelkurs till SEK (1 NOK = x SEK). Används bara för antal aktier vid RISK_KR i risk."""
    if land == "SE": return 1.0
    if land == "US":
        return float(usa_px["USDSEK=X"].dropna().iloc[-1]) if "USDSEK=X" in usa_px.columns else 10.5
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
    k_idag = k[k.Datum == px.index[-1]].set_index("Ticker").Stang
    har_idag = idag <= px.index[-1]   # officiella stängningar för i dag finns redan för vissa aktier
    for t, p in LIVE.items():
        if t in rad.index and p and not (har_idag and pd.notna(k_idag.get(t))): rad[t] = float(p)
    if idag > px.index[-1]:
        px.loc[idag] = rad; vo.loc[idag] = np.nan
    else:
        px.iloc[-1] = rad
    prel = "--bekraftat" not in sys.argv
dates = px.index
turn = (px * vo).rolling(60, min_periods=40).median().ffill()
rank = turn.iloc[-1][[t for t in px.columns if t.endswith(".ST")]].rank(ascending=False)
univ = list(rank[rank <= TOPP_N].index) + [t for t in EXTRA if t in px.columns] + NORD_AKTIER + USA_AKTIER
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
    if LAND(t) == "US":
        n = str(USA_INFO.Namn.get(t, t))
        return re.sub(r",?\s+(Inc|Corp|Corporation|Co|Ltd|Plc|PLC|N\.V|Holdings?|Incorporated|Company)\b\.?", "", n).strip()
    if LAND(t) != "SE":
        n = str(NORD_INFO.Namn.get(t, t))
        n = n.title() if n.isupper() else n
        return re.sub(r"\s+(Asa|ASA|A/S|As|AS|Ltd|Plc|PLC|Holdings?|Group)\b\.?", "", n).strip()
    return re.sub(r"\s*\(publ\.?\)|\s+AB\b|\s+SE\b", "", str(sek.longName.get(t, t))).strip()
def sektor(t):
    v = USA_INFO.Sektor.get(t) if LAND(t) == "US" else NORD_INFO.Sektor.get(t) if LAND(t) != "SE" else sek.sector.get(t)
    return v if isinstance(v, str) and v else None
try:
    NORDNET = pd.read_csv(DATA + "nordnet_lankar.csv").set_index("Ticker").Url.to_dict()
except FileNotFoundError:
    NORDNET = {}
# bara aktier som går att handla via Nordnet (finns i Nordnets aktielista)
if len(NORDNET) > 100:
    utan = [t for t in univ if t not in NORDNET]
    if utan: print("Tas bort, saknas på Nordnet:", ", ".join(utan))
    univ = [t for t in univ if t in NORDNET]
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
# fler rapportdatum: yfinance för hela universumet (divergens_rapportdatum.csv) och manuella (rapport_manuell.csv)
for _f in ("divergens_rapportdatum.csv", "rapport_manuell.csv"):
    if os.path.exists(DATA + _f):
        rk = pd.concat([rk, pd.read_csv(DATA + _f, parse_dates=["Datum"])[["Ticker", "Datum"]]], ignore_index=True)
rk = rk.drop_duplicates(["Ticker", "Datum"])
RAPPORT_DAGAR = 14   # kalenderdagar framåt (ca 10 handelsdagar = affärens längd)
bl = pd.read_csv(DATA + "blankning.csv")
norm = lambda s: re.sub(r"[^a-z0-9]", "", re.sub(r"\(publ\.?\)|\bab\b|\baktiebolag(et)?\b|\bgroup\b|\bholding\b", "", str(s).lower()))
bl_map = {norm(r.Emittent): r for r in bl.itertuples()}
idag_ts0 = px.index[-1]
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

# Bredd: andel aktier som stänger under sitt 50-dagarssnitt (Sverige: alla Stockholmsaktier i kurser.csv; Norge/Danmark: aktierna i screenen)
BREDD_GRANS, RS_GRANS, MK_GRANS = 0.70, -0.06, -0.10
def bredd(cols_):
    x = px[cols_]; ma = x.rolling(50, min_periods=40).mean()
    ok = x.iloc[-1].notna() & ma.iloc[-1].notna()
    return float((x.iloc[-1][ok] < ma.iloc[-1][ok]).mean()) if ok.sum() >= 10 else None
for l_, cols_ in (("SE", [c_ for c_ in px.columns if c_.endswith(".ST")]), ("NO", [c_ for c_ in NORD_AKTIER if c_.endswith(".OL")]),
                  ("DK", [c_ for c_ in NORD_AKTIER if c_.endswith(".CO")])):
    MARKNAD[l_]["bredd"] = bredd(cols_)
    MARKNAD[l_]["filter"] = bool((MARKNAD[l_]["dd"] is not None and MARKNAD[l_]["dd"] <= MK_GRANS)
                                 or (MARKNAD[l_]["bredd"] is not None and MARKNAD[l_]["bredd"] > BREDD_GRANS))
# Index per land på screenens datum (för relativ styrka): OMXSPI, OMXC25, Norge likaviktat
def _idx_serie(l_):
    if l_ == "SE":
        s_ = mk.reindex(px.index).ffill(); sym = "^OMXSPI"
    elif l_ == "US" and "^GSPC" in usa_px.columns:
        s_ = usa_px["^GSPC"].reindex(px.index).ffill(); sym = "^GSPC"
    elif l_ == "DK" and "^OMXC25" in nord_px.columns:
        s_ = nord_px["^OMXC25"].reindex(px.index).ffill(); sym = "^OMXC25"
    else:
        cols_ = [c_ for c_ in NORD_AKTIER if c_.endswith(".OL" if l_ == "NO" else ".CO")]
        return (1 + px[cols_].pct_change(fill_method=None).mean(axis=1).fillna(0)).cumprod().values
    s_ = s_.copy()
    if LIVE.get(sym) and abs(float(LIVE[sym]) / s_.iloc[-2] - 1) < 0.08: s_.iloc[-1] = float(LIVE[sym])   # skydd mot annan indexskala
    return s_.values
INDEX = {l_: _idx_serie(l_) for l_ in ("SE", "NO", "DK")}

# Riktkursändringar (Placera, via riktkurs_nyhet.csv) för svenska bolag, som kontext
try:
    rkn = pd.read_csv(DATA + "riktkurs_nyhet.csv", parse_dates=["Datum"])
    rkn = rkn[(rkn.Typ == "rk") & (rkn.Datum >= idag_ts0 - pd.Timedelta(days=60)) & rkn.Gammal.notna() & (rkn.Valuta == "SEK")]
except Exception:
    rkn = pd.DataFrame(columns=["Datum", "Firma", "Bolag", "Ny", "Gammal"])
_n2t = {}
for x_ in sek.itertuples():
    _n2t.setdefault(norm(x_.longName), x_.Index)
def _match(b_):
    nb = norm(b_)
    if nb in _n2t: return _n2t[nb]
    c_ = [t_ for n_, t_ in _n2t.items() if nb and (n_.startswith(nb) or nb.startswith(n_)) and min(len(n_), len(nb)) >= 4]
    return c_[0] if len(c_) == 1 else None
rkn = rkn.assign(Ticker=rkn.Bolag.map(_match)) if len(rkn) else rkn.assign(Ticker=None)

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

UPPF = []


def _uppf_signaler(t, c, sigs):
    """Följ varje signal sedan UPPF_START med screenens regel: köp på signaldagens stängning med RISK_KR i risk,
    halva vid 2R och stoppen till köpkursen, resten vid 3R, på stoppen eller efter 10 handelsdagar (stängningskurser).
    Index räknas med samma insats under samma dagar. En ny signal i samma aktie räknas först när förra affären är avslutad."""
    upptagen, upptagen_rap = -1, -1   # rapportsignaler köps aldrig och spärrar bara andra rapportsignaler
    I = INDEX[LAND(t)]
    avg = VALUTA_AVG if LAND(t) != "SE" else 0.0
    for s in sigs:
        if dates[s["i"]] < pd.Timestamp(UPPF_START) or s["i"] <= upptagen: continue
        i, e = s["i"], s["e"]; Rk = e * s["risk"]; stop = e - Rk
        _d0 = dates[i].normalize()
        rap = rk[(rk.Ticker == t) & (rk.Datum >= _d0) & (rk.Datum <= _d0 + pd.Timedelta(days=RAPPORT_DAGAR))].Datum.min()
        rap = None if pd.isna(rap) else rap.strftime("%Y-%m-%d")   # rapportregeln: aldrig Köpläge, räknas separat
        if rap and i <= upptagen_rap: continue
        kvar, res, half, slut, hand = 1.0, 0.0, False, None, []
        avg_r = avg * e / Rk                                  # växlingsavgift vid köp, i R
        for d in range(1, 11):
            if i + d >= len(c): break
            x = c[i + d]
            if not np.isfinite(x): continue
            ds = dates[i + d].strftime("%Y-%m-%d")
            if not half and x >= e + 2 * Rk:
                res += 0.5 * 2; avg_r += 0.5 * avg * (e + 2 * Rk) / Rk; kvar = 0.5; half = True; stop = e; hand.append([d, ds, "halva såld vid 2R", float(e + 2 * Rk)])
            if half and x >= e + 3 * Rk:
                res += kvar * 3; avg_r += kvar * avg * (e + 3 * Rk) / Rk; kvar = 0; slut = i + d; hand.append([d, ds, "resten såld vid 3R", float(e + 3 * Rk)]); break
            if x <= stop:
                res += kvar * (x - e) / Rk; avg_r += kvar * avg * x / Rk; kvar = 0; slut = i + d
                hand.append([d, ds, "resten stoppad på köpkursen" if half else "stoppad, allt sålt", float(x)]); break
            if d == 10:
                res += kvar * (x - e) / Rk; avg_r += kvar * avg * x / Rk; kvar = 0; slut = i + d; hand.append([d, ds, ("resten" if half else "allt") + " sålt efter tio dagar", float(x)])
        j = slut if slut is not None else len(c) - 1
        if rap: upptagen_rap = j if slut is not None else len(c)
        else: upptagen = j if slut is not None else len(c)
        if kvar > 0: res += kvar * (c[j] - e) / Rk; avg_r += kvar * avg * c[j] / Rk   # öppen: avgiften vid en försäljning nu
        res -= avg_r
        insats = RISK_KR * e / Rk
        idx_kr = insats * (I[j] / I[i] - 1) if np.isfinite(I[i]) and np.isfinite(I[j]) and I[i] > 0 else None
        UPPF.append(dict(t=t, namn=namn(t), land=LAND(t), valuta=VALUTA[LAND(t)], datum=dates[i].strftime("%Y-%m-%d"), kurs=float(e),
                         stopp=float(e - Rk), m2=float(e + 2 * Rk), m3=float(e + 3 * Rk), halv=half, nu=float(c[j]), r=float(res), kr=round(RISK_KR * res), idx_kr=None if idx_kr is None else round(idx_kr),
                         insats=round(insats), oppen=slut is None, rapport=rap, dagar=int(j - i), handelser=hand,
                         ser=dict(i=int(i - max(0, i - 5)), d=[dates[q].strftime("%Y-%m-%d") for q in range(max(0, i - 5), min(len(c), i + 26))],
                                  c=[round(float(c[q]), 4) if np.isfinite(c[q]) else None for q in range(max(0, i - 5), min(len(c), i + 26))])))


rows = []
for t in univ:
    c, r, K, D, h, hi250 = ind(t)
    if not np.isfinite(c[-1]) or not np.isfinite(hi250[-1]): continue
    now = c[-1]; dd = now / hi250[-1] - 1
    top_i = len(c) - 250 + int(np.nanargmax(c[-250:]))
    sigs = s1_signals(c, r, K, hi250)
    _uppf_signaler(t, c, sigs)
    status, niv, pair, note, imorgon = None, None, None, "", None
    last = sigs[-1] if sigs else None
    if last and last["i"] >= T - 8:
        e, R = last["e"], last["risk"]; stop = e * (1 - R)
        pair = (last["p"], last["b"])
        fx = fx_sek(LAND(t))
        alder = T - 1 - last["i"]                       # handelsdagar sedan signalen
        Rk = e * R                                       # R i kronor från signalkursen
        # nivåerna räknas från dagens kurs (det pris du kan köpa till), stoppen ligger kvar 1 % under bottnen
        kop_nu = now if now > stop else e
        Rn = max(kop_nu - stop, kop_nu * 0.015)
        antal = int(RISK_KR // (Rn * fx))
        niv = dict(signaldag=dates[last["i"]].strftime("%Y-%m-%d"), signalkurs=e, alder=alder, kop=kop_nu, stopp=stop,
                   m2=kop_nu + 2 * Rn, m3=kop_nu + 3 * Rn, risk=Rn / kop_nu, antal=antal, varde=antal * kop_nu * fx)
        if now <= stop: status, note = "Utgår", "Stoppen nådd"
        elif now >= e + 2 * Rk: status, note = "Utgår", "Målet 2R nått"
        elif K[-1] >= 80: status, note = "Utgår", "Stochastic överköpt, läget passerat"
        elif alder > MAX_ALDER: status, note = "Utgår", f"Signalen är {alder} handelsdagar gammal. Köpläge gäller bara signaldagen och två dagar till"
        elif now > e + MAX_OVER_R * Rk: status, note = "Utgår", f"Kursen ligger {(now - e) / Rk:.1f}R över signalkursen. För lite uppsida kvar mot risken".replace(".", ",", 1)
        else: status = "Köpläge"
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
            # en av två stängningar över bottnen klar: blir köpläge i morgon om nästa stängning också ligger över bottnen
            if m == len(c) - 2 and now > c[m] and np.nanmin(K[-3:]) < 25 and (now - c[m] * 0.99) / now <= 0.08:
                stopp_ = c[m] * 0.99; fx = fx_sek(LAND(t))
                imorgon = dict(botten=float(c[m]), bottendag=dates[m].strftime("%Y-%m-%d"), stopp=float(stopp_),
                               antal=int(RISK_KR // (max(now - stopp_, now * 0.015) * fx)))
                note = f"En av två stängningar över bottnen klar. Blir köpläge i morgon om kursen stänger över {c[m]:.2f}".replace(".", ",")
        elif now <= 1.06 * np.nanmin(c[-20:]) and (K[-1] < 25 or r[-1] < 35):
            status = "Bevaka"; note = "Översåld men ingen positiv divergens än"
            if prior: pair = (prior[-1], m)
    if status is None: continue
    # relativ styrka mot index sedan första bottnen; i lugnt marknadsläge krävs att aktien inte gått klart sämre än index
    land = LAND(t); rs = None
    if pair:
        I = INDEX[land]; p0 = pair[0]
        if np.isfinite(I[p0]) and np.isfinite(I[-1]) and I[p0] > 0:
            rs = float((now / c[p0] - 1) - (I[-1] / I[p0] - 1))
    svag_rs = rs is not None and rs < RS_GRANS and not MARKNAD[land]["filter"]
    rap_nara = rk[(rk.Ticker == t) & (rk.Datum >= idag_ts.normalize()) & (rk.Datum <= idag_ts + pd.Timedelta(days=RAPPORT_DAGAR))].Datum.min()
    if status == "Köpläge" and pd.notna(rap_nara):
        status = "Avvakta"
        note = (f"Rapport {rap_nara:%-d/%-m} under affärens tid. Stoppen skyddar inte mot ett kursras på rapportdagen, "
                "och i testet var utfallen dubbelt så spridda. Köp efter rapporten om signalen står kvar")
    if status == "Köpläge" and svag_rs:
        status = "Avvakta"
        note = (f"Aktien har gått {abs(100*rs):.0f} procentenheter sämre än index sedan första bottnen medan börsen inte är bredt svag. "
                "I testet gav det läget förlust i snitt")
    if imorgon and pd.notna(rap_nara):
        imorgon = None
        note = f"Kan bli signal i morgon, men rapport {rap_nara:%-d/%-m} ligger under affärens tid (blir Avvakta)"
    if imorgon and svag_rs:
        imorgon = None
        note = f"En av två stängningar över bottnen klar, men aktien har gått {abs(100*rs):.0f} procentenheter sämre än index sedan första bottnen (blir Avvakta)"
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
    rap = rk[(rk.Ticker == t) & (rk.Datum >= idag_ts.normalize()) & (rk.Datum <= idag_ts + pd.Timedelta(days=RAPPORT_DAGAR))]
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
    lowinfo, vb = None, None
    if pair:
        p, q = pair
        with np.errstate(all="ignore"):
            v1, v2 = np.nanmean(v[p - 1:p + 2]), np.nanmean(v[q - 1:q + 2])
            vv = np.nanmean(v[q + 1:]) / np.nanmean(v[max(0, q - 20):q]) if q < len(v) - 1 else np.nan
        vb = dict(botten=float(v2 / v1) if v1 > 0 and np.isfinite(v2) else None, vand=float(vv) if np.isfinite(vv) else None)
        lowinfo = dict(d1=dates[p].strftime("%Y-%m-%d"), c1=float(c[p]), r1=float(r[p]), d2=dates[q].strftime("%Y-%m-%d"), c2=float(c[q]), r2=float(r[q]))
    flags = []
    if sdd is not None and sdd <= -0.15: flags.append(("Sektorfall", "pos"))
    if ins["kop_n"]: flags.append((f"Insynsköp {ins['kop_n']} pers · {kr(ins['kop_kr'])}", "pos"))
    if ins["salj_n"]: flags.append((f"Insynssälj {ins['salj_n']} pers · {kr(ins['salj_kr'])}", "neg"))
    if blank and blank["niva"] >= 3: flags.append((f"Blankad {blank['niva']:.1f} %".replace(".", ",") + (" ↑" if blank["f20"] > 0.2 else " ↓" if blank["f20"] < -0.2 else ""), "neu"))
    if rapport: flags.append((f"Rapport {pd.Timestamp(rapport):%-d/%-m}", "neg"))
    if rs is not None and rs < RS_GRANS: flags.append((f"Svagare än index {100*rs:.0f} p.e.".replace("-", "−"), "neg"))
    elif rs is not None and rs >= -0.01: flags.append(("Starkare än index", "pos"))
    rr = rkn[rkn.Ticker == t] if land == "SE" else rkn.iloc[:0]
    riktk = [dict(d=x.Datum.strftime("%Y-%m-%d"), firma=x.Firma, ny=float(x.Ny), gammal=float(x.Gammal)) for x in rr.sort_values("Datum", ascending=False).head(6).itertuples()]
    n_sank, n_hoj = int((rr.Ny < rr.Gammal).sum()), int((rr.Ny > rr.Gammal).sum())
    if n_sank or n_hoj: flags.append((f"Riktkurs 60 d: {n_hoj} höjd, {n_sank} sänkt", "neu"))
    if bolagsnyhet: flags.append(("Bolagsnyhet 30 d", "neg"))
    # motivering
    mot = []
    if lowinfo and status in ("Köpläge", "Närmar sig"):
        f2 = lambda x: f"{x:.2f}".replace(".", ",")
        mot.append(f"Kursen gjorde lägre botten ({f2(lowinfo['c2'])} mot {f2(lowinfo['c1'])}) men RSI(9) högre ({lowinfo['r2']:.0f} mot {lowinfo['r1']:.0f}).")
    mot.append(f"Stochastic: {stoch_text(K, D).lower()}. MACD: {macd_text(h).lower()}.")
    if vb and vb["botten"] is not None:
        x = vb["botten"]
        mot.append(f"Volymen vid andra bottnen var {100*x:.0f} % av den vid första" + (" (säljtrycket avtar)" if x < 0.8 else " (mer säljtryck)" if x > 1.2 else "")
                   + (f", och efter bottnen {100*vb['vand']:.0f} % av snittet" if vb["vand"] is not None else "") + ". Volymen har inte gett någon fördel i testet, så se den som kontext.")
    if sdd is not None: mot.append("Hela sektorn har fallit (median " + f"{100*sdd:.0f} %)." if sdd <= -0.15 else f"Sektorn har inte fallit lika mycket (median {100*sdd:.0f} %), så fallet är mer bolagsspecifikt.")
    if rs is not None:
        mot.append(f"Sedan första bottnen har aktien gått {abs(100*rs):.0f} procentenheter {'bättre' if rs >= 0 else 'sämre'} än index"
                   + (". Att aktien slutat falla mer än index har historiskt varit ett bra tecken." if rs >= -0.01 else "."))
    if note: mot.append(note + ".")
    if land != "SE": mot.append("Insyn, blankning och pressmeddelanden finns bara för svenska bolag, och reglerna är backtestade på Stockholmsbörsen.")
    rows.append(dict(t=t, namn=namn(t), sektor=s, land=land, valuta=VALUTA[land], mk_filter=MARKNAD[land]["filter"], bors=bors(t), rs=rs, nordnet=NORDNET.get(t), riktk=riktk, status=status, kurs=float(now), dd=float(dd), topp=float(c[top_i]),
                     toppdag=dates[top_i].strftime("%Y-%m-%d"), rsi=float(r[-1]), K=float(K[-1]), D=float(D[-1]),
                     stoch=stoch_text(K, D), macd=macd_text(h), vol=vol, sektor_dd=sdd, ins=ins, blank=blank, rapport=rapport,
                     nyheter=nyheter, niv=niv, imorgon=imorgon, low=lowinfo, vb=vb, hist=dict(n=hn, mal=hhit, stopp=hstop), flags=flags,
                     mot=" ".join(mot), not_=note, live=t in LIVE, ch=ch))

ORD = {"Köpläge": 0, "Avvakta": 1, "Närmar sig": 2, "Bevaka": 3, "Undvik": 4, "Utgår": 5}
rows.sort(key=lambda x: (ORD[x["status"]], x["dd"]))
from zoneinfo import ZoneInfo
meta = dict(uppdaterad=dt.datetime.now(ZoneInfo("Europe/Stockholm")).strftime("%Y-%m-%d %H:%M"), kursdag=dates[-1].strftime("%Y-%m-%d"),
            senast_stang=senast_stang.strftime("%Y-%m-%d"), prel=prel, mk_dd=mk_dd, mk_filter=MARKNAD["SE"]["filter"], bredd_grans=BREDD_GRANS, rs_grans=RS_GRANS,
            n_univ=len(univ), n_se=sum(1 for t in univ if LAND(t) == "SE"), n_no=sum(1 for t in univ if LAND(t) == "NO"),
            n_dk=sum(1 for t in univ if LAND(t) == "DK"), n_us=sum(1 for t in univ if LAND(t) == "US"),
            usa_prel=False, marknad=MARKNAD, risk_kr=RISK_KR)
# ---------- mina positioner och teoretisk portfölj (divergens_positioner.json, divergens_position.py) ----------
POS, PORTF = [], []
try:
    _pos = json.load(open(DATA + "divergens_positioner.json", encoding="utf-8")).get("positioner", [])
except FileNotFoundError:
    _pos = []
_kurva = {}
for ps_ in _pos:
    t_ = ps_["ticker"]
    if t_ not in px.columns: continue
    c_ = px[t_].dropna(); nu_ = float(c_.iloc[-1]); e_ = float(ps_["kurs"]); stp = float(ps_["stopp"]); fx_ = fx_sek(LAND(t_))
    R_ = e_ - stp; antal_ = int(ps_["antal"]); salj_ = ps_.get("salj", [])
    salt = sum(int(x["antal"]) for x in salj_)
    kvar = 0 if ps_.get("stangd") else antal_ - salt
    halv = bool(salt) or bool(ps_.get("notis", {}).get("m2"))
    if not salj_ and ps_.get("notis", {}).get("m2"): kvar = antal_ - antal_ // 2
    avg_ = VALUTA_AVG if LAND(t_) != "SE" else 0.0     # växlingsavgift vid köp och sälj
    real = sum(int(x["antal"]) * ((float(x["kurs"]) - e_) - avg_ * (float(x["kurs"]) + e_)) for x in salj_) * fx_
    oreal = kvar * ((nu_ - e_) - avg_ * (nu_ + e_)) * fx_
    risk = antal_ * R_ * fx_
    dagar = int((c_.index > pd.Timestamp(ps_["datum"])).sum())
    stangd = kvar <= 0
    PORTF.append(dict(ticker=t_, namn=namn(t_), land=LAND(t_), valuta=VALUTA[LAND(t_)], datum=ps_["datum"], antal=antal_, kvar=kvar, kurs=e_, nu=nu_,
                      salj=salj_, real=round(real), oreal=round(oreal), insats=round(antal_ * e_ * fx_), risk=round(risk),
                      r=(real + oreal) / risk if risk > 0 else None, stangd=stangd,
                      slut=max([x["datum"] for x in salj_], default=None) if stangd else None))
    # daglig kurva: realiserat t.o.m. dagen + innehav * (stängning - köpkurs)
    for d_ in c_.index[c_.index >= pd.Timestamp(ps_["datum"])]:
        ds = d_.strftime("%Y-%m-%d")
        sald = sum(int(x["antal"]) for x in salj_ if x["datum"] <= ds)
        rl = sum(int(x["antal"]) * ((float(x["kurs"]) - e_) - avg_ * (float(x["kurs"]) + e_)) for x in salj_ if x["datum"] <= ds)
        hav = 0 if (stangd and ds >= (PORTF[-1]["slut"] or ds)) else antal_ - sald
        _kurva[ds] = _kurva.get(ds, 0) + (rl + hav * ((float(c_[d_]) - e_) - avg_ * (float(c_[d_]) + e_))) * fx_
    if stangd: continue
    POS.append(dict(ticker=t_, namn=namn(t_), nordnet=NORDNET.get(t_), land=LAND(t_), valuta=VALUTA[LAND(t_)],
                    antal=antal_, kurs=e_, datum=ps_["datum"], nu=nu_, stopp=e_ if halv else stp, stopp_ursp=stp,
                    m2=e_ + 2 * R_, m3=e_ + 3 * R_, R=R_, dagar=dagar, dagar_kvar=max(0, 10 - dagar), halv=halv,
                    antal_kvar=kvar, resultat_r=(nu_ - e_) / R_ if R_ > 0 else None, notis=ps_.get("notis", {})))
if PORTF:
    meta["portfolj"] = dict(affarer=PORTF, kurva=sorted(_kurva.items()),
                            real=sum(x["real"] for x in PORTF), oreal=sum(x["oreal"] for x in PORTF),
                            risk=sum(x["risk"] for x in PORTF), start=min(x["datum"] for x in PORTF))
meta["positioner"] = POS
if UPPF:
    _st = {(r["t"], r["niv"]["signaldag"]): r["status"] for r in rows if r["niv"]}
    for u_ in UPPF: u_["status"] = _st.get((u_["t"], u_["datum"]))
    UPPF.sort(key=lambda u_: (u_["datum"], u_["namn"]), reverse=True)
    meta["uppfoljning"] = dict(start=UPPF_START, affarer=UPPF, kr=sum(u_["kr"] for u_ in UPPF if not u_["oppen"] and not u_["rapport"]),
                               idx_kr=sum(u_["idx_kr"] or 0 for u_ in UPPF if not u_["oppen"] and not u_["rapport"]),
                               n=sum(1 for u_ in UPPF if not u_["rapport"]), stangda=sum(1 for u_ in UPPF if not u_["oppen"] and not u_["rapport"]),
                               rapport_dagar=RAPPORT_DAGAR)
# ---------- extra bevakning (divergens_bevaka.json: {"tickers": [...]}) ----------
try:
    BEV_T = json.load(open(DATA + "divergens_bevaka.json", encoding="utf-8")).get("tickers", [])
except FileNotFoundError:
    BEV_T = []
BEV_EXTRA = list(BEV_T)
radmap = {r["t"]: r for r in rows}
# alla som Närmar sig bevakas automatiskt, och de som var Närmar sig vid förra stängningen (så att en ändring därifrån syns)
try:
    _forra = json.load(open(DATA + "divergens_state.json", encoding="utf-8")).get("bevakning", {})
except FileNotFoundError:
    _forra = {}
BEV_T = list(dict.fromkeys(BEV_T + [r["t"] for r in rows if r["status"] == "Närmar sig"]
                           + [t for t, v in _forra.items() if v.get("status") == "Närmar sig"]))
BEV = {}
for t in BEV_T:
    if t not in px.columns: continue
    c, r, K, D, h, hi250 = ind(t)
    r_ = radmap.get(t)
    lag20 = float(np.nanmin(c[-21:-1])); lag20_g = float(np.nanmin(c[-22:-2]))
    BEV[t] = dict(namn=namn(t), status=r_["status"] if r_ else "Ingen signal", not_=r_["not_"] if r_ else "",
                  kurs=float(c[-1]), K=float(K[-1]), rsi=float(r[-1]), lagsta20=lag20, ny_botten=bool(c[-1] < lag20),
                  igar=dict(oversald=bool(K[-2] < 25), ny_botten=bool(c[-2] < lag20_g)),
                  imorgon=r_["imorgon"] if r_ else None, niv=r_["niv"] if r_ else None)
    if r_ and t in BEV_EXTRA: r_["bevakas"] = True
meta["bevakning"] = BEV
data = dict(meta=meta, rows=rows)
json.dump({r["t"]: dict(status=r["status"], namn=r["namn"], kurs=r["kurs"], valuta=r["valuta"], niv=r["niv"], imorgon=r["imorgon"], rs=r["rs"], land=r["land"]) for r in rows} | {"_positioner": POS, "_bevakning": BEV},
          open(UT.rsplit(".", 1)[0] + "_status.json", "w"), ensure_ascii=False, indent=1)
mall = open(__file__.rsplit("/", 1)[0] + "/divergens_mall.html", encoding="utf-8").read()
open(UT, "w", encoding="utf-8").write(mall.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, default=float)))
antal = ", ".join(f"{s}: {sum(1 for r in rows if r['status'] == s)}" for s in ORD)
print(f"{len(rows)} kandidater ({antal}), kursdag {meta['kursdag']}, OMXSPI {100*mk_dd:.1f} % från topp, bredd {100*(MARKNAD['SE']['bredd'] or 0):.0f} % under 50d, filter {'på' if MARKNAD['SE']['filter'] else 'av'}" + (" [PRELIMINÄRT]" if prel else ""))
