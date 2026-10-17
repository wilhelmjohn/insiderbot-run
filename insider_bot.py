#!/usr/bin/env python3
"""
Insiderbot: samlar insynshandel från Finansinspektionen (marknadssok.fi.se)
i en historikfil som växer för varje körning, och hittar aktieköp som
matchar dina regler.

Filer (sparas i samma mapp som skriptet):
  insiderhandel.csv    all insynshandel, alla transaktionstyper (rådata, rensad)
  insiderkop.csv       alla aktieköp (Förvärv) med Belopp och flagga för
                       interna överföringar. Bra underlag för backtest.
  hamtade_dagar.txt    vilka publiceringsdagar som är hämtade och kompletta
  traffar.csv          senaste träffarna enligt regeln
  kluster.csv          bolag där flera olika personer köpt (senaste 30 dagarna)

Kör:
  python insider_bot.py                    # uppdatera historik + visa träffar senaste 3 dagarna
  python insider_bot.py --fran 2024-01-01  # första gången: hämta tillbaka i tiden (tar en stund)
  python insider_bot.py --dagar 14         # visa träffar för senaste 14 dagarna
  python insider_bot.py --bara-analys      # ingen hämtning, analysera bara sparad historik

Krav: pip install requests pandas lxml

VIKTIGT vid backtest: använd kolumnen Publiceringsdatum (när marknaden fick
veta om köpet), inte Transaktionsdatum. Annars använder du information som
inte var känd ännu.
"""
import argparse
import re
import time
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

import checkpoint

BASE = "https://marknadssok.fi.se/Publiceringsklient/sv-SE/Search/Search"
MIN_BELOPP_SEK = 10_000
SIDSTORLEK = 10  # FI visar 10 rader per sida
STANDARD_START = "2021-01-01"

MAPP = Path(__file__).resolve().parent
HISTORIK = MAPP / "insiderhandel.csv"
KOPLISTA = MAPP / "insiderkop.csv"
DAGFIL = MAPP / "hamtade_dagar.txt"
TRAFFAR = MAPP / "traffar.csv"
KLUSTER = MAPP / "kluster.csv"

KOLUMNER = [
    "Publiceringsdatum", "Emittent", "Person i ledande ställning", "Befattning",
    "Närstående", "Karaktär", "Instrumentnamn", "Instrumenttyp", "ISIN",
    "Transaktionsdatum", "Volym", "Volymsenhet", "Pris", "Valuta",
]


# ---------------------------------------------------------------- hämtning
def hamta_url(session: requests.Session, url: str, params: dict, forsok: int = 5):
    """GET med omförsök och växande väntetid. FI bryter ibland anslutningen
    (ConnectionError) om man hämtar för snabbt eller från molnservrar."""
    senaste = None
    for n in range(forsok):
        try:
            r = session.get(url, params=params, timeout=60)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            senaste = e
            time.sleep(min(5 * 2 ** n, 60))  # 5, 10, 20, 40, 60 sekunder
    raise senaste


def _tabell(html: str) -> pd.DataFrame:
    """Läser FI:s tabell. Volym och Pris läses som TEXT: pandas skulle annars
    tolka '2,70' som 270 (komma som tusentalsavskiljare) och förstöra priset."""
    try:
        ramar = pd.read_html(StringIO(html), thousands=None, converters={"Volym": str, "Pris": str})
    except ValueError:  # ingen tabell på sidan
        return pd.DataFrame()
    return pd.concat(ramar, ignore_index=True) if ramar else pd.DataFrame()


def _antal_poster(html: str):
    """Läser 'Visar 1 till 10 av 61 result' även om siffran ligger i egen HTML-tagg."""
    text = re.sub(r"<[^>]+>", " ", html).replace("\xa0", " ")
    m = re.search(r"av\s+([\d ]+?)\s+result", text)
    return int(m.group(1).replace(" ", "")) if m else None


def hamta_dag(session: requests.Session, dag: date):
    """Hämtar alla poster med publiceringsdatum = dag.
    Returnerar (DataFrame, komplett) där komplett=False om något saknas."""
    d = dag.isoformat()
    params = {
        "SearchFunctionType": "Insyn",
        "Publiceringsdatum.From": d,
        "Publiceringsdatum.To": d,
    }
    r = hamta_url(session, BASE, params)
    ramar = [_tabell(r.text)]
    totalt = _antal_poster(r.text)
    komplett = True

    if totalt is not None:
        max_sida = -(-totalt // SIDSTORLEK)  # ceil
    else:
        # Antalet hittades inte: bläddra tills en sida är tom eller kort
        print(f"  Info {d}: hittade inte antal poster, bläddrar tills sidorna tar slut")
        max_sida = 100

    sida = 2
    while sida <= max_sida:
        if totalt is None and len(ramar[-1]) < SIDSTORLEK:
            break
        p = {
            "button": "search",
            "SearchFunctionType": "Insyn",
            "paging": "True",
            "Publiceringsdatum.From": d,
            "Publiceringsdatum.To": d,
            "page": sida,
        }
        try:
            rr = hamta_url(session, f"{BASE}/Insyn", p)
            ramar.append(_tabell(rr.text))
        except requests.RequestException:
            print(f"  Varning: kunde inte hämta sida {sida} för {d}")
            komplett = False
            if totalt is None:
                break
        sida += 1
        time.sleep(1.0)  # snäll mot FI:s server

    ramar = [x for x in ramar if not x.empty]
    df = pd.concat(ramar, ignore_index=True) if ramar else pd.DataFrame()
    if totalt is not None and len(df) != totalt:
        print(f"  Varning {d}: fick {len(df)} av {totalt} rader")
        komplett = False
    return df, komplett


# ---------------------------------------------------------------- rensning
def _tal(serie: pd.Series) -> pd.Series:
    """'11,38' -> 11.38 (hanterar mellanslag och hårda mellanslag)."""
    s = serie.astype(str).str.replace(r"[\s\xa0]", "", regex=True).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


def rensa(df: pd.DataFrame) -> pd.DataFrame:
    """Gör om text till tal. Kan köras flera gånger på samma data."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if not pd.api.types.is_numeric_dtype(df["Volym"]):
        # Volym är ett heltal: komma/mellanslag är tusentalsavskiljare ("5,485")
        df["Volym"] = pd.to_numeric(
            df["Volym"].astype(str).str.replace(r"[\s\xa0,]", "", regex=True), errors="coerce"
        )
    if not pd.api.types.is_numeric_dtype(df["Pris"]):
        df["Pris"] = _tal(df["Pris"])
    df["Belopp"] = df["Volym"] * df["Pris"]
    df["Närstående"] = df["Närstående"].fillna("").astype(str)
    # FI använder hårda mellanslag (\xa0) i texten; annars blir 'Anna Berg' och
    # 'Anna\xa0Berg' två olika personer.
    for c in ["Emittent", "Person i ledande ställning", "Befattning", "Närstående",
              "Karaktär", "Instrumentnamn", "Instrumenttyp", "Valuta"]:
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str).str.replace("\xa0", " ", regex=False).str.strip()
    return df


def till_lagring(df: pd.DataFrame) -> pd.DataFrame:
    df = rensa(df)
    for k in KOLUMNER:
        if k not in df.columns:
            df[k] = ""
    df["Publiceringsdatum"] = df["Publiceringsdatum"].astype(str).str[:10]
    df["Transaktionsdatum"] = df["Transaktionsdatum"].astype(str).str[:10]
    return df[KOLUMNER]


# ---------------------------------------------------------------- historik
def las_historik() -> pd.DataFrame:
    if not HISTORIK.exists():
        return pd.DataFrame(columns=KOLUMNER)
    return pd.read_csv(HISTORIK, dtype={"ISIN": str}, keep_default_na=False)


def las_dagar() -> set:
    if not DAGFIL.exists():
        return set()
    return {x.strip() for x in DAGFIL.read_text().splitlines() if x.strip()}


CHECKPOINT_DAGAR = 60   # checka in i git (Actions) var 60:e hämtade dag, ca var 30:e minut


def uppdatera_historik(fran: date, max_dagar: int = 0, till: date = None, inkl_idag: bool = False) -> None:
    """Hämtar alla dagar (även helger) från `fran` till igår som inte redan är hämtade.
    inkl_idag: hämtar även idag (kvällskörning efter stängning). Dagen sparas då men markeras INTE som komplett, så att
    nästa körning hämtar om den (sena publiceringar) och tar bort de delvisa raderna först.
    max_dagar > 0 begränsar antalet dagar per körning; `till` stoppar vid ett slutdatum
    (inklusive), så att man kan ladda ett år i taget."""
    hamtade = las_dagar()
    idag = date.today()
    if till is not None:
        idag = min(idag, till + timedelta(days=1))
    att_hamta = []
    dag = fran
    slutdag = idag + timedelta(days=1) if (inkl_idag and till is None) else idag
    while dag < slutdag:  # idag hoppas över om inte inkl_idag: dagen är inte klar än
        # alla dagar: FI publicerar även på lördagar och söndagar (helgtest 2026-10-04: ca 9 poster per helgdag)
        if dag.isoformat() not in hamtade or dag >= idag:
            att_hamta.append(dag)
        dag += timedelta(days=1)

    if not att_hamta:
        print("Historiken är redan uppdaterad.")
        return

    if max_dagar > 0:
        att_hamta = att_hamta[:max_dagar]
    print(f"Hämtar {len(att_hamta)} dagar ({att_hamta[0]} till {att_hamta[-1]})...")
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    })

    # Om en dag skulle finnas delvis i filen (avbruten körning) tas den bort först
    hist = las_historik()
    if not hist.empty:
        rensa_dagar = {d.isoformat() for d in att_hamta}
        kvar = hist[~hist["Publiceringsdatum"].isin(rensa_dagar)]
        if len(kvar) != len(hist):
            kvar.to_csv(HISTORIK, index=False)

    nya_totalt = 0
    i_rad_misslyckade = 0
    for i, dag in enumerate(att_hamta, 1):
        try:
            df, komplett = hamta_dag(s, dag)
            i_rad_misslyckade = 0
        except requests.RequestException as e:
            i_rad_misslyckade += 1
            print(f"  {dag}: misslyckades ({e.__class__.__name__}), försöker igen nästa körning")
            if i_rad_misslyckade >= 5:
                print("FI svarar inte (5 dagar i rad misslyckades). Avbryter; "
                      "kör igen senare, det som redan hämtats är sparat.")
                break
            continue
        time.sleep(0.5)
        if not df.empty:
            lagra = till_lagring(df)
            lagra.to_csv(HISTORIK, mode="a", header=not HISTORIK.exists(), index=False)
            nya_totalt += len(lagra)
        if komplett and dag < idag:  # tom vardag (helgdag) är också komplett; idag markeras aldrig komplett
            with open(DAGFIL, "a") as f:
                f.write(dag.isoformat() + "\n")
        if i % 10 == 0 or i == len(att_hamta):
            print(f"  {i}/{len(att_hamta)} dagar klara, {nya_totalt} nya rader")
        if i % CHECKPOINT_DAGAR == 0 and i < len(att_hamta):
            checkpoint.spara_i_git([HISTORIK, DAGFIL], f"Mellanlagring insynshandel {dag.isoformat()}")
    print(f"Klart. {nya_totalt} nya rader sparade i {HISTORIK.name}")


# ---------------------------------------------------------------- regler
def bolagsnyckel(df: pd.DataFrame) -> pd.Series:
    """Nyckel per bolag, byggd på ett normaliserat emittentnamn. FI stavar samma
    bolag olika ('group'/'Group', '(publ)'/'(publ.)', avhuggna namn, 'HANZA AB'/
    'HANZA Holding AB'). ISIN går inte att använda: det gäller aktieslaget, så
    A- och B-aktier eller holdingbolagens poster hamnar annars på olika bolag."""
    namn = (
        df["Emittent"].astype(str).str.lower()
        .str.replace(r"\(publ\.?\)?", "", regex=True)   # (publ), (publ., (publ.)
        .str.replace(r"\bpubl\b\.?", "", regex=True)
        .str.replace(r"\b(ab|aktiebolag|group|holding|corporation)\b", "", regex=True)
        .str.replace(r"[^0-9a-zåäöøæ]+", "", regex=True)
    )
    return "namn:" + namn


def markera_interna(df: pd.DataFrame) -> pd.DataFrame:
    """Flaggar
    - Intern_overforing: köp med en försäljning av samma person, bolag, dag,
      volym och pris (t.ex. överföring mellan person och närståendes bolag).
    - Delad_affar: samma bolag, dag, volym och pris anmält av flera personer
      (en gemensam affär, t.ex. via ett bolag). Ska bara räknas en gång."""
    df = df.copy()
    df["Bolag_id"] = bolagsnyckel(df)
    # Innehav via holdingbolag ("Aktier i X AB, som äger aktier i Y"). Det är
    # ofta bud, övertaganden eller omstruktureringar och inga köp på marknaden.
    df["Indirekt"] = df["Instrumentnamn"].astype(str).str.contains(
        r"som (?:indirekt |direkt )?(?:genom .*?)?äger|indirekt", case=False, regex=True
    )
    person = ["Bolag_id", "Person i ledande ställning", "Transaktionsdatum", "Volym", "Pris"]
    salj = df[df["Karaktär"] == "Avyttring"]
    salj_nycklar = set(map(tuple, salj[person].values))
    df["Intern_overforing"] = df[person].apply(tuple, axis=1).isin(salj_nycklar)

    affar = ["Bolag_id", "Transaktionsdatum", "Volym", "Pris"]
    kop = df["Karaktär"] == "Förvärv"
    antal_personer = (
        df[kop].groupby(affar)["Person i ledande ställning"].transform("nunique")
    )
    df["Delad_affar"] = False
    df.loc[kop, "Delad_affar"] = antal_personer > 1
    return df


# FI:s register saknade fältet Instrumenttyp före 2018-09-18. För de raderna antas instrumentet vara en aktie
# om namnet inte ser ut som en option, ett teckningsbevis eller liknande (träffsäkerhet ca 95 % mot de rader som har typ;
# resten är oftast optioner vars instrumentnamn är bolagsnamnet och som sorteras bort av kurskontrollen (brus) i backtestet).
_OPTIONSLIKA = re.compile(
    r"option|warrant|\bTO\s?\d|\bTO[A-Z]?\d|\bBTA\b|\bBTU\b|teckningsr|unitr|uniträtt|\bUR\b|konvertib|obligation|"
    r"\bSO\s?\d|\bKO\d|\bLTI|incitament|\bRätt|instrument|\bunit|tilldelningsr|swap|certifikat", re.I)


def antag_instrumenttyp(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    saknas = df["Instrumenttyp"].isna() | (df["Instrumenttyp"].astype(str).str.strip() == "")
    optionslik = df["Instrumentnamn"].astype(str).str.contains(_OPTIONSLIKA)
    df["Typ_antagen"] = saknas & ~optionslik
    df.loc[saknas & ~optionslik, "Instrumenttyp"] = "Aktie"
    return df


def bygg_koplista(hist: pd.DataFrame) -> pd.DataFrame:
    df = markera_interna(rensa(antag_instrumenttyp(hist)))
    kop = df[(df["Karaktär"] == "Förvärv") & (df["Instrumenttyp"] == "Aktie")]
    return kop.sort_values(["Publiceringsdatum", "Emittent"]).reset_index(drop=True)


AFFAR = ["Bolag_id", "Transaktionsdatum", "Volym", "Pris"]

ROLLER = {
    "verkställande direktör (vd)": "VD",
    "vice vd": "Vice VD",
    "styrelseordförande": "Styrelseordf.",
    "styrelseledamot": "Styrelse",
    "ekonomichef/finanschef/finansdirektör": "CFO",
    "annan ledande befattningshavare": "Ledning",
    "arbetstagarrepresentant i styrelsen eller arbetstagarsuppleant": "Arbetstagarrep.",
}


def kort_roll(befattning) -> str:
    b = str(befattning).replace("\xa0", " ").strip()
    if b.lower().startswith("annan medlem i bolagets"):
        return "Annan (adm./ledn./kontroll)"
    return ROLLER.get(b.lower(), b)



def _giltiga_kop(kop: pd.DataFrame) -> pd.DataFrame:
    """Riktiga aktieköp i SEK: inte interna överföringar, pris > 0, personnamn
    normaliserade och dubbletter från rättelser borttagna."""
    k = kop[
        (~kop["Intern_overforing"])
        & (~kop["Indirekt"])
        & (kop["Valuta"].str.upper() == "SEK")
        & (kop["Pris"] > 0)
    ].copy()
    k["Person i ledande ställning"] = (
        k["Person i ledande ställning"].astype(str).str.strip().str.title()
    )  # 'björn krasse' och 'Björn Krasse' är samma person
    k["Roll"] = k["Befattning"].map(kort_roll)
    k["Namn_roll"] = k["Person i ledande ställning"] + " (" + k["Roll"] + ")"
    return k.drop_duplicates(
        subset=["Bolag_id", "Person i ledande ställning", "Transaktionsdatum", "Volym", "Pris"]
    )


def regel_insiderkop(kop: pd.DataFrame, min_belopp: float) -> pd.DataFrame:
    """Regel 1: aktieköp i SEK, inte interna överföringar, pris > 0,
    summerat per bolag >= min_belopp. En delad affär (flera personer,
    samma bolag/dag/volym/pris) räknas bara en gång; Personer visar alla."""
    k = _giltiga_kop(kop)
    antal_personer = k.groupby("Bolag_id")["Person i ledande ställning"].nunique()
    # Delad affär: behåll en rad per affär men spara alla namn
    affar = AFFAR
    k["Personer"] = k.groupby(affar)["Namn_roll"].transform(
        lambda x: ", ".join(sorted(set(x)))
    )
    k = k.drop_duplicates(subset=affar)

    s = (
        k.groupby("Bolag_id", dropna=False)
        .agg(
            Bolag=("Emittent", lambda x: x.value_counts().idxmax()),
            Personer=("Personer", lambda x: " | ".join(sorted(set(x)))),
            Belopp=("Belopp", "sum"),
            Affarer=("Belopp", "count"),
            Storsta_affar=("Belopp", "max"),
            Narstaende=("Närstående", lambda x: "Ja" if (x == "Ja").any() else ""),
            Delad=("Delad_affar", lambda x: "Ja" if x.any() else ""),
            Publicerat=("Publiceringsdatum", "max"),
            Fran=("Transaktionsdatum", "min"),
            Till=("Transaktionsdatum", "max"),
        )
        .reset_index()
    )
    s.insert(2, "Antal_personer", s["Bolag_id"].map(antal_personer))
    return s[s["Belopp"] >= min_belopp].sort_values("Belopp", ascending=False)


def klusterkop(kop: pd.DataFrame, dagar: int, min_belopp: float, min_personer: int = 2) -> pd.DataFrame:
    """Bolag där minst `min_personer` olika personer köpt aktier (publicerat
    senaste `dagar` dagarna). En delad affär ger flera namn men ett belopp."""
    grans = (date.today() - timedelta(days=dagar)).isoformat()
    k = _giltiga_kop(kop[kop["Publiceringsdatum"] >= grans])
    if k.empty:
        return pd.DataFrame()
    personer = k.groupby("Bolag_id")["Namn_roll"].agg(lambda x: sorted(set(x)))
    delade = k.groupby("Bolag_id")["Delad_affar"].any()
    k1 = k.drop_duplicates(subset=AFFAR)  # en rad per affär så beloppet inte dubbleras
    g = (
        k1.groupby("Bolag_id")
        .agg(
            Bolag=("Emittent", lambda x: x.value_counts().idxmax()),
            Belopp=("Belopp", "sum"),
            Affarer=("Belopp", "count"),
            Senast_publicerat=("Publiceringsdatum", "max"),
        )
        .reset_index()
    )
    g["Antal_personer"] = g["Bolag_id"].map(k.groupby("Bolag_id")["Person i ledande ställning"].nunique())
    g["Personer"] = g["Bolag_id"].map(personer.map(", ".join))
    g["Delad_affar"] = g["Bolag_id"].map(delade).map({True: "Ja", False: ""})
    g = g[(g["Antal_personer"] >= min_personer) & (g["Belopp"] >= min_belopp)]
    return g.sort_values(["Antal_personer", "Belopp"], ascending=False)[
        ["Bolag", "Antal_personer", "Belopp", "Affarer", "Delad_affar",
         "Senast_publicerat", "Personer", "Bolag_id"]
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fran", default=STANDARD_START,
                    help=f"Startdatum för hämtning (standard {STANDARD_START})")
    ap.add_argument("--dagar", type=int, default=3,
                    help="Visa träffar publicerade senaste N dagarna")
    ap.add_argument("--min-belopp", type=float, default=MIN_BELOPP_SEK)
    ap.add_argument("--bara-analys", action="store_true", help="Hämta inget, analysera sparad historik")
    ap.add_argument("--kluster-dagar", type=int, default=30,
                    help="Fönster (publicerat) för bolag där flera personer köpt")
    ap.add_argument("--inkl-idag", action="store_true", help="Hämta även idag (kvällskörning); dagen hämtas om nästa körning")
    ap.add_argument("--till", default=None, help="Sista dag att hämta (YYYY-MM-DD), t.ex. 2021-12-31")
    ap.add_argument("--max-dagar", type=int, default=0,
                    help="Max antal dagar att hämta per körning (0 = alla)")
    args = ap.parse_args()

    if not args.bara_analys:
        uppdatera_historik(datetime.strptime(args.fran, "%Y-%m-%d").date(), args.max_dagar,
                           datetime.strptime(args.till, "%Y-%m-%d").date() if args.till else None, args.inkl_idag)

    hist = las_historik()
    if hist.empty:
        print("Ingen historik sparad än.")
        return

    kop = bygg_koplista(hist)
    kop.to_csv(KOPLISTA, index=False)
    print(f"\nHistorik: {len(hist)} rader, {len(kop)} aktieköp "
          f"({hist['Publiceringsdatum'].min()} till {hist['Publiceringsdatum'].max()})")
    print(f"Sparade aktieköp i {KOPLISTA.name}")

    pd.set_option("display.width", 220, "display.max_columns", None, "display.max_colwidth", 40)

    kluster = klusterkop(kop, args.kluster_dagar, args.min_belopp)
    kluster.to_csv(KLUSTER, index=False)
    if kluster.empty:
        print(f"\nInga bolag där flera personer köpt senaste {args.kluster_dagar} dagarna.")
    else:
        print(f"\nBolag där FLERA PERSONER köpt (publicerat senaste {args.kluster_dagar} dagarna, "
              f"totalt >= {args.min_belopp:,.0f} kr):")
        print(kluster.drop(columns=["Bolag_id"]).to_string(
            index=False, float_format=lambda x: f"{x:,.0f}"))
        print(f"Sparade i {KLUSTER.name}")

    grans = (date.today() - timedelta(days=args.dagar)).isoformat()
    senaste = kop[kop["Publiceringsdatum"] >= grans]
    traffar = regel_insiderkop(senaste, args.min_belopp)
    if traffar.empty:
        print(f"\nInga träffar senaste {args.dagar} dagarna.")
        return

    print(f"\nTräffar (köp >= {args.min_belopp:,.0f} kr, publicerade senaste {args.dagar} dagarna):")
    print(traffar.to_string(index=False, float_format=lambda x: f"{x:,.0f}"))
    traffar.to_csv(TRAFFAR, index=False)
    print(f"\nSparade i {TRAFFAR.name}")


if __name__ == "__main__":
    main()
