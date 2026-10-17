#!/usr/bin/env python3
"""
Nyheter: bolagens pressmeddelanden och börsmeddelanden (Nasdaq Nordic, stockholmsbörsen
och First North Sweden), klassade efter typ, så att varje insiderköp kan sättas i
sitt sammanhang: vad hände i bolaget de senaste dagarna före köpet?

Typer (högst prioritet först; ett köp får den högsta typen som finns i fönstret):
  vinstvarning    vinstvarning, sänkt prognos, nedskrivning
  nyemission      nyemission, företrädesemission, riktad emission
  m_and_a         förvärv, uppköpserbjudande, fusion, avyttring
  ledning         VD/CFO-byte, ny ledning
  prognoshojning  höjd prognos eller uppjusterade mål
  rapport         delårs-, kvartals-, halvårs- eller bokslutsrapport
  annan_nyhet     övriga nyheter
  ingen_nyhet     inget publicerat (bolaget finns i datan)
  okand           bolaget saknas i datan (t.ex. Spotlight eller NGM)

Kör (på GitHub Actions; Nasdaqs API nås inte från alla miljöer):
  python nyheter.py --fran 2024-01-01          # fyller på historiken dag för dag
  python nyheter.py --fran 2024-01-01 --max-dagar 20   # test

Utdata:
  nyheter.csv        Publicerat, Bolag, Bolag_id, Marknad, Kategori, Rubrik, Typ, Sprak
  nyhetsbolag.csv    alla bolag som setts i datan (för täckning), Bolag_id, Bolag, Marknad
  nyheter_dagar.txt  vilka dagar som är färdighämtade

Bruset (antal aktier och röster, återköp, bolagsstämmor, insiderhandel, derivat osv.)
sparas inte: insiderhandeln finns redan från FI.
Tidsstämplar är svensk tid. En nyhet räknas som känd från nästa handelsdag, så att
ingen information från efter köpet läcker in i backtestet.
"""
import argparse
import re
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import checkpoint
import insider_bot as ib

MAPP = Path(__file__).resolve().parent
NYHETER = MAPP / "nyheter.csv"
BOLAG = MAPP / "nyhetsbolag.csv"
DAGAR = MAPP / "nyheter_dagar.txt"

URL = "https://api.news.eu.nasdaq.com/news/query.action"
MARKNADER = ["Main Market, Stockholm", "First North Sweden"]
SIDSTORLEK = 100
CHECKPOINT_DAGAR = 1    # mellanlagra i git efter varje hämtad dag
KOLUMNER = ["Publicerat", "Bolag", "Bolag_id", "Marknad", "Kategori", "Rubrik", "Typ", "Sprak"]

PRIORITET = ["vinstvarning", "nyemission", "m_and_a", "ledning", "prognoshojning", "rapport", "annan_nyhet"]

# Sparas inte alls
BRUS = re.compile(
    r"rösträtter|antal aktier och röster|antalet aktier och röster|voting rights|number of shares and votes|"
    r"shares and votes|aktier och röster|återköp|buy-?back|repurchase|derivat|securitized|mini future|"
    r"leverage certificate|insider transactions|executive transactions|transaktioner (av|i)|"
    r"managers'? transactions|pdmr|kallelse|notice (of|to)|årsstämma|bolagsstämma|annual general|"
    r"\bagm\b|\begm\b|valberedning|nomination committee|financial calendar|finansiell kalender|"
    r"inbjudan|invitation|listing of|upptagande till handel|admission to trading|"
    r"stabiliseringsåtgärder|stabilisation|kursstabilisering|offentliggörande av.*revisionsberättelse|"
    r"bolagsordning|articles of association|capital and votes|kapital och röster|"
    r"changes? in the number|ändring(ar)? i antal|förändring(ar)? (av|i) antal",
    re.I)

VINSTVARNING = re.compile(
    r"vinstvarning|profit warning|resultatvarning|negativ resultatavvikelse|negative (earnings|profit) "
    r"(deviation|warning)|nedskrivning|impairment|"
    r"(sänker|sänkt|justerar ned|drar tillbaka|dras tillbaka|lowers?|lowered|withdraws?|cuts?|reduces?)\b.*"
    r"(prognos|finansiella mål|utsikter|guidance|outlook|forecast|targets?)|"
    r"lägre än (tidigare )?(kommunicerad|förväntad|förväntat)|below (earlier|previously|expectations)", re.I)
NYEMISSION = re.compile(
    r"nyemission|företrädesemission|riktad (nyemission|emission)|emission av|rights issue|"
    r"directed (new )?(share )?issue|new share issue|share issue|private placement|capital raise|"
    r"kapitalanskaffning|företrädesrätt|fulltecknad|oversubscribed|konvertibel|convertible", re.I)
M_AND_A = re.compile(
    r"förvärv|förvärvar|acquires?|acquisition|acquired|uppköpserbjudande|offentligt erbjudande|"
    r"public (cash )?offer|takeover|budpliktig|\bbud\b|fusion|merger|avyttrar|avyttring|divests?|"
    r"divestment|säljer (hela )?(verksamhet|dotterbolag|rörelsen)", re.I)
LEDNING = re.compile(
    r"management change|ny (vd|cfo|ekonomichef|styrelseordförande)|vd (lämnar|avgår|utses)|"
    r"(lämnar|avgår) (som )?(vd|cfo)|utser .*(vd|cfo|ekonomichef)|appoints?|new (ceo|cfo|chair)|"
    r"(ceo|cfo) (resigns|to leave|steps down|leaves)|rekrytering av|successor|efterträdare|"
    r"förändring(ar)? i (koncern)?ledning|changes? (in|to) (the )?(executive|group) management", re.I)
PROGNOSHOJNING = re.compile(
    r"(höjer|uppjusterar|höjt|raises?|raised|increases?|upgrades?)\b.*"
    r"(prognos|finansiella mål|utsikter|guidance|outlook|forecast|targets?)", re.I)
RAPPORT = re.compile(
    r"delårsrapport|kvartalsrapport|bokslutskommuniké|halvårsrapport|halvårsredogörelse|"
    r"interim (financial )?report|year-end (report|release)|half-year (financial )?report|"
    r"quarterly report|\bq[1-4]\b.*(rapport|report|resultat|results)|"
    r"(rapport|report|results?)\b.*\bq[1-4]\b|financial (report|statement)|"
    r"(first|second|third|fourth) quarter|(första|andra|tredje|fjärde) kvartalet|full-?year results|"
    r"preliminära?.*(resultat|finansiella)|preliminary.*(results|financial)", re.I)
RAPPORTKATEGORI = re.compile(r"report|rapport|bokslut", re.I)


_MANADER = {"januari": 1, "februari": 2, "mars": 3, "april": 4, "maj": 5, "juni": 6, "juli": 7, "augusti": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
            "january": 1, "february": 2, "march": 3, "may": 5, "june": 6, "july": 7, "august": 8, "october": 10}
_MAN = "|".join(sorted(_MANADER, key=len, reverse=True))
_DAG_MAN = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th|:e)?\s+({_MAN})\b(?:\s+(\d{{4}}))?", re.I)
_MAN_DAG = re.compile(rf"\b({_MAN})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*[-–])(?:,?\s+(\d{{4}}))?", re.I)
_AVSIKT = re.compile(r"bjuder in|inbjud|invit|publicerar|to publish|will publish|kommer att publicera|presenterar|presents?\b|offentliggör|"
                     r"will be (published|presented)|publiceras|offentliggörs|telefonkonferens|conference call|webcast|webbsänd|webcast", re.I)


def rapportdatum_i_rubrik(rubrik, publicerat):
    """Datum för en kommande rapport som bolaget anger i en rubrik som 'Trelleborg publicerar rapport för tredje kvartalet den 23 oktober'
    eller 'Lyko bjuder in till presentation av Q3-rapporten den 22 oktober'. Kräver att rubriken handlar om en rapport och uttrycker avsikt
    (publicerar, bjuder in, presenterar). Returnerar Timestamp eller None. Datum utan årtal räknas som nästa förekomst efter publiceringsdagen."""
    r = str(rubrik)
    if not _AVSIKT.search(r) or not (RAPPORT.search(r) or re.search(r"\brapport|\breport", r, re.I)):
        return None
    pub = pd.Timestamp(publicerat).normalize()
    kand = []
    for m in _DAG_MAN.finditer(r):
        kand.append((int(m.group(1)), _MANADER[m.group(2).lower()], m.group(3)))
    for m in _MAN_DAG.finditer(r):
        kand.append((int(m.group(2)), _MANADER[m.group(1).lower()], m.group(3)))
    ut = []
    for dag, man, ar in kand:
        try:
            d = pd.Timestamp(int(ar) if ar else pub.year, man, dag)
            if not ar and d < pub - pd.Timedelta(days=3):
                d = pd.Timestamp(pub.year + 1, man, dag)
        except ValueError:
            continue
        if pub - pd.Timedelta(days=1) <= d <= pub + pd.Timedelta(days=130):
            ut.append(d)
    return min(ut) if ut else None


def klassa(rubrik, kategori="") -> str:
    """Typ av nyhet, eller 'brus' för sådant som inte ska sparas."""
    text = f"{rubrik} {kategori}"
    if BRUS.search(text):
        # en rapport i kategorin 'Kvartalsrapport' ska inte förloras på ett bristande ord
        if not (RAPPORTKATEGORI.search(str(kategori)) and not re.search(r"inbjudan|invitation|kallelse|notice", text, re.I)):
            return "brus"
    if VINSTVARNING.search(text):
        return "vinstvarning"
    if NYEMISSION.search(text):
        return "nyemission"
    if M_AND_A.search(text):
        return "m_and_a"
    if LEDNING.search(text):
        return "ledning"
    if PROGNOSHOJNING.search(text):
        return "prognoshojning"
    if RAPPORT.search(text) or RAPPORTKATEGORI.search(str(kategori)):
        return "rapport"
    return "annan_nyhet"


# ---------------------------------------------------------------- hämtning
PROVA2 = False
DIAGNOS = []                       # skrivs till nyheter_diagnos.txt (första anropen och alla fel, max 60 rader)
DIAGFIL = MAPP / "nyheter_diagnos.txt"


def _diag(rad: str) -> None:
    if len(DIAGNOS) < 60:
        DIAGNOS.append(rad)


def hamta_sida(session, params: dict, forsok: int = 5) -> dict:
    """GET med omförsök och växande väntetid."""
    senaste = None
    for n in range(forsok):
        t0 = time.time()
        try:
            r = session.get(URL, params=params, timeout=60)
            sek = time.time() - t0
            if len(DIAGNOS) < 12 or r.status_code != 200:
                _diag(f"{params.get('fromDate')} {params.get('market')} start={params.get('start')} försök {n+1}: HTTP {r.status_code}, {sek:.1f} s, {len(r.content)} byte, början: {r.text[:300]!r}")
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            _diag(f"{params.get('fromDate')} {params.get('market')} försök {n+1}: {e.__class__.__name__}: {str(e)[:200]} efter {time.time()-t0:.1f} s")
            senaste = e
            time.sleep(min(5 * 2 ** n, 60))
    raise senaste


def poster_ur_svar(data) -> list:
    """API:t svarar {"results": {"item": [ ... ]}} (item är ett objekt om bara ett träff)."""
    res = data.get("results") if isinstance(data, dict) else data
    if isinstance(res, dict):
        res = res.get("item", [])
    if isinstance(res, dict):
        res = [res]
    return [p for p in (res or []) if isinstance(p, dict)]


def PROVA2_LISTA(kor, bas) -> None:
    """Provar filter som kan dela upp sökningen så att varje del når längre bakåt."""
    for foretag in ("Castellum AB", "Castellum", "Lagercrantz Group AB", "Asker Healthcare Group AB"):
        kor(f"H company={foretag} start=0", {"company": foretag})
        kor(f"H company={foretag} start=300", {"company": foretag, "start": 300})
    kor("I company=Castellum AB djup start=1000", {"company": "Castellum AB", "start": 1000})
    kor("J categoryId=55", {"categoryId": 55})
    kor("J categoryId=55 start=5000", {"categoryId": 55, "start": 5000})
    kor("K cnsCategory=Delårsrapport", {"cnsCategory": "Delårsrapport"})
    kor("K cnsCategory=Delårsrapport start=1000", {"cnsCategory": "Delårsrapport", "start": 1000})
    kor("K cnsCategory=Delårsrapport start=3000", {"cnsCategory": "Delårsrapport", "start": 3000})
    kor("L cnsCategory=Insiderinformation start=3000", {"cnsCategory": "Insiderinformation", "start": 3000})
    kor("M text=vinstvarning", {"text": "vinstvarning"})
    kor("N searchText=vinstvarning", {"searchText": "vinstvarning"})
    kor("O headline=Delårsrapport start=5000", {"headline": "Delårsrapport", "start": 5000})
    kor("P cnsTypeId=1", {"cnsTypeId": 1})
    for off in (6000, 8000, 10000, 12000, 15000, 18000):
        kor(f"G start={off}", {"start": off})


def prova(session) -> None:
    """Diagnos: provar parametervarianter och djup bläddring, skriver sammanfattning till nyheter_diagnos.txt."""
    bas = {"globalGroup": "company", "displayLanguage": "sv", "timeZone": "CET",
           "dateMask": "yyyy-MM-dd HH:mm:ss", "limit": 100, "start": 0, "market": MARKNADER[0]}
    def kor(namn, extra):
        p = dict(bas); p.update(extra)
        try:
            t0 = time.time(); r = session.get(URL, params=p, timeout=60)
            it = poster_ur_svar(r.json())
            pub = sorted(str(x.get("published")) for x in it if x.get("published"))
            rad = (f"{namn}: HTTP {r.status_code}, {len(it)} poster, {time.time()-t0:.1f} s, "
                   f"published {pub[0] if pub else '-'} .. {pub[-1] if pub else '-'}, "
                   f"första id {it[0].get('disclosureId') if it else '-'}; nycklar {sorted(it[0].keys())[:14] if it else '-'}")
        except Exception as e:
            rad = f"{namn}: FEL {e.__class__.__name__}: {str(e)[:150]}"
        print(rad); DIAGNOS.append(rad)
    if PROVA2:
        PROVA2_LISTA(kor, bas)
        DIAGFIL.write_text("\n".join(DIAGNOS) + "\n")
        return
    dag = "2024-03-12"
    kor("A nuvarande fromDate/toDate", {"fromDate": dag, "toDate": dag})
    kor("B med klockslag", {"fromDate": dag + " 00:00:00", "toDate": dag + " 23:59:59"})
    kor("C dir=DESC + countResults", {"fromDate": dag, "toDate": dag, "dir": "DESC", "countResults": "false"})
    kor("D fromDate/toDate utan globalGroup", {"fromDate": dag, "toDate": dag, "globalGroup": "exchangeNotice", "globalName": "NordicAllMarkets"})
    kor("E fromDate=2021-01-01 toDate=2021-01-31", {"fromDate": "2021-01-01", "toDate": "2021-01-31"})
    kor("F intervall 2024-03-12..2024-03-13", {"fromDate": dag, "toDate": "2024-03-13"})
    for off in (0, 100, 1000, 5000, 20000, 50000, 100000):
        kor(f"G djup bläddring start={off}", {"start": off})
    DIAGFIL.write_text("\n".join(DIAGNOS) + "\n")


def hamta_dag(session, dag: date, marknad: str, sida: int = SIDSTORLEK, max_sidor: int = 200) -> list:
    """Alla meddelanden för en marknad och dag. Bläddrar tills sidan är tom, eftersom
    API:t kan ge färre poster per sida än begärt."""
    ut, start, sett = [], 0, set()
    for _ in range(max_sidor):
        params = {
            "globalGroup": "company", "displayLanguage": "sv", "timeZone": "CET",
            "dateMask": "yyyy-MM-dd HH:mm:ss", "limit": sida, "start": start,
            "fromDate": dag.isoformat(), "toDate": dag.isoformat(), "market": marknad,
        }
        data = hamta_sida(session, params)
        sidan = poster_ur_svar(data)
        if not sidan:
            break
        # skydd mot att API:t ignorerar 'start' och ger samma sida igen (annars 200 sidor per dag)
        nya_nycklar = {(p.get("published"), p.get("company"), p.get("headline")) for p in sidan} - sett
        if not nya_nycklar:
            break
        sett |= nya_nycklar
        ut.extend(sidan)
        start += len(sidan)
        time.sleep(0.5)
    return ut


def till_rader(poster: list) -> pd.DataFrame:
    """Gör om API-svaret till sparade rader, klassade och utan brus."""
    rader = []
    for p in poster:
        if not isinstance(p, dict):      # skydd: okänt svarsformat hoppas över
            continue
        rubrik = str(p.get("headline") or "").strip()
        kat = str(p.get("cnsCategory") or "").strip()
        bolag = str(p.get("company") or "").strip()
        if not bolag or not p.get("published"):
            continue
        rader.append({
            "Publicerat": p["published"], "Bolag": bolag, "Marknad": p.get("market", ""),
            "Kategori": kat, "Rubrik": rubrik, "Sprak": p.get("language", ""),
            "Typ": klassa(rubrik, kat),
        })
    df = pd.DataFrame(rader, columns=["Publicerat", "Bolag", "Marknad", "Kategori", "Rubrik", "Sprak", "Typ"])
    if df.empty:
        return df.assign(Bolag_id=pd.Series(dtype=str))[KOLUMNER]
    df["Bolag_id"] = ib.bolagsnyckel(df.rename(columns={"Bolag": "Emittent"}))
    return df[KOLUMNER]


def slå_ihop(gamla: pd.DataFrame, nya: pd.DataFrame) -> pd.DataFrame:
    """Lägger ihop och tar bort dubbletter (svensk och engelsk version av samma
    meddelande, samt omkörningar). Den svenska versionen föredras."""
    alla = pd.concat([gamla, nya], ignore_index=True)
    if alla.empty:
        return alla
    alla["_sv"] = (alla["Sprak"] != "sv").astype(int)
    alla = alla.sort_values(["Publicerat", "Bolag_id", "_sv"])
    alla = alla.drop_duplicates(["Bolag_id", "Publicerat", "Typ"], keep="first")
    return alla.drop(columns="_sv").reset_index(drop=True)


def las_dagar() -> set:
    return set(DAGAR.read_text().split()) if DAGAR.exists() else set()


def uppdatera(fran: date, max_dagar: int = 0, sida: int = SIDSTORLEK, till: date = None,
              max_minuter: float = 0, bara_kvar: bool = False) -> None:
    gamla = pd.read_csv(NYHETER, keep_default_na=False, dtype=str) if NYHETER.exists() else pd.DataFrame(columns=KOLUMNER)
    bolag = pd.read_csv(BOLAG, keep_default_na=False, dtype=str) if BOLAG.exists() else pd.DataFrame(columns=["Bolag_id", "Bolag", "Marknad"])
    klara = las_dagar()

    sista = date.today() - timedelta(days=1)          # idag är inte färdig
    if till is not None:
        sista = min(sista, till)
    dagar = [fran + timedelta(days=i) for i in range((sista - fran).days + 1)]
    dagar = [d for d in dagar if d.isoformat() not in klara]
    if max_dagar:
        dagar = dagar[:max_dagar]
    print(f"{len(dagar)} dagar att hämta")
    if bara_kvar:
        return
    start_tid = time.time()

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (insiderbot)", "Accept": "application/json"})
    nya, fel_i_rad, hamtade = [], 0, 0
    t_dag = time.time()

    def spara():
        nonlocal gamla, bolag, nya
        if not nya:
            return
        ny = pd.concat(nya, ignore_index=True)
        # täckning: alla bolag, även de vars nyheter bara var brus
        b = ny[["Bolag_id", "Bolag", "Marknad"]].drop_duplicates("Bolag_id")
        bolag = pd.concat([bolag, b], ignore_index=True).drop_duplicates("Bolag_id", keep="first")
        bolag.to_csv(BOLAG, index=False)
        gamla = slå_ihop(gamla, ny[ny["Typ"] != "brus"])
        gamla.to_csv(NYHETER, index=False)
        DAGAR.write_text("\n".join(sorted(klara)) + "\n")
        nya = []

    try:
        for d in dagar:
            if max_minuter and (time.time() - start_tid) / 60 >= max_minuter:
                print(f"Tidsgräns på {max_minuter:.0f} min nådd, sparar och avslutar. Kör igen för resten.")
                break
            try:
                poster = []
                for m in MARKNADER:
                    poster += hamta_dag(session, d, m, sida)
                rader = till_rader(poster)
                fel_i_rad = 0
            except Exception as e:
                fel_i_rad += 1
                print(f"  {d}: fel ({e.__class__.__name__}: {str(e)[:120]}) {fel_i_rad}/5")
                if fel_i_rad >= 5:
                    print("För många fel i rad, avbryter. Det som hämtats är sparat.")
                    break
                continue
            sek = time.time() - t_dag; t_dag = time.time()
            if sek > 30:
                print(f"  {d}: långsam dag, {sek:.0f} s, {len(poster)} poster")
            nya.append(rader)
            klara.add(d.isoformat())
            hamtade += 1
            if hamtade % 20 == 0:
                spara()
                tot = (time.time() - start_tid) / 60
                print(f"  {d}: {hamtade}/{len(dagar)} dagar, {len(gamla)} nyheter sparade, {tot:.1f} min ({tot*60/hamtade:.1f} s/dag)")
            if hamtade % CHECKPOINT_DAGAR == 0 and hamtade < len(dagar):
                spara()
                checkpoint.spara_i_git([NYHETER, BOLAG, DAGAR], f"Mellanlagring nyheter {d.isoformat()}")
    finally:
        spara()   # spara det som hunnit hämtas även vid oväntat fel
        if DIAGNOS:
            DIAGFIL.write_text("\n".join(DIAGNOS) + "\n")
    print(f"Klart: {len(gamla)} nyheter i {NYHETER.name}, {len(bolag)} bolag i {BOLAG.name}")


# ---------------------------------------------------------------- användning
def uppdatera_bakat(fran: date, sida: int = SIDSTORLEK, max_sidor: int = 400) -> None:
    """API:t ignorerar datumparametrarna och ger alltid de senaste meddelandena först, men
    går att bläddra bakåt med 'start' (djupet är begränsat, HTTP 400 efter ett tak). Bläddrar
    därför bakåt per marknad tills äldsta meddelandet är äldre än 'fran' eller taket nås,
    och filtrerar på publiceringsdatum lokalt."""
    gamla = pd.read_csv(NYHETER, keep_default_na=False, dtype=str) if NYHETER.exists() else pd.DataFrame(columns=KOLUMNER)
    bolag = pd.read_csv(BOLAG, keep_default_na=False, dtype=str) if BOLAG.exists() else pd.DataFrame(columns=["Bolag_id", "Bolag", "Marknad"])
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (insiderbot)", "Accept": "application/json"})
    alla, aldsta_per_marknad = [], {}
    for m in MARKNADER:
        poster, start, aldsta = [], 0, None
        for _ in range(max_sidor):
            params = {"globalGroup": "company", "displayLanguage": "sv", "timeZone": "CET",
                      "dateMask": "yyyy-MM-dd HH:mm:ss", "limit": sida, "start": start, "market": m}
            try:
                data = hamta_sida(session, params, forsok=2)
            except Exception as e:
                print(f"  {m}: stoppar vid start={start} ({e.__class__.__name__}: {str(e)[:80]}), taket för bläddring är nått")
                break
            sidan = poster_ur_svar(data)
            if not sidan:
                break
            poster.extend(sidan)
            start += len(sidan)
            pub = [str(p.get("published"))[:10] for p in sidan if p.get("published")]
            if pub:
                aldsta = min(pub)
                if aldsta < fran.isoformat():
                    break
            time.sleep(0.2)
        aldsta_per_marknad[m] = aldsta
        print(f"  {m}: {len(poster)} meddelanden, äldsta {aldsta}")
        alla += poster
    rader = till_rader(alla)
    rader = rader[rader["Publicerat"].str[:10] >= fran.isoformat()]
    if not rader.empty:
        b = rader[["Bolag_id", "Bolag", "Marknad"]].drop_duplicates("Bolag_id")
        bolag = pd.concat([bolag, b], ignore_index=True).drop_duplicates("Bolag_id", keep="first")
        bolag.to_csv(BOLAG, index=False)
        gamla = slå_ihop(gamla, rader[rader["Typ"] != "brus"])
        gamla.to_csv(NYHETER, index=False)
    # dagar täckta av båda marknaderna (äldsta datum som alla nådde) fram till igår
    if all(v for v in aldsta_per_marknad.values()):
        start_dag = max(date.fromisoformat(v) for v in aldsta_per_marknad.values())
        start_dag = max(start_dag + timedelta(days=1), fran)
        klara = las_dagar()
        d = start_dag
        while d < date.today():
            klara.add(d.isoformat()); d += timedelta(days=1)
        DAGAR.write_text("\n".join(sorted(klara)) + "\n")
    print(f"Klart: {len(gamla)} nyheter i {NYHETER.name}, {len(bolag)} bolag i {BOLAG.name}")


BOLAGSFIL = MAPP / "nyheter_bolag_klara.txt"      # bolagsnamn som hämtats per bolag (för omstart)


def uppdatera_per_bolag(fran: date, max_minuter: float = 200, sida: int = SIDSTORLEK, max_sidor: int = 100,
                        checkpoint_var: int = 25) -> None:
    """Filtret company=<exakt namn> fungerar och bläddringen per bolag når mycket längre bakåt än den
    globala (taket är ca 10 000 träffar per sökning). Hämtar historik per bolag för alla emittenter i
    insiderkop.csv. Sparar och checkar in var 'checkpoint_var':e bolag; omstart hoppar över gjorda bolag."""
    rå = pd.read_csv(MAPP / "insiderkop.csv", usecols=["Emittent"], dtype=str)["Emittent"].dropna().unique()
    # FI-namnen har ibland prefix/suffix som nyhetsflödet inte har: "*Bolag AB (publ)" -> "Bolag AB"
    namn = sorted({re.sub(r"\s*\(publ\)", "", x).lstrip("*").strip() for x in rå} - {""})
    klara = set(BOLAGSFIL.read_text().splitlines()) if BOLAGSFIL.exists() else set()
    kvar = [n for n in namn if n not in klara]
    print(f"{len(namn)} bolag, {len(kvar)} kvar att hämta")
    gamla = pd.read_csv(NYHETER, keep_default_na=False, dtype=str) if NYHETER.exists() else pd.DataFrame(columns=KOLUMNER)
    bolag = pd.read_csv(BOLAG, keep_default_na=False, dtype=str) if BOLAG.exists() else pd.DataFrame(columns=["Bolag_id", "Bolag", "Marknad"])
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (insiderbot)", "Accept": "application/json"})
    start_tid, nya, utan_traff, hunna = time.time(), [], [], 0

    def spara():
        nonlocal gamla, bolag, nya
        if nya:
            ny = pd.concat(nya, ignore_index=True)
            if not ny.empty:
                b = ny[["Bolag_id", "Bolag", "Marknad"]].drop_duplicates("Bolag_id")
                bolag = pd.concat([bolag, b], ignore_index=True).drop_duplicates("Bolag_id", keep="first")
                bolag.to_csv(BOLAG, index=False)
                gamla = slå_ihop(gamla, ny[(ny["Typ"] != "brus") & (ny["Publicerat"].str[:10] >= fran.isoformat())])
                gamla.to_csv(NYHETER, index=False)
            nya = []
        BOLAGSFIL.write_text("\n".join(sorted(klara)) + "\n")

    try:
        for n in kvar:
            if (time.time() - start_tid) / 60 >= max_minuter:
                print(f"Tidsgräns på {max_minuter:.0f} min nådd. Kör igen för resten.")
                break
            poster, start = [], 0
            for _ in range(max_sidor):
                params = {"globalGroup": "company", "displayLanguage": "sv", "timeZone": "CET",
                          "dateMask": "yyyy-MM-dd HH:mm:ss", "limit": sida, "start": start, "company": n}
                try:
                    sidan = poster_ur_svar(hamta_sida(session, params, forsok=2))
                except Exception:
                    break          # taket (HTTP 400) eller bestående fel: ta det som hämtats
                if not sidan:
                    break
                poster += [p for p in sidan if p.get("company") == n] or []
                start += len(sidan)
                pub = [str(p.get("published"))[:10] for p in sidan if p.get("published")]
                if pub and min(pub) < fran.isoformat():
                    break
                time.sleep(0.15)
            if not poster:
                utan_traff.append(n)
            nya.append(till_rader(poster)); klara.add(n); hunna += 1
            if hunna % checkpoint_var == 0:
                spara()
                tot = (time.time() - start_tid) / 60
                print(f"  {hunna}/{len(kvar)} bolag, {len(gamla)} nyheter, {tot:.1f} min, utan träff hittills: {len(utan_traff)}")
                checkpoint.spara_i_git([NYHETER, BOLAG, DAGAR, BOLAGSFIL], f"Mellanlagring nyheter per bolag ({hunna} bolag)")
    finally:
        spara()
    if utan_traff:
        DIAGFIL.write_text("Bolag utan träff på exakt namn (" + str(len(utan_traff)) + "):\n" + "\n".join(utan_traff[:400]) + "\n")
    print(f"Klart: {len(gamla)} nyheter i {NYHETER.name}, {len(bolag)} bolag; {len(utan_traff)} bolagsnamn utan träff")


def las_nyheter(path=NYHETER) -> pd.DataFrame:
    n = pd.read_csv(path, keep_default_na=False, dtype=str)
    n["Publicerat"] = pd.to_datetime(n["Publicerat"])
    return n


def nyhetstillstand(px: pd.DataFrame, nyheter: pd.DataFrame, bolag: pd.DataFrame,
                    bolag_ticker: dict, fonster: int = 10):
    """Tillståndsmatris (datum x ticker) för backtestet: högsta nyhetstyp under de
    senaste `fonster` handelsdagarna. En nyhet är känd först handelsdagen efter
    publiceringsdagen (inget läckage in i köpdagen). Kod -1 = bolaget saknas i datan,
    0 = ingen nyhet, 1.. = plats i PRIORITET."""
    dates = px.index
    kol = {tk: i for i, tk in enumerate(px.columns)}
    mat = pd.DataFrame(0, index=dates, columns=px.columns, dtype=int)
    n = nyheter.assign(Ticker=nyheter["Bolag_id"].map(bolag_ticker))
    n = n[n["Ticker"].isin(kol)]
    for kod in range(len(PRIORITET), 0, -1):          # lägst prioritet först, högst skriver över
        e = n[n["Typ"] == PRIORITET[kod - 1]]
        if e.empty:
            continue
        pos = dates.searchsorted(e["Publicerat"].dt.normalize().to_numpy(), side="right")
        kol_i = e["Ticker"].map(kol).to_numpy()
        ok = pos < len(dates)
        ind = np.zeros(px.shape, dtype=float)
        ind[pos[ok], kol_i[ok]] = 1.0
        ind = pd.DataFrame(ind, index=dates, columns=px.columns)
        har = ind.rolling(fonster, min_periods=1).max() > 0
        mat = mat.where(~har, kod)
    tackta = set(bolag["Bolag_id"])
    okanda = [tk for tk in px.columns
              if not any(bid in tackta for bid, t in bolag_ticker.items() if t == tk)]
    if okanda:
        mat[okanda] = -1
    etik = {-1: "okand", 0: "ingen_nyhet", **{i + 1: t for i, t in enumerate(PRIORITET)}}
    return mat, etik


def nyhet_fore(nyheter: pd.DataFrame, bolag_id: str, dag, dagar: int = 14):
    """Högsta nyhetstyp, datum och rubrik för ett bolag de senaste `dagar` dagarna
    fram till och med `dag`. Returnerar (typ, datum, rubrik) eller (None, None, None)."""
    dag = pd.Timestamp(dag)
    n = nyheter[(nyheter["Bolag_id"] == bolag_id) & (nyheter["Publicerat"] <= dag + pd.Timedelta(days=1))
                & (nyheter["Publicerat"] > dag - pd.Timedelta(days=dagar))]
    if n.empty:
        return None, None, None
    n = n.assign(_p=n["Typ"].map({t: i for i, t in enumerate(PRIORITET)})).sort_values(["_p", "Publicerat"], ascending=[True, False])
    r = n.iloc[0]
    return r["Typ"], r["Publicerat"].date().isoformat(), r["Rubrik"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fran", default="2021-01-01")
    ap.add_argument("--till", default=None, help="Sista dag att hämta (YYYY-MM-DD), t.ex. 2021-12-31")
    ap.add_argument("--max-dagar", type=int, default=0, help="Hämta max N dagar (test)")
    ap.add_argument("--sida", type=int, default=SIDSTORLEK)
    ap.add_argument("--max-minuter", type=float, default=0, help="Avsluta ordnat efter N minuter (sparar först)")
    ap.add_argument("--per-bolag", action="store_true", help="Hämta historik per bolag (company-filter), når längre bakåt")
    ap.add_argument("--gammal", action="store_true", help="Gamla dag-för-dag-hämtningen (fungerar inte: API:t ignorerar datum)")
    ap.add_argument("--prova", nargs="?", const="1", default="", help="Diagnos av API:t (1 = datum/djup, 2 = filter), skriver nyheter_diagnos.txt")
    ap.add_argument("--kvar", action="store_true", help="Skriv bara ut hur många dagar som återstår (sista raden är ett tal)")
    a = ap.parse_args()
    if a.prova:
        global PROVA2
        PROVA2 = a.prova == "2"
        sess = requests.Session()
        sess.headers.update({"User-Agent": "Mozilla/5.0 (insiderbot)", "Accept": "application/json"})
        prova(sess); return
    if a.per_bolag:
        uppdatera_per_bolag(date.fromisoformat(a.fran), a.max_minuter or 200, a.sida); return
    if not a.gammal:
        if a.kvar:
            print("0 dagar att hämta"); return
        uppdatera_bakat(date.fromisoformat(a.fran), a.sida); return
    uppdatera(date.fromisoformat(a.fran), a.max_dagar, a.sida,
              date.fromisoformat(a.till) if a.till else None, a.max_minuter, a.kvar)


if __name__ == "__main__":
    main()
