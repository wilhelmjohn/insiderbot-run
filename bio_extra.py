"""Tillägg till Biotechscreen: sektorjämförelse (XBI, OMXSPI), studieregister (ClinicalTrials.gov) och branschkalender (CHMP, kongresser).
Läser bio_index.csv, bio_studier.csv, bio_studier_logg.csv och bransch_kalender.csv, alla skapade av extra_hamta.py / för hand med källa.
Saknas en fil returneras tomma resultat, så att sidan alltid går att bygga. Används av bio_bygg.py och bio_sf.py."""
import re
from pathlib import Path
import numpy as np, pandas as pd

HER = Path(__file__).resolve().parent
IDX_NAMN = {"XBI": "XBI (amerikansk biotech)", "^OMXSPI": "OMXSPI (Stockholmsbörsen)"}
AKTIV = {"RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING", "SUSPENDED"}
STATUS_SV = {"RECRUITING": "Rekryterar", "NOT_YET_RECRUITING": "Ej startad", "ENROLLING_BY_INVITATION": "Rekryterar (inbjudan)", "ACTIVE_NOT_RECRUITING": "Pågår, rekryterar ej",
             "COMPLETED": "Avslutad", "TERMINATED": "Avbruten", "SUSPENDED": "Pausad", "WITHDRAWN": "Återtagen", "NO_LONGER_AVAILABLE": "Ej längre tillgänglig", "UNKNOWN": "Okänd status"}

def csv_eller_tom(namn, **kw):
    f = HER / namn
    try: return pd.read_csv(f, **kw) if f.exists() else None
    except Exception: return None

# ---------------------------------------------------------------- sektor
def las_index():
    x = csv_eller_tom("bio_index.csv", parse_dates=["Datum"])
    if x is None or x.empty: return {}
    return {t: g.dropna(subset=["Close"]).drop_duplicates("Datum").set_index("Datum").Close.sort_index() for t, g in x.groupby("Ticker")}

def _asof(s, d):
    x = s[s.index <= d]
    return float(x.iloc[-1]) if len(x) else None

def rel_index(g, idx):
    """g = bolagets kurser (Datum, Close). Returnerar för varje index: avkastning 20/60/250 dagar, bolagets överavkastning (procentenheter) och hur stor del av dagsrörelserna indexet förklarar."""
    ut = {}
    c = g.set_index("Datum").Close
    for t, s in idx.items():
        try:
            rad = {}
            for n in (20, 60, 250):
                if len(c) <= n: rad[f"r{n}"] = rad[f"o{n}"] = None; continue
                d0, d1 = c.index[-1 - n], c.index[-1]
                a, b = _asof(s, d0), _asof(s, d1)
                ri = None if not a or not b else (b / a - 1) * 100
                rc = (c.iloc[-1] / c.iloc[-1 - n] - 1) * 100
                rad[f"r{n}"] = None if ri is None else round(ri, 1); rad[f"o{n}"] = None if ri is None else round(rc - ri, 1)
            sf = s.reindex(c.index.union(s.index)).ffill().reindex(c.index)
            rc_ = c.pct_change(); ri_ = sf.pct_change()
            best = None
            for lag in (0, 1):  # XBI stänger efter Stockholm: dagens USA-rörelse slår igenom nästa svenska dag
                z = pd.concat([rc_, ri_.shift(lag)], axis=1).dropna().tail(250)
                z = z[(z.iloc[:, 0].abs() < 0.6)]
                if len(z) < 120: continue
                cc = float(np.corrcoef(z.iloc[:, 0], z.iloc[:, 1])[0, 1]); beta = float(np.cov(z.iloc[:, 0], z.iloc[:, 1])[0, 1] / z.iloc[:, 1].var())
                if best is None or cc * cc > best[0]: best = (cc * cc, beta, lag)
            rad["r2"] = None if best is None else round(best[0] * 100); rad["beta"] = None if best is None else round(best[1], 2); rad["lag"] = None if best is None else best[2]
            ut[t] = rad
        except Exception:
            continue
    return ut

def index_dag(idx, dt):
    """Indexens rörelse (%) samma kalenderdag och föregående indexdag, för stora kursrörelser i dossiern."""
    ut = {}
    for t, s in idx.items():
        r = s.pct_change() * 100
        ut[t] = (float(r.get(dt, np.nan)), float(r[r.index < dt].iloc[-1]) if len(r[r.index < dt]) else np.nan)
    return ut

def sektor_rubrik(idx):
    """Rad för toppen av sidan: indexens avkastning."""
    ut = []
    for t, s in idx.items():
        if len(s) < 260: continue
        r = lambda n: round(float((s.iloc[-1] / s.iloc[-1 - n] - 1) * 100), 1)
        ut.append(dict(t=t, namn=IDX_NAMN.get(t, t), senast=str(s.index[-1].date()), kurs=round(float(s.iloc[-1]), 2), d1=r(1), r20=r(20), r60=r(60), r250=r(250)))
    return ut

# ---------------------------------------------------------------- studieregister
def _datum(s, slut=True):
    """ClinicalTrials.gov anger ÅÅÅÅ-MM eller ÅÅÅÅ-MM-DD. Månadsdatum räknas som månadens sista dag (senaste möjliga)."""
    s = str(s or "").strip()
    if not s or s == "nan": return None
    try:
        if len(s) == 7: return pd.Timestamp(s + "-01") + pd.offsets.MonthEnd(0)
        return pd.Timestamp(s)
    except Exception:
        return None

def las_studier():
    s = csv_eller_tom("bio_studier.csv")
    return None if s is None else s.fillna("")

def las_studielogg():
    return csv_eller_tom("bio_studier_logg.csv", parse_dates=["Datum"])

def studie_info(t, st, logg, idag, fas_prob):
    """Returnerar (tabell för sidan, händelser, varningar, signaler) ur studieregistret för ett bolag."""
    if st is None: return [], [], [], [], []
    x = st[st.Ticker == t]
    if x.empty: return [], [], [], [], []
    rader, ev, W, U = [], [], [], []
    for r in x.itertuples():
        d = _datum(r.PrimarSlut); upd = _datum(r.Uppdaterad)
        fas = r.Fas or ""; fas23 = bool(re.search(r"Fas [23]", fas))
        dg = None if d is None or r.Status not in AKTIV else int((d - idag).days)
        dg_ev = None if d is None else int((d - idag).days)
        rader.append(dict(nct=r.NCT, t=r.Titel[:140], fas=fas or "–", s=r.Status, ssv=STATUS_SV.get(r.Status, r.Status), ps=str(r.PrimarSlut or ""), pst=r.PrimarSlutTyp, dg=dg,
                          u=str(r.Uppdaterad or ""), res=str(r.HarResultat) == "True", til=r.Tillstand))
        if not fas23: continue
        nu = f"{r.NCT} ({fas}): {r.Titel[:70]}"
        if r.Status in ("ACTIVE_NOT_RECRUITING", "RECRUITING", "ENROLLING_BY_INVITATION", "NOT_YET_RECRUITING") and dg is not None:
            fk = "fas3" if "Fas 3" in fas else "fas2"
            if 0 <= dg <= 365:
                ev.append(dict(d=str(d.date()), h=f"{nu}: primärt slutdatum enligt studieregistret", typ="Planerat", k=f"https://clinicaltrials.gov/study/{r.NCT}",
                               s="ClinicalTrials.gov, beräknat datum; resultat kommer ofta 1-4 månader senare", dg=dg, fas=fas_prob[fk][0], p=fas_prob[fk][1]))
            elif -150 <= dg < 0 and r.Status == "ACTIVE_NOT_RECRUITING" and not (str(r.HarResultat) == "True"):
                ev.append(dict(d=str(idag.date()), h=f"{nu}: primärt slutdatum ({d.date()}) har passerat, resultat kan komma", typ="Planerat", k=f"https://clinicaltrials.gov/study/{r.NCT}",
                               s="ClinicalTrials.gov: rekryteringen är klar, men bolaget har inte redovisat resultat i registret", dg=0, fas=("Fas 3" if "Fas 3" in fas else "Fas 2"), p=None))
                U.append((f"Primärt slutdatum passerat: {r.NCT}", f"{fas}-studien har slutat rekrytera och dess primära slutdatum ({r.PrimarSlut}) har passerat utan att resultat lagts upp. Bolaget kan redovisa data när som helst (enligt ClinicalTrials.gov)."))
        if r.Status in ("TERMINATED", "SUSPENDED") and upd is not None and (idag - upd).days <= 150:
            hard = "Fas 3" in fas
            W.append((f"Studie {STATUS_SV[r.Status].lower()}: {r.NCT}", f"{fas}-studie markerad som {STATUS_SV[r.Status].lower()} i ClinicalTrials.gov (uppdaterad {r.Uppdaterad}): {r.Titel[:90]}. Läs bolagets förklaring.", "hard" if hard else "mjuk"))
        if r.Status == "WITHDRAWN" and upd is not None and (idag - upd).days <= 150:
            W.append((f"Studie återtagen: {r.NCT}", f"{fas}-studien återtogs innan den startade (uppdaterad {r.Uppdaterad}): {r.Titel[:90]}. Kan vara en omläggning, kolla bolagets förklaring.", "mjuk"))
        if str(r.HarResultat) == "True" and upd is not None and (idag - upd).days <= 60:
            U.append((f"Resultat upplagt: {r.NCT}", f"Resultat finns i ClinicalTrials.gov för {fas}-studien ({r.Titel[:80]}), uppdaterad {r.Uppdaterad}."))
    andr = []
    if logg is not None and len(logg):
        y = logg[(logg.Ticker == t) & (logg.Datum >= idag - pd.Timedelta(days=30))]
        for r in y.itertuples():
            andr.append(dict(d=str(r.Datum.date()), nct=r.NCT, typ=r.Typ, fran=str(r.Fran if r.Fran == r.Fran else ""), till=str(r.Till if r.Till == r.Till else "")))
    rader.sort(key=lambda r: (r["s"] not in AKTIV, r["dg"] is None, r["dg"] if r["dg"] is not None else 0))
    return rader[:12], ev, W, U, andr

# ---------------------------------------------------------------- flaggningar (FI Börsinformation)
def las_flaggning():
    return csv_eller_tom("bio_flaggning.csv", parse_dates=["Publicerad"])

def flagg_info(t, fl, idag):
    """Flaggningsmeddelanden ur FI:s Finanscentralen (extra_hamta.py flaggning). Tabellen anger bara publiceringstid och länk.
    Returnerar dict med senaste datum, dagar sedan, antal senaste 90 dagarna och länk, eller None."""
    if fl is None or fl.empty: return None
    x = fl[fl.Ticker == t].dropna(subset=["Publicerad"]).sort_values("Publicerad", ascending=False)
    if x.empty: return None
    sen = x.iloc[0]
    return dict(d=str(sen.Publicerad.date()), dg=int((idag - sen.Publicerad.normalize()).days), n90=int((x.Publicerad >= idag - pd.Timedelta(days=90)).sum()),
                lank=str(sen.Lank) if sen.Lank == sen.Lank else "", n=int(len(x)))

# ---------------------------------------------------------------- branschkalender
def las_bransch(idag):
    """Händelser som rör branschen i stort (EMA:s CHMP-möten, stora kongresser). Underhålls med källa i bransch_kalender.csv."""
    x = csv_eller_tom("bransch_kalender.csv", parse_dates=["Datum"])
    if x is None: return []
    x = x[x.Datum >= idag].sort_values("Datum")
    ut = []
    for r in x.itertuples():
        ut.append(dict(d=str(r.Datum.date()), dg=int((r.Datum - idag).days), typ=r.Typ, h=r.Handelse, omr=str(r.Omrade) if r.Omrade == r.Omrade else "", k=str(r.Kalla)))
    return ut
