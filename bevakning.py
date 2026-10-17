#!/usr/bin/env python3
"""
Bevakning: aktuella fall där flera insiders köper, särskilt efter ett kraftigt
kursfall (som Asker i september 2026).

För varje bolag med insiderköp publicerade de senaste N dagarna visas:
antal personer och roller, köpbelopp, vägd köpkurs, kursen nu, utvecklingen
sedan köpen och hur kursen låg till vid första köpet:
  snabbt_fall = minst 15 % lägre än 15 handelsdagar tidigare
  fall        = minst 20 % under 52-veckorshögsta
  ej_fall     = inget av detta

Kör:
  python bevakning.py                     # senaste 30 dagarna, minst 2 personer
  python bevakning.py --dagar 14 --min-personer 3
  python bevakning.py --endast-fall       # bara fall efter kursfall

Utdata: bevakning.csv

Om nyheter.csv finns (nyheter.py) tillkommer Nyhet_fore (högsta nyhetstyp i bolaget de
14 dagarna före första köpet: vinstvarning, nyemission, m_and_a, ledning, rapport ...)
och Nyhet_rubrik med datum och rubrik. Ett köp efter en vinstvarning eller ett ras på
rapporten är något annat än ett köp utan känd orsak.

Kolumnen Kontroll: "kurs avviker" = köpkursen skiljer sig mer än en faktor 2 från
marknadskursen (split, emission, uppköp?). Sådana rader ska granskas för hand.

Obs: tickers på Frankfurt (.F) handlas i euro och jämförs inte mot köpkursen
i kronor; utvecklingen sedan köp visas då inte.
"""
import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

import backtest as bt
import insider_bot as ib
import nyheter as ny
import analys

MAPP = Path(__file__).resolve().parent


_RAPPORT_RE = r"(?i)rapport|bokslut|delår|halvår|kvartal"
_EJ_RAPPORT_RE = r"(?i)inbjud|bjuder in|presentation|redovisning|webb|telefonkonferens|publicering av|kallelse"


def _las_rapporter() -> dict:
    """Rapportdatum per ticker: tidigare ur Nasdaq-nyheterna (rapporter.csv) och, där de saknas eller för
    kommande rapporter, ur Yahoos kalender (rapportkalender.csv). Kommande datum läggs i RAPPORT_FRAMTID."""
    ut = {}
    pm_ = {}          # kommande rapportdatum som bolaget själv anger i en rubrik
    idag0 = pd.Timestamp.today().normalize()

    def _pm(t, rub, pub):
        d = ny.rapportdatum_i_rubrik(rub, pub)
        if d is not None and idag0 < d <= idag0 + pd.Timedelta(days=130) and (t not in pm_ or d < pm_[t]):
            pm_[t] = d
    f = MAPP / "rapporter.csv"
    if f.exists():
        r = pd.read_csv(f, parse_dates=["Publicerat"]).dropna(subset=["Ticker"])
        rub = r["Rubrik"].astype(str)
        # 'Trelleborg publicerar rapport ... den 23 oktober' är ett besked om datum, inte rapporten: ut ur historiken, in som kommande datum
        ann = pd.Series([ny.rapportdatum_i_rubrik(a, b) is not None for a, b in zip(rub, r["Publicerat"])], index=r.index)
        for t, a, b in zip(r["Ticker"][ann], rub[ann], r["Publicerat"][ann]):
            _pm(t, a, b)
        r = r[rub.str.contains(_RAPPORT_RE) & ~rub.str.contains(_EJ_RAPPORT_RE) & ~ann]
        ut = {t: sorted(g["Publicerat"].dt.normalize().unique()) for t, g in r.groupby("Ticker")}
    # reservkälla för bolag utan Nasdaq-nyheter (bev_mfn.py): rapporter ur MFN/Cision-flödet
    m = MAPP / "nyheter_mfn.csv"
    if m.exists():
        x = pd.read_csv(m, dtype=str, keep_default_na=False)
        x = x[(x["Typ"] == "rapport") & x["Rubrik"].str.contains(_RAPPORT_RE) & ~x["Rubrik"].str.contains(_EJ_RAPPORT_RE)]
        for t, g in x.groupby("Ticker"):
            hist = [pd.Timestamp(d) for d in ut.get(t, [])]
            for d in pd.to_datetime(g["Publicerat"], errors="coerce").dt.normalize().dropna():
                if all(abs((d - h).days) > 7 for h in hist):
                    hist.append(d)
            ut[t] = sorted(hist)
    RAPPORT_FRAMTID.clear()
    RAPPORT_FRAMTID_KALLA.clear()
    if m.exists():
        y = pd.read_csv(m, dtype=str, keep_default_na=False)
        for t, rub, pub in zip(y["Ticker"], y["Rubrik"], pd.to_datetime(y["Publicerat"], errors="coerce")):
            if not pd.isna(pub):
                _pm(t, rub, pub)
    k = MAPP / "rapportkalender.csv"
    if k.exists():
        c = pd.read_csv(k, parse_dates=["Datum"])
        idag = pd.Timestamp.today().normalize()
        for t, g in c.groupby("Ticker"):
            hist = [pd.Timestamp(x) for x in ut.get(t, [])]
            for d in g[g["Datum"] <= idag]["Datum"]:
                if all(abs((d - h).days) > 7 for h in hist):      # samma rapport finns redan om datumet ligger nära
                    hist.append(d)
            if hist:
                ut[t] = sorted(hist)
            fut = g[g["Datum"] > idag]["Datum"]
            if len(fut):
                RAPPORT_FRAMTID[t] = fut.min()
    for t, d in pm_.items():        # bolagets eget pressmeddelande går före Yahoos kalender
        RAPPORT_FRAMTID[t] = d
        RAPPORT_FRAMTID_KALLA[t] = "pm"
    return ut


RAPPORT_FRAMTID: dict = {}
RAPPORT_FRAMTID_KALLA: dict = {}


def _rapport_info(datum, idag, framtid=None, framtid_kalla="kalender"):
    """(senaste rapport, dagar sedan, nästa rapport, dagar till, rapportdatum senaste 380 d, källa).
    Nästa rapport: 1) kommande datum ur kalendern (källa 'kalender'), 2) samma rapport ett år tidigare + 364 dagar
    ('uppskattad'), 3) senaste rapport + 91 dagar ('grovt'), när bolaget rapporterar kvartalsvis."""
    d = [pd.Timestamp(x) for x in (datum or [])]
    for_ = [x for x in d if x <= idag]
    senast = max(for_) if for_ else None
    nasta, kalla = None, ""
    if framtid is not None and pd.notna(framtid) and idag < framtid <= idag + pd.Timedelta(days=130):
        nasta, kalla = pd.Timestamp(framtid), framtid_kalla
    if nasta is None:
        c = [x + pd.Timedelta(days=364) for x in d if idag < x + pd.Timedelta(days=364) <= idag + pd.Timedelta(days=130)]
        if c:
            nasta, kalla = min(c), "uppskattad"
    if nasta is None and senast is not None and (idag - senast).days <= 200:
        n = senast + pd.Timedelta(days=91)
        while n <= idag:
            n += pd.Timedelta(days=91)
        if (n - idag).days <= 130:
            nasta, kalla = n, "grovt"
    sedan = (idag - senast).days if senast is not None else np.nan
    till = (nasta - idag).days if nasta is not None else np.nan
    return senast, sedan, nasta, till, [x for x in for_ if x >= idag - pd.Timedelta(days=380)], kalla


def _serieskopare(g, hist) -> str:
    """Personer bakom köpet som också köpt (>=50 tkr) under minst 3 olika månader de senaste 12 månaderna
    före köpfönstret. Bara information, påverkar inte poängen: i backtestet presterar händelser med
    serieköpare inte sämre (6 mån +11 % mot +8,5 %; osäkert, händelserna överlappar)."""
    if hist is None or hist.empty:
        return ""
    m = hist.assign(_m=hist["Publicerat"].dt.year * 12 + hist["Publicerat"].dt.month) \
            .groupby("Person i ledande ställning")["_m"].nunique()
    ut = []
    for p in sorted(set(g["Person i ledande ställning"])):
        n = int(m.get(p, 0))
        if n >= 3:
            ut.append(f"{p} (köpt {n} av senaste 12 mån)")
    return "; ".join(ut)


def _bolagsnamn(g):
    """Bolagsnamn: vanligaste Emittent, men FI har ibland insiderns namn i det fältet."""
    pers = set(g["Person i ledande ställning"].astype(str).str.strip().str.lower())
    ok = g[~g["Emittent"].astype(str).str.strip().str.lower().isin(pers)]
    if len(ok):
        return ok["Emittent"].value_counts().idxmax()
    return g["Instrumentnamn"].value_counts().idxmax()


_OHLC = None


_VOL = None


def _vol() -> pd.DataFrame:
    """Volym per dag och ticker (kurser.csv), för volympanelen och kommentarerna."""
    global _VOL
    if _VOL is None:
        try:
            k = pd.read_csv(Path(__file__).with_name("kurser.csv"), usecols=["Datum", "Ticker", "Volym"], parse_dates=["Datum"])
            _VOL = k.pivot_table(index="Datum", columns="Ticker", values="Volym", aggfunc="last")
        except Exception as e:
            print("Kunde inte läsa volym:", e)
            _VOL = pd.DataFrame()
    return _VOL


def _vol_tk(tk):
    v = _vol()
    return v[tk] if tk in v.columns else None


def _ohlc() -> dict:
    """ohlc.csv (om den finns) som {ticker: DataFrame(Open, High, Low, Close)}, för ljusstakar i graferna."""
    global _OHLC
    if _OHLC is None:
        _OHLC = {}
        f = Path(__file__).with_name("ohlc.csv")
        if f.exists():
            try:
                d = pd.read_csv(f, parse_dates=["Datum"])
                _OHLC = {t: g.set_index("Datum")[["Open", "High", "Low", "Close"]].sort_index() for t, g in d.groupby("Ticker")}
            except Exception as e:
                print("Kunde inte läsa ohlc.csv:", e)
    return _OHLC


def poang(r) -> tuple:
    """Poäng 0-100 för ett fall. Fem villkor ger ett poäng vardera (bara .ST-aktier):
      över MA200, inte efter kursfall, inom 10 % av 52v-högsta, omsättning >= 2 MSEK/dag, minst 2 personer.
    Backtestet (belopp_50k, 7 500 köp 2021-2026) ger median överavkastning mot medianaktien på 6 mån
    per antal poäng (0-5): -2,7 / +1,1 / +3,9 / +6,5 / +8,3 / +10 %, i samma ordning i båda delperioderna.
    Skalan: 20 poäng per uppfyllt villkor (0, 20, 40, 60, 80, 100). 40 motsvarar ett genomsnittligt insynsköp >= 50 tkr,
    under 40 är historiskt sämre än snittet och över 40 bättre."""
    if not str(r.get("Ticker", "")).endswith(".ST") or str(r.get("Kontroll", "")) == "kurs avviker":
        return np.nan, "ej bedömbar (inte .ST eller avvikande köpkurs)"
    ja = []
    if r.get("Over_MA200") == "ja": ja.append("över MA200")
    if r.get("Tillstand") == "ej_fall": ja.append("ej efter fall")
    ub = r.get("Under_52v_hogsta_%")
    if pd.notna(ub) and ub >= -10: ja.append("nära 52v-högsta")
    om = r.get("Oms_MSEK_dag")
    if pd.notna(om) and om >= LIKVID_MIN / 1e6: ja.append("likvid")
    if r.get("Antal_personer", 0) >= 2: ja.append("2+ personer")
    return float(20 * len(ja)), ", ".join(ja) if ja else "inga"


def _orsak(r) -> str:
    """Varför ett fall inte är en regelträff (tomt för regelträffar)."""
    if r.get("Regel") == "ja":
        return ""
    o = []
    if str(r.get("Kontroll", "")) == "euro" or not str(r.get("Ticker", "")).endswith(".ST"):
        o.append("ej svensk .ST-notering")
    if str(r.get("Kontroll", "")) == "kurs avviker":
        o.append("köpkursen avviker från marknadskursen")
    if r.get("Tillstand") != "ej_fall":
        o.append(f"efter kursfall ({r.get('Under_52v_hogsta_%', float('nan')):.0f} % från 52v-högsta)")
    if r.get("Over_MA200") == "nej":
        o.append("under MA200")
    om = r.get("Oms_MSEK_dag")
    if pd.isna(om) or om < LIKVID_MIN / 1e6:
        o.append("omsättning under 2 MSEK/dag" + ("" if pd.isna(om) else f" ({om:.1f})"))
    if r.get("Belopp", 0) < REGEL_MIN_BELOPP:
        o.append("köp under 50 tkr")
    return "; ".join(o)


def _handel_per_ticker(tickermap) -> dict:
    """Insynshandel (köp och sälj av aktier i SEK) per ticker, för diagrammen.
    Publiceringsdatum används, eftersom det är då marknaden fick veta. Sälj som matchar ett köp (samma
    person, dag, volym och pris) räknas som intern överföring och tas bort."""
    h = ib.las_historik()
    if h.empty:
        return {}
    h = ib.rensa(h)
    h = h[(h["Instrumenttyp"] == "Aktie") & h["Karaktär"].isin(["Förvärv", "Avyttring"])
          & (h["Valuta"].str.upper() == "SEK") & (h["Pris"] > 0)].copy()
    h["Bolag_id"] = ib.bolagsnyckel(h)
    h["Person i ledande ställning"] = h["Person i ledande ställning"].astype(str).str.strip().str.title()
    nyckel = ["Bolag_id", "Person i ledande ställning", "Transaktionsdatum", "Volym", "Pris"]
    kop_n = set(map(tuple, h.loc[h["Karaktär"] == "Förvärv", nyckel].values))
    salj_n = set(map(tuple, h.loc[h["Karaktär"] == "Avyttring", nyckel].values))
    intern = h[nyckel].apply(tuple, axis=1).isin(kop_n & salj_n)
    h = h[~intern].drop_duplicates(subset=nyckel + ["Karaktär"])
    tm = pd.read_csv(tickermap, keep_default_na=False, dtype=str)
    tm = tm[(tm["Ticker"] != "") & tm["Status"].isin(["ok", "manuell"])]
    h["Ticker"] = h["Bolag_id"].map(dict(zip(tm["Bolag_id"], tm["Ticker"])))
    h = h[h["Ticker"].notna()]
    pub = pd.to_datetime(h["Publiceringsdatum"])
    trans = pd.to_datetime(h["Transaktionsdatum"], errors="coerce")
    # Markören placeras där affären gjordes. Saknas/orimligt transaktionsdatum (efter publiceringen eller > 1 år före)
    # används publiceringsdatumet. Publ behålls för att kunna visa sena anmälningar.
    ok = trans.notna() & (trans <= pub) & (pub - trans <= pd.Timedelta(days=400))
    h = h.assign(Datum=trans.where(ok, pub), Publ=pub, Typ=np.where(h["Karaktär"] == "Förvärv", "Köp", "Sälj"),
                 Person=h["Person i ledande ställning"])[["Ticker", "Datum", "Publ", "Typ", "Belopp", "Person"]]
    return {t: g.drop(columns="Ticker") for t, g in h.groupby("Ticker")}


def _antal_aktier() -> dict:
    f = MAPP / "aktier.csv"
    if not f.exists():
        return {}
    a = pd.read_csv(f, dtype={"Ticker": str}).dropna(subset=["Aktier"])
    return dict(zip(a["Ticker"], a["Aktier"]))

# Aktiv regel (backtest 2021-2026, överavkastning mot medianaktien ca +12 % på 12 mån i båda delperioderna):
# insynsköp >= 50 tkr, aktien över MA200, inte efter kursfall (ej_fall), omsättning >= 2 MSEK/dag.
REGEL_MIN_BELOPP = 50_000
LIKVID_MIN = 2_000_000
ROD_TYPER = ("vinstvarning", "nyemission")
# Bevakningsnivå under regeln: ett enskilt köp på minst så här mycket tas med som "Bevaka" även om aktien
# inte uppfyller regeln (t.ex. efter kursfall eller illikvid, som Lyko sept 2026). Backtestet ger ingen edge
# för dessa fall (fall + över MA200 gav i snitt -2 % mot medianaktien på 3 mån), så det är en lista att
# hålla koll på, inte en köpsignal.
BEVAKA_MIN_BELOPP = 250_000


def _omsattning(kurser_fil, px):
    """Median omsättning (kr/dag) de 20 dagarna före varje dag, som i backtest.py."""
    d = pd.read_csv(kurser_fil, dtype={"Ticker": str})
    if "Volym" not in d.columns or d["Volym"].notna().sum() == 0:
        return None
    d["Datum"] = pd.to_datetime(d["Datum"])
    d["Oms"] = d["Stang"] * d["Volym"]
    o = d.pivot_table(index="Datum", columns="Ticker", values="Oms", aggfunc="last").sort_index()
    o = o.reindex(index=px.index, columns=px.columns)
    return o.rolling(20, min_periods=10).median().shift(1)


def _sen_anmalan(g) -> dict:
    """Hur många dagar efter affären den största insynsaffären i fallet anmäldes (FI-regeln är 3 arbetsdagar).
    Sen_anm_dagar fylls bara när det är mer än 14 dagar; Affar_datum är den affärens transaktionsdatum."""
    if "Transaktionsdatum" not in g.columns or g.empty:
        return {"Sen_anm_dagar": np.nan, "Affar_datum": ""}
    r = g.loc[g["Belopp"].idxmax()]
    t = pd.to_datetime(r["Transaktionsdatum"], errors="coerce")
    if pd.isna(t) or t > r["Publicerat"]:
        return {"Sen_anm_dagar": np.nan, "Affar_datum": ""}
    dagar = (r["Publicerat"] - t).days
    return {"Sen_anm_dagar": float(dagar) if dagar > 14 else np.nan, "Affar_datum": t.date().isoformat()}


def _med_mfn(nyh, idag):
    """Lägger MFN/Cision-nyheter (nyheter_mfn.csv, bev_mfn.py) till bolag som saknar Nasdaq-nyheter det senaste året.
    Återköp sparas inte i Nasdaq-datan och kommer därför bara härifrån. Returnerar (nyheter, återköp, Bolag_id med Nasdaq-täckning,
    Bolag_id med MFN-täckning). Saknas filen blir allt som förut."""
    nasdaq = set(nyh.loc[nyh["Publicerat"] > idag - pd.Timedelta(days=365), "Bolag_id"])
    tom = pd.DataFrame(columns=["Publicerat", "Bolag_id", "Rubrik"])
    f = MAPP / "nyheter_mfn.csv"
    if not f.exists():
        return nyh, tom, nasdaq, set()
    m = pd.read_csv(f, keep_default_na=False, dtype=str)
    m["Publicerat"] = pd.to_datetime(m["Publicerat"], errors="coerce")
    m = m.dropna(subset=["Publicerat"])
    kf = MAPP / "bev_mfn_kalla.csv"
    mfn = set()
    if kf.exists():
        kk = pd.read_csv(kf, keep_default_na=False, dtype=str)
        mfn = {"t:" + t for t in kk.loc[kk["Typ"] != "", "Ticker"]}
    aterk = m[m["Typ"] == "aterkop"]
    rader = m[~m["Typ"].isin(["aterkop", "rapport_datum"]) & ~m["Bolag_id"].isin(nasdaq)]
    kol = list(nyh.columns)
    nyh = pd.concat([nyh.assign(Kalla="nasdaq"), rader[kol].assign(Kalla="mfn")], ignore_index=True)
    return nyh, aterk, nasdaq, mfn


def bevaka(kop_fil, tickermap, kurser_fil, dagar=30, min_personer=2, min_belopp=200_000,
           endast_fall=False, idag=None, nyheter_fil=None, diagram_mapp=None, tickers=None):
    k, _ = bt.las_kop(Path(kop_fil), Path(tickermap))
    kurser = bt.las_kurser(Path(kurser_fil))
    idag = pd.Timestamp(idag) if idag is not None else pd.Timestamp.today().normalize()
    gran = idag - pd.Timedelta(days=dagar)
    k_hist = k[(k["Publicerat"] < gran) & (k["Publicerat"] >= gran - pd.Timedelta(days=365)) & (k["Belopp"] >= 50_000)]
    k_hist = {t: x for t, x in k_hist.groupby("Ticker")}
    k = k[k["Publicerat"] >= gran]
    if tickers is not None:
        k = k[k["Ticker"].isin(tickers)]
    if k.empty:
        return pd.DataFrame()

    rapporter = _las_rapporter()
    handel = _handel_per_ticker(tickermap) if diagram_mapp else {}
    aktier_n = _antal_aktier()
    nyh = ny.las_nyheter(nyheter_fil) if nyheter_fil and Path(nyheter_fil).exists() else None
    if nyh is not None:
        # las_kop slår ihop bolag på ticker (Bolag_id = "t:"+ticker); nyheterna har namnbaserad nyckel.
        # Översätt nyheternas nyckel på samma sätt, annars matchar ingen nyhet något köp.
        tm = pd.read_csv(tickermap, keep_default_na=False, dtype=str)
        tm = tm[(tm["Ticker"] != "") & tm["Status"].isin(["ok", "manuell"])]
        mp = dict(zip(tm["Bolag_id"], tm["Ticker"]))
        tick = nyh["Bolag_id"].map(mp)
        nyh = nyh.assign(Bolag_id=np.where(tick.notna(), "t:" + tick.astype(str), nyh["Bolag_id"]))
        nyh, aterk, ids_nasdaq, ids_mfn = _med_mfn(nyh, idag)

    dates = kurser[bt.INDEX].dropna().index
    px = kurser.reindex(dates).ffill(limit=5)
    hog = px.rolling(252, min_periods=120).max()
    utv15 = px / px.shift(bt.SNABB_DAGAR) - 1
    ma200 = px.rolling(200, min_periods=150).mean()
    oms = _omsattning(kurser_fil, px)

    rader = []
    for tk, g in k.groupby("Ticker"):
        if tk not in px.columns:
            continue
        forst = g["Publicerat"].min()
        pos = dates.searchsorted(forst, side="right") - 1      # sista handelsdag <= första publiceringen
        if pos < 0 or pd.isna(px.iloc[pos][tk]):
            continue
        dag = dates[pos]
        under_hog = px.at[dag, tk] / hog.at[dag, tk] - 1 if pd.notna(hog.at[dag, tk]) else np.nan
        fall15 = utv15.at[dag, tk]
        if pd.notna(fall15) and fall15 <= bt.SNABB_FALL:
            tillstand = "snabbt_fall"
        elif pd.notna(under_hog) and under_hog <= bt.NEDGANG_FALL:
            tillstand = "fall"
        else:
            tillstand = "ej_fall"

        trend_ok = bool(px.at[dag, tk] > ma200.at[dag, tk]) if pd.notna(ma200.at[dag, tk]) else False
        omsatt = oms.at[dag, tk] if (oms is not None and tk in oms.columns) else np.nan
        likvid_ok = bool(pd.notna(omsatt) and omsatt >= LIKVID_MIN)

        unika = g[g["Belopp_unik"] > 0]
        belopp = g["Belopp_unik"].sum()
        volym = unika["Volym"].sum()
        kopkurs = belopp / volym if volym else np.nan
        nu = px[tk].dropna().iloc[-1] if px[tk].notna().any() else np.nan
        euro = tk.endswith(".F")
        kurs_dag = px.at[dag, tk]
        # Köpkursen ska ligga nära marknadskursen. Avviker den kraftigt kan det bero på
        # split, sammanläggning, emission, uppköp eller valuta, och utvecklingen blir missvisande.
        avviker = (not euro) and bool(kopkurs) and not (0.5 <= kopkurs / kurs_dag <= 2.0)
        rad = {
            "Bolag": _bolagsnamn(g),
            "Ticker": tk,
            "Tillstand": tillstand,
            "Antal_personer": g["Person i ledande ställning"].nunique(),
            "Belopp": round(belopp),
            "Kopkurs": round(kopkurs, 2) if not euro else np.nan,
            "Kurs_nu": round(nu, 2) if not euro else np.nan,
            "Utv_sedan_kop_%": round((nu / kopkurs - 1) * 100, 1) if not euro and kopkurs else np.nan,
            "Fall_15d_fore_%": round(fall15 * 100, 1) if pd.notna(fall15) else np.nan,
            "Under_52v_hogsta_%": round(under_hog * 100, 1) if pd.notna(under_hog) else np.nan,
            "Over_MA200": "ja" if trend_ok else "nej",
            "Oms_MSEK_dag": round(omsatt / 1e6, 2) if pd.notna(omsatt) else np.nan,
            "Borsvarde_MSEK": round(aktier_n[tk] * nu / 1e6) if (tk in aktier_n and tk.endswith(".ST") and not euro and pd.notna(nu)) else np.nan,
            "Regel": "ja" if (trend_ok and tillstand == "ej_fall" and likvid_ok and not euro
                              and tk.endswith(".ST") and not avviker and belopp >= REGEL_MIN_BELOPP) else "nej",
            "Kontroll": "kurs avviker" if avviker else ("euro" if euro else ""),
            "Forsta_publ": forst.date().isoformat(),
            "Senaste_publ": g["Publicerat"].max().date().isoformat(),
            **_sen_anmalan(g),
            "Personer": ", ".join(sorted(set(g["Namn_roll"]))),
            "Serieskopare": _serieskopare(g, k_hist.get(tk)),
        }
        # teknisk analys: stop loss, brytkurs och (för regelträffar) graf med förväntan 3 månader fram
        senast_r, dagar_sedan_r, nasta_r, dagar_till_r, rapp_dat, rapp_kalla = _rapport_info(rapporter.get(tk), idag, RAPPORT_FRAMTID.get(tk), RAPPORT_FRAMTID_KALLA.get(tk, "kalender"))
        rad.update({"Rapport_senast": senast_r.date().isoformat() if senast_r is not None else "",
                    "Dagar_sedan_rapport": dagar_sedan_r,
                    "Rapport_nasta_uppsk": nasta_r.date().isoformat() if nasta_r is not None else "",
                    "Dagar_till_rapport": dagar_till_r, "Rapport_kalla": rapp_kalla})
        tek = analys.teknisk(px[tk].dropna()) if tk.endswith(".ST") else {}
        if tk.endswith(".ST"):
            try:
                rad["Kommentar"] = analys.kommentar(re.sub(r"\s*\(\s*publ\s*\)|\s+(aktiebolag|ab|inc\.?|se|ag)\b", "", str(rad["Bolag"]), flags=re.I).strip(), px[tk].dropna(), _vol_tk(tk))
            except Exception as e:
                print(f"  Kommentar för {tk} misslyckades: {e}")
        if tek and rad["Regel"] == "ja" and tek.get("Over_MA200") is False:
            rad["Regel"] = "nej"            # har tappat trenden sedan köpet: inte längre en regelträff
        if tek:
            rad.update({"Stop": round(tek["Stop"], 2), "Stop_%": round(tek["Stop_%"], 1),
                        "Brytkurs": round(tek["Brytkurs"], 2), "Brytkurs_%": round(tek["Brytkurs_%"], 1),
                        "Bruten": "ja" if tek["Bruten"] else "nej", "RSI14": round(tek["RSI14"], 0),
                        "Teknik": tek["Text"].replace("\n", " | ")})
            if diagram_mapp:                 # graf för alla fall med kursdata (inte bara de över beloppsgränsen)
                Path(diagram_mapp).mkdir(exist_ok=True)
                try:
                    analys.rita(rad["Bolag"], tk, px[tk].dropna(), tek, Path(diagram_mapp) / f"{tk}.png", ohlc=_ohlc().get(tk),
                                publ_datum=forst, kopkurs=kopkurs,
                                handel=handel.get(tk), rapporter=(rapp_dat, nasta_r), borsvarde=aktier_n.get(tk, np.nan) * nu if tk in aktier_n else None,
                                vol=_vol_tk(tk))
                    rad["Diagram"] = f"{tk}.png"
                except Exception as e:      # grafen får aldrig stoppa flaggningen
                    print(f"  Diagram för {tk} misslyckades: {e}")
        elif diagram_mapp and tk.endswith(".ST") and len(px[tk].dropna()) >= 2:
            # nyligen noterat bolag: för lite historik för teknisk analys, men en enkel graf går att rita
            try:
                Path(diagram_mapp).mkdir(exist_ok=True)
                analys.rita(rad["Bolag"], tk, px[tk].dropna(), {}, Path(diagram_mapp) / f"{tk}.png", publ_datum=forst,
                            kopkurs=kopkurs, handel=handel.get(tk), rapporter=(rapp_dat, nasta_r), forvantan=False,
                            ohlc=_ohlc().get(tk), vol=_vol_tk(tk))
                rad["Diagram"] = f"{tk}.png"
            except Exception as e:
                print(f"  Enkel graf för {tk} misslyckades: {e}")
        if nyh is not None:
            # nyheter i bolaget de 14 dagarna före första köpet: vad föregick insiderköpet?
            bid = g["Bolag_id"].iloc[0]
            typ, nd, rubrik = ny.nyhet_fore(nyh, bid, forst, 14)
            # Täckning: Nasdaq-nyheter, annars MFN/Cision, annars saknas nyhetsdata (då är 'ingen nyhet' okänt, inte nej)
            kalla_n = "nasdaq" if bid in ids_nasdaq else "mfn" if bid in ids_mfn else "saknas"
            rad["Nyhet_kalla"] = kalla_n
            rad["Nyhet_fore"] = typ or ("okand" if kalla_n == "saknas" else "ingen_nyhet")
            ak = aterk[(aterk["Bolag_id"] == bid) & (aterk["Publicerat"] > idag - pd.Timedelta(days=90))]
            rad["Aterkop"] = "" if ak.empty else f"{ak['Publicerat'].max().date().isoformat()}: {str(ak.sort_values('Publicerat').iloc[-1]['Rubrik'])[:80]}"
            rad["Nyhet_rubrik"] = f"{nd}: {rubrik}"[:110] if typ else ""
            # Röd flagga: vinstvarning eller nyemission senaste 30 dagarna. Inte bevisat i backtestet
            # (för få träffar), men värt att läsa innan man agerar.
            rod = []
            for t in ROD_TYPER:
                n30 = nyh[(nyh["Bolag_id"] == g["Bolag_id"].iloc[0]) & (nyh["Typ"] == t)
                          & (nyh["Publicerat"] > idag - pd.Timedelta(days=30))]
                if not n30.empty:
                    r0 = n30.sort_values("Publicerat").iloc[-1]
                    rod.append(f"{t} {r0['Publicerat'].date().isoformat()}: {str(r0['Rubrik'])[:70]}")
            rad["Rod_flagga"] = " | ".join(rod)
        rader.append(rad)
    res = pd.DataFrame(rader)
    if res.empty:
        return res
    # Regelträffar (backtestad regel) tas med redan vid ett köp på minst REGEL_MIN_BELOPP;
    # övriga fall kräver min_personer och min_belopp som förut.
    res = res[((res["Antal_personer"] >= min_personer) & (res["Belopp"] >= min_belopp)) | (res["Regel"] == "ja")
              | (res["Belopp"] >= BEVAKA_MIN_BELOPP)]
    res["Kategori"] = np.where(res["Regel"] == "ja", "Regelträff", "Bevaka")
    res["Varfor_ej_regel"] = [_orsak(r) for _, r in res.iterrows()]
    pp = [poang(r) for _, r in res.iterrows()]
    res["Poang"] = [p[0] for p in pp]
    res["Poang_skal"] = [p[1] for p in pp]
    if endast_fall:
        res = res[res["Tillstand"] != "ej_fall"]
    res = res.assign(_o=np.where(res["Regel"] == "ja", 0, 1), _p=res["Poang"].fillna(-99)).sort_values(
        ["_o", "_p", "Antal_personer", "Belopp"], ascending=[True, False, False, False]).drop(columns=["_o", "_p"])
    return res.reset_index(drop=True)


def _med_signalfall(res, a):
    """Knivar äldre än listans fönster (30 d) tas med så länge en vändningssignal kom de senaste 10 handelsdagarna,
    annars skickas en notis om en aktie som inte finns i listan. De får kolumnen Lagts_till = "vändningssignal"."""
    try:
        import vandning
        from vandning import MAX_DAG
        gem = bevaka(a.kop, a.tickermap, a.kurser, MAX_DAG + 30, a.min_personer, a.min_belopp, False, nyheter_fil=MAPP / "nyheter.csv")
        if gem.empty:
            return res
        kn = gem[gem["Tillstand"].isin(["fall", "snabbt_fall"]) & (gem["Kategori"] != "Regelträff") & gem["Ticker"].str.endswith(".ST")]
        kn = kn[~kn["Ticker"].isin(set(res["Ticker"]))]
        if kn.empty:
            return res
        kurser = bt.las_kurser(Path(a.kurser)); kurser.index = pd.to_datetime(kurser.index)
        v = _vol()
        tk = set()
        for _, r in kn.iterrows():
            t = r["Ticker"]
            if t in kurser and t in v.columns and vandning.signaler(kurser[t], v[t], r["Forsta_publ"], tillbaka=10):
                tk.add(t)
        if not tk:
            return res
        ext = bevaka(a.kop, a.tickermap, a.kurser, MAX_DAG + 30, a.min_personer, a.min_belopp, False,
                     nyheter_fil=MAPP / "nyheter.csv", diagram_mapp=MAPP / "diagram", tickers=tk)
        ext["Lagts_till"] = "vändningssignal"
        print(f"{len(ext)} äldre knivar med ny vändningssignal läggs till: {', '.join(ext['Ticker'])}")
        return pd.concat([res, ext], ignore_index=True)
    except Exception as e:
        print("Kunde inte lägga till signalfall:", e)
        return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kop", default=str(MAPP / "insiderkop.csv"))
    ap.add_argument("--tickermap", default=str(MAPP / "ticker_map.csv"))
    ap.add_argument("--kurser", default=str(MAPP / "kurser.csv"))
    ap.add_argument("--dagar", type=int, default=30)
    ap.add_argument("--min-personer", type=int, default=2)
    ap.add_argument("--min-belopp", type=float, default=200_000)
    ap.add_argument("--endast-fall", action="store_true")
    a = ap.parse_args()

    res = bevaka(a.kop, a.tickermap, a.kurser, a.dagar, a.min_personer, a.min_belopp, a.endast_fall,
                 nyheter_fil=MAPP / "nyheter.csv", diagram_mapp=MAPP / "diagram")
    res = _med_signalfall(res, a)
    if res.empty:
        print("Inga bolag uppfyller villkoren.")
        return
    res.to_csv(MAPP / "bevakning.csv", index=False)
    pd.set_option("display.width", 220, "display.max_columns", None, "display.max_colwidth", 60)
    print(f"Insiderköp publicerade senaste {a.dagar} dagarna, minst {a.min_personer} personer "
          f"och {a.min_belopp:,.0f} kr:\n")
    print(res.drop(columns=["Personer", "Nyhet_rubrik", "Rod_flagga", "Teknik", "Diagram", "Varfor_ej_regel", "Poang_skal"], errors="ignore").to_string(index=False, float_format=lambda x: f"{x:,.1f}"))
    print("\nSparade i bevakning.csv (med namn och roller i kolumnen Personer)")


if __name__ == "__main__":
    main()
