#!/usr/bin/env python3
"""
Backtest: sker insiderköp före kursuppgångar på medellång sikt?

Händelsestudie. För varje signal (t.ex. "3 personer köper inom 14 dagar") mäts
kursutvecklingen efter publiceringen, jämfört med index och med slumpen.
Utfallet delas dessutom upp efter teknisk analys vid inträdet, och en teoretisk
stop loss kan simuleras.

Indata (skapas av insider_bot.py och kurser.py):
  insiderkop.csv, ticker_map.csv, kurser.csv

Utdata:
  backtest_resultat.csv   sammanfattning per signal, period, dimension, värde och horisont
  backtest_handelser.csv  varje enskild händelse med utfall och tekniskt läge
  backtest_stopp.csv      stop loss-simulering (med --stopp)

Kör:
  python backtest.py
  python backtest.py --delning 2026-01-01    # reglerna "på" 2024-25, test på 2026
  python backtest.py --kostnad 0.01          # 1 % totalt för köp + sälj (standard 0,16 %)
  python backtest.py --benchmark MEDIAN      # jämför mot medianaktien i datan över samma period
  python backtest.py --benchmark MEDEL       # jämför mot genomsnittsaktien (kapad) över samma period
  python backtest.py --benchmark ^OMXSSCPI   # jämför mot Small Cap PI (om Yahoo har den)
  python backtest.py --stopp                 # simulera stop loss (håll max 126 dagar)
  python backtest.py --stopp --fyllning stopp  # optimistiskt: fyll exakt på stoppnivån

Dimensioner (hur kursen såg ut vid inträdet; Dimension/Varde i resultatet):
  Villkor      : ej_fall / fall (minst 20 % under 52v-högsta) / snabbt_fall
                 (minst 15 % lägre än för 15 handelsdagar sedan)
  Trend        : över eller under 200-dagars glidande medelvärde
  RSI          : RSI(14): <30 översåld, 30-50, 50-70, >=70 överköpt
  Nara_hogsta  : inom 10 % från 52-veckorshögsta (momentum) eller längre ner
  Donchian20/50: utbrott ur Donchian-kanal (stängningskurser): ny 20- eller 50-dagarshög
                 (utbrott_upp) eller -botten (utbrott_ner) någon gång de senaste 10
                 handelsdagarna fram till gårdagen, annars inom_kanal.
  Likviditet   : median av daglig omsättning (kurs x volym) de 20 handelsdagarna före
                 köpet: under 0,1 / 0,1-0,5 / 0,5-2 / över 2 MSEK per dag (kräver Volym
                 i kurser.csv). Euroaktier på Frankfurt (.F) får okand.
  Storlek      : börsvärde, grovt: kurs x DAGENS antal aktier (aktier.csv). Emissioner i
                 efterhand gör det felaktigt för tidigare år; använd som grov indelning.
  Rapport_reaktion : reaktionen (mot index, dagen rapporten kom) på senaste rapporten om den
                 är högst 20 handelsdagar gammal: negativ (<= -3 procentenheter) / neutral /
                 positiv (>= +3), annars ingen_rapport_20d. Kräver nyheter.csv.
  Rapport_forsprang : hur aktien rört sig mot index de 10 dagarna FÖRE den rapporten
                 (fall <= -5 / flat / uppgang >= +5 procentenheter). Visar om nyheten
                 redan var inprisad.
  Nyhet        : högsta nyhetstyp i bolaget de 10 handelsdagarna före köpet (kräver
                 nyheter.csv från nyheter.py): vinstvarning, nyemission, m_and_a, ledning,
                 prognoshojning, rapport, annan_nyhet, ingen_nyhet eller okand (bolaget
                 saknas i datan, t.ex. Spotlight/NGM)

Så läser du resultatet:
  Median_AR   : median av överavkastning mot OMX Stockholm PI, i procent
  Positiva_%  : andel händelser som slog index
  Placebo_pct : andel slumpdragningar (samma bolag, slumpmässigt datum, samma
                dimension och värde) som ger LÄGRE median än signalen. Nära 100 =
                bättre än slump, runt 50 = ingen skillnad mot slump.

Stop loss: bara dagliga stängningskurser finns. Stoppet utlöses när en stängning
ligger på eller under nivån och utgången sker på den stängningen (realistiskt i
småbolag där kursen ofta gappar förbi stoppet). Med --fyllning stopp antas fyllning
exakt på nivån, vilket är optimistiskt. Regler: fasta stopp 10/15/20 % under
inträdeskursen och efterföljande (trailing) stopp 15/20 % under högsta stängning.
Positioner som inte hunnit nå full innehavstid och inte stoppats räknas inte med.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import insider_bot as ib
import nyheter as ny

MAPP = Path(__file__).resolve().parent
HORISONTER = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}  # handelsdagar
INDEX = "^OMXSPI"
TOPPROLLER = {"VD", "Vice VD", "CFO"}
NEDGANG_FALL = -0.20   # "fall" = kursen minst 20 % under 52-veckorshögsta vid inträdet
SNABB_DAGAR = 15       # "snabbt_fall" = kursen minst 15 % lägre än för 15 handelsdagar sedan
SNABB_FALL = -0.15
DATAFEL_NED = -0.80    # en dags kursrörelse under -80 % eller över +400 % räknas som troligt datafel
DATAFEL_UPP = 4.00     # (omvänd split/sammanläggning som Yahoo inte justerat, felaktig ticker)
KARENS_DAGAR = 30      # samma bolag och signal räknas inte om inom 30 dagar
FONSTER_DAGAR = 14
MAX_BELOPP = 5e8            # köp över 500 Mkr betraktas som prisfel och tas bort
KOSTNAD_PER_AFFAR = 0.0008  # 0,08 % per affär; köp + sälj = 0,16 %. Spread i småbolag ingår inte.

SIGNALER = {
    "belopp_10k": ("belopp", 10_000),           # lågt tröskelvärde, för jämförelse med 50k
    "vd_cfo_10k": ("roll", 10_000),
    "narstaende_10k": ("narstaende", 10_000),
    "direkt_10k": ("direkt", 10_000),
    "belopp_50k": ("belopp", 50_000),
    "belopp_100k": ("belopp", 100_000),
    "belopp_500k": ("belopp", 500_000),
    "vd_cfo_50k": ("roll", 50_000),
    "narstaende_50k": ("narstaende", 50_000),   # köp av närstående
    "direkt_50k": ("direkt", 50_000),           # köp av personen själv (jämförelse)
    "kluster_2": ("kluster", 2),
    "kluster_3": ("kluster", 3),
}

# fast = stopp under inträdeskursen, trail = efterföljande stopp under högsta stängning
STOPPREGLER = {
    "ingen": ("ingen", 0.0),
    "stopp_10": ("fast", 0.10),
    "stopp_15": ("fast", 0.15),
    "stopp_20": ("fast", 0.20),
    "trail_15": ("trail", 0.15),
    "trail_20": ("trail", 0.20),
}
DIMENSIONER = ("Villkor", "Trend", "RSI", "Nara_hogsta", "Nyhet", "Rapport_reaktion", "Rapport_forsprang",
               "Donchian20", "Donchian50", "Likviditet", "Storlek")
OMSATTNING_GRANSER = [100_000, 500_000, 2_000_000]          # kr per dag (median 20 dagar)
OMSATTNING_ETIKETT = {0: "under_0.1_MSEK", 1: "0.1-0.5_MSEK", 2: "0.5-2_MSEK", 3: "over_2_MSEK", -1: "okand"}
STORLEK_GRANSER = [250e6, 1e9, 5e9]                          # börsvärde, kr
STORLEK_ETIKETT = {0: "micro_under_250M", 1: "small_250M-1G", 2: "mid_1-5G", 3: "large_over_5G", -1: "okand"}


# ------------------------------------------------------------ indata
BRUSFILTER = True   # släpp arbetstagarrepresentanter, aktiesparprogram och regelbundna sparare


REGELBUNDEN_MAX_BELOPP = 100_000   # regelbundna köp över detta (median per person) är inte sparande


def brusfilter(k: pd.DataFrame):
    """Tar bort köp som inte är ett aktivt köpbeslut. Använder bara information som fanns vid publiceringen.
      arbetstagarrep : Befattning är arbetstagarrepresentant (köper i personalprogram)
      sparprogram    : minst 3 personer köper samma dag till exakt samma pris med icke-rund kurs
                       (t.ex. Skanskas 267,34047), typiskt aktiesparprogram
      regelbunden    : samma person köper i bolaget under minst 4 olika kalendermånader de senaste 6 månaderna,
                       men bara om medianbeloppet är under 100 tkr (annars är det aktiva köp)
    Returnerar (filtrerade köp, antal borttagna per orsak)."""
    pers = k["Person i ledande ställning"].astype(str)
    arb = k["Befattning"].astype(str).str.lower().str.contains("arbetstagar", na=False)
    pris_ej_rund = (k["Pris"].round(2) - k["Pris"]).abs() > 1e-9
    g = k.groupby(["Bolag_id", "Transaktionsdatum", "Pris"])["Person i ledande ställning"].transform("nunique")
    prog = (g >= 3) & pris_ej_rund

    reg = pd.Series(False, index=k.index)
    d = k.assign(_p=pers, _m=k["Publicerat"].dt.year * 12 + k["Publicerat"].dt.month)
    for _, grp in d.sort_values("Publicerat").groupby(["Bolag_id", "_p"]):
        if len(grp) < 4:
            continue
        # Sparande är små, jämna belopp. Stora ojämna köp (t.ex. en VD som köper för 1-2 MSEK varannan
        # månad) är aktiva beslut och ska inte filtreras bort (backtest: de bortfiltrerade regelträffarna
        # hade +9,6 % mot +7,0 % 6m-överavkastning).
        if grp["Belopp"].median() >= REGELBUNDEN_MAX_BELOPP:
            continue
        mm = grp["_m"].to_numpy(); dd = grp["Publicerat"].to_numpy()
        flagg = []
        for i in range(len(grp)):
            fonster = (dd <= dd[i]) & (dd > dd[i] - np.timedelta64(183, "D"))
            flagg.append(len(set(mm[fonster])) >= 4)
        reg.loc[grp.index] = flagg
    bort = arb | prog | reg
    return k[~bort].copy(), {"arbetstagarrep": int(arb.sum()), "sparprogram": int(prog.sum()),
                             "regelbunden": int(reg.sum()), "totalt_borttagna": int(bort.sum())}


def las_kop(path: Path, tickermap: Path, brus: bool = None):
    kop = pd.read_csv(path, keep_default_na=False, dtype={"ISIN": str})
    for c in ["Intern_overforing", "Indirekt", "Delad_affar"]:
        kop[c] = kop[c].astype(str).str.lower().eq("true")
    kop = ib.rensa(kop)
    k = ib._giltiga_kop(kop)          # riktiga aktieköp i SEK, utan interna och indirekta
    k["Publicerat"] = pd.to_datetime(k["Publiceringsdatum"])
    brus_info = {}
    if BRUSFILTER if brus is None else brus:
        k, brus_info = brusfilter(k)
        print(f"Brusfilter: {brus_info}")

    tm = pd.read_csv(tickermap, keep_default_na=False, dtype=str)
    tm = tm[(tm["Ticker"] != "") & tm["Status"].isin(["ok", "manuell"])]
    k["Ticker"] = k["Bolag_id"].map(dict(zip(tm["Bolag_id"], tm["Ticker"])))
    # sanera belopp: prisfel/enhetsfel i FI-datan ger orimliga belopp (t.ex. 3,7 biljoner)
    for_stora = k["Belopp"] > MAX_BELOPP
    # samma bolag under flera namnvarianter (Collector AB / Collector Bank AB osv.) slås ihop via ticker
    k["Bolag_id"] = np.where(k["Ticker"].notna(), "t:" + k["Ticker"].astype(str), k["Bolag_id"])
    k = k[~for_stora].copy()

    # en affär som anmälts av flera personer ger ett belopp, inte flera
    forst = ~k.duplicated(ib.AFFAR)
    k["Belopp_unik"] = np.where(forst, k["Belopp"], 0.0)
    tackning = {
        "Köp": len(k),
        "Köp med ticker": int(k["Ticker"].notna().sum()),
        "Borttagna för stora belopp": int(for_stora.sum()),
        "Brusfilter": brus_info,
        "Ticker per år": k.groupby(k["Publicerat"].dt.year)["Ticker"].apply(lambda x: x.notna().mean()).to_dict(),
        "Ticker serie": k["Ticker"],
        "Ar serie": k["Publicerat"].dt.year,
    }
    return k[k["Ticker"].notna()].copy(), tackning


def nyhetsdimension(tickermap: Path, nyheter_fil: Path, bolag_fil: Path):
    """Funktion px -> {'Nyhet': (matris, etiketter)}, eller None om nyheter saknas."""
    if not (Path(nyheter_fil).exists() and Path(bolag_fil).exists()):
        return None
    n = ny.las_nyheter(nyheter_fil)
    b = pd.read_csv(bolag_fil, keep_default_na=False, dtype=str)
    tm = pd.read_csv(tickermap, keep_default_na=False, dtype=str)
    tm = tm[(tm["Ticker"] != "") & tm["Status"].isin(["ok", "manuell"])]
    bt = dict(zip(tm["Bolag_id"], tm["Ticker"]))
    return lambda px: {"Nyhet": ny.nyhetstillstand(px, n, b, bt)}


def rapportdimension(tickermap: Path, nyheter_fil: Path):
    """Funktion px -> {'Rapport_reaktion', 'Rapport_forsprang'}, eller None om nyheter saknas."""
    if not Path(nyheter_fil).exists():
        return None
    import rapporter as rp
    n = ny.las_nyheter(nyheter_fil)
    tm = pd.read_csv(tickermap, keep_default_na=False, dtype=str)
    tm = tm[(tm["Ticker"] != "") & tm["Status"].isin(["ok", "manuell"])]
    bt = dict(zip(tm["Bolag_id"], tm["Ticker"]))
    return lambda px: rp.rapportstillstand(px, n, bt)


def _klassa(mat: pd.DataFrame, granser) -> pd.DataFrame:
    kod = np.digitize(mat.to_numpy(dtype=float), granser)
    kod = np.where(np.isnan(mat.to_numpy(dtype=float)), -1, kod)
    return pd.DataFrame(kod, index=mat.index, columns=mat.columns)


def likviditetsdimensioner(kurser_fil: Path, aktier_fil: Path):
    """Funktion px -> {'Likviditet': ..., 'Storlek': ...}, eller None om kurser.csv
    saknar volym. Storlek läggs bara till om aktier.csv finns."""
    d = pd.read_csv(kurser_fil, dtype={"Ticker": str})
    if "Volym" not in d.columns or d["Volym"].notna().sum() == 0:
        return None
    d["Datum"] = pd.to_datetime(d["Datum"])
    d["Oms"] = d["Stang"] * d["Volym"]
    oms = d.pivot_table(index="Datum", columns="Ticker", values="Oms", aggfunc="last").sort_index()
    aktier = None
    if Path(aktier_fil).exists():
        a = pd.read_csv(aktier_fil, dtype={"Ticker": str})
        aktier = a.dropna(subset=["Aktier"]).set_index("Ticker")["Aktier"]

    def f(px):
        o = oms.reindex(index=px.index, columns=px.columns)
        # median de 20 dagarna FÖRE inträdesdagen: inget läckage från inträdesdagens volym
        med = o.rolling(20, min_periods=10).median().shift(1)
        euro = [c for c in px.columns if str(c).endswith(".F") or str(c).startswith("^")]
        med[euro] = np.nan
        ut = {"Likviditet": (_klassa(med, OMSATTNING_GRANSER), OMSATTNING_ETIKETT)}
        if aktier is not None:
            n = aktier.reindex(px.columns)
            cap = px.mul(n, axis=1)
            cap[euro] = np.nan
            ut["Storlek"] = (_klassa(cap, STORLEK_GRANSER), STORLEK_ETIKETT)
        return ut
    return f


def las_kurser(path: Path) -> pd.DataFrame:
    d = pd.read_csv(path, dtype={"Ticker": str})
    d["Datum"] = pd.to_datetime(d["Datum"])
    bred = d.pivot_table(index="Datum", columns="Ticker", values="Stang", aggfunc="last")
    return bred.sort_index()


# ------------------------------------------------------------ signaler
def kandidater(k: pd.DataFrame, typ: str, param) -> pd.DataFrame:
    """Alla tillfällen då signalen är uppfylld (före karens)."""
    ut = []
    for tk, g in k.groupby("Ticker"):
        g = g.sort_values("Publicerat")
        d = g["Publicerat"].to_numpy()
        bel = g["Belopp_unik"].to_numpy()
        pers = g["Person i ledande ställning"].to_numpy()
        roll = g["Roll"].to_numpy()
        rbel = g["Belopp"].to_numpy()
        nar = g["Närstående"].astype(str).str.strip().eq("Ja").to_numpy()
        for i in range(len(g)):
            start = np.searchsorted(d, d[i] - np.timedelta64(FONSTER_DAGAR - 1, "D"), side="left")
            belopp = bel[start:i + 1].sum()
            personer = len(set(pers[start:i + 1]))
            if typ == "belopp":
                ok = belopp >= param
            elif typ == "narstaende":
                ok = bool(nar[i]) and rbel[i] >= param
            elif typ == "direkt":
                ok = (not nar[i]) and rbel[i] >= param
            elif typ == "roll":
                ok = roll[i] in TOPPROLLER and rbel[i] >= param
            else:  # kluster
                ok = personer >= param and belopp >= 100_000
            if ok:
                ut.append((tk, d[i], belopp, personer))
    return pd.DataFrame(ut, columns=["Ticker", "Publicerat", "Belopp_fonster", "Antal_personer"])


def karens(c: pd.DataFrame) -> pd.DataFrame:
    """Behåll första träffen och hoppa över nya inom KARENS_DAGAR per bolag."""
    keep = []
    for tk, g in c.sort_values("Publicerat").groupby("Ticker"):
        sista = None
        for idx, r in g.iterrows():
            if sista is None or (r["Publicerat"] - sista).days >= KARENS_DAGAR:
                keep.append(idx)
                sista = r["Publicerat"]
    return c.loc[keep].sort_values("Publicerat")


# ------------------------------------------------------------ teknisk analys
def rsi(px: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """Wilders RSI på stängningskurser."""
    d = px.diff()
    upp = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ner = (-d).clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + upp / ner)


DONCHIAN_FONSTER = 10     # utbrott räknas om det skedde under de senaste 10 handelsdagarna


def donchian(px: pd.DataFrame, n: int, k: int = DONCHIAN_FONSTER):
    """Donchian-kanal på stängningskurser (högsta/lägsta saknas i datan): utbrott uppåt =
    stängning över högsta stängning de n föregående dagarna, nedåt = under lägsta.
    Tillståndet gäller om ett utbrott skett de senaste k handelsdagarna, mätt till och med
    GÅRDAGEN (inget läckage in i inträdesdagen). 0 = inom kanalen, 1 = utbrott upp,
    2 = utbrott ner, 3 = både och, -1 = för kort historik."""
    hog = px.shift(1).rolling(n, min_periods=n).max()
    lag = px.shift(1).rolling(n, min_periods=n).min()
    giltig = hog.notna()
    upp = ((px > hog) & giltig).astype(float).shift(1).rolling(k, min_periods=1).max() > 0
    ner = ((px < lag) & giltig).astype(float).shift(1).rolling(k, min_periods=1).max() > 0
    kod = upp.astype(int) + 2 * ner.astype(int)
    kod = kod.where(giltig.shift(1, fill_value=False), -1)
    return kod


DONCHIAN_ETIKETT = {-1: "okand", 0: "inom_kanal", 1: f"utbrott_upp_{DONCHIAN_FONSTER}d", 2: f"utbrott_ner_{DONCHIAN_FONSTER}d", 3: "upp_och_ner"}


def bygg_tillstand(px: pd.DataFrame) -> dict:
    """Tekniska dimensioner. Varje dimension är en matris (datum x ticker) med
    heltalskoder och en ordbok kod -> etikett. -1 = okänd (för kort historik)."""
    hog = px.rolling(252, min_periods=120).max()
    under_hog = (px / hog - 1) <= NEDGANG_FALL
    snabbt = (px / px.shift(SNABB_DAGAR) - 1) <= SNABB_FALL
    # 0 = ej_fall, 1 = fall (långt under 52v-högsta), 2 = snabbt_fall (rasat nyligen)
    villkor = under_hog.astype(int).where(~snabbt, 2)

    ma200 = px.rolling(200, min_periods=150).mean()
    trend = (px > ma200).astype(int).where(ma200.notna(), -1)

    r = rsi(px)
    rsi_k = pd.DataFrame(
        np.select([r < 30, r < 50, r < 70, r >= 70], [0, 1, 2, 3], default=-1),
        index=px.index, columns=px.columns)

    nara = ((px / hog - 1) >= -0.10).astype(int).where(hog.notna(), -1)
    return {
        "Villkor": (villkor, {0: "ej_fall", 1: "fall", 2: "snabbt_fall"}),
        "Trend": (trend, {1: "over_MA200", 0: "under_MA200", -1: "okand"}),
        "RSI": (rsi_k, {0: "RSI<30", 1: "RSI30-50", 2: "RSI50-70", 3: "RSI>=70", -1: "okand"}),
        "Nara_hogsta": (nara, {1: "inom10pct_av_52v_hogsta", 0: "langre_ner", -1: "okand"}),
        "Donchian20": (donchian(px, 20), DONCHIAN_ETIKETT),
        "Donchian50": (donchian(px, 50), DONCHIAN_ETIKETT),
    }


# ------------------------------------------------------------ avkastning
MEDIAN = "MEDIAN"   # jämförelse: medianaktien i datan över samma period (köp och behåll)
MEDEL = "MEDEL"     # jämförelse: genomsnittsaktien (kapad vid -90 % / +300 %) över samma period
TVARSNITT = (MEDIAN, MEDEL)


def aktiekolumner(px: pd.DataFrame) -> list:
    return [c for c in px.columns if not str(c).startswith("^") and not str(c).endswith(".F")]


def datafel_kumulativt(px: pd.DataFrame) -> pd.DataFrame:
    """Kumulativt antal 'troliga datafel' per aktie och datum: en dags kursrörelse under
    DATAFEL_NED eller över DATAFEL_UPP. Händelser vars innehavstid innehåller ett sådant
    hopp tas bort, annars förstör ett enda fel (t.ex. +15 000 % efter en sammanläggning)
    genomsnittet. Skillnaden mellan två datum visar om felet ligger i perioden."""
    r = px.pct_change(fill_method=None)
    return ((r > DATAFEL_UPP) | (r < DATAFEL_NED)).astype(int).cumsum()


def benchmark_fram(fwd: pd.DataFrame, bench: str) -> pd.Series:
    """Jämförelseavkastning per startdatum. Index: kolumnen. MEDIAN/MEDEL: tvärsnitt av
    alla aktiers köp-och-behåll-avkastning över samma period. Det är konsekvent med
    aktierna (justerade kurser, samma period) och undviker det uppåtbias som ett dagligen
    rebalanserat lika viktat index får i volatila småbolag."""
    if bench == MEDIAN:
        return fwd[aktiekolumner(fwd)].median(axis=1)
    if bench == MEDEL:
        return fwd[aktiekolumner(fwd)].clip(lower=-0.9, upper=3.0).mean(axis=1)
    return fwd[bench]


def bygg_marknad(kurser: pd.DataFrame, extra=None, bench: str = INDEX):
    """Index-kalendern är master. Returnerar datum, priser, avkastning och
    överavkastning per horisont samt tekniska tillstånd."""
    dates = kurser[INDEX].dropna().index
    px = kurser.reindex(dates).ffill(limit=5)
    if bench not in TVARSNITT and bench not in px.columns:
        raise SystemExit(f"Jämförelseindexet {bench} saknas i kurser.csv")
    raw, ar = {}, {}
    fel = datafel_kumulativt(px)
    for namn, h in HORISONTER.items():
        fwd = px.shift(-h) / px - 1
        fwd = fwd.mask((fel.shift(-h) - fel) > 0)   # period med troligt datafel räknas inte
        raw[namn] = fwd
        ar[namn] = fwd.sub(benchmark_fram(fwd, bench), axis=0)
    tillstand = bygg_tillstand(px)
    if extra:
        tillstand.update(extra(px))
    return dates, px, raw, ar, tillstand


def hantera_handelser(c, signal, dates, px, raw, ar, tillstand, delning):
    rows = []
    for r in c.itertuples():
        pos = dates.searchsorted(r.Publicerat, side="right")  # första handelsdag efter publicering
        if pos >= len(dates) or r.Ticker not in px.columns:
            continue
        entry = dates[pos]
        if pd.isna(px.at[entry, r.Ticker]):
            continue
        rad = {
            "Signal": signal, "Ticker": r.Ticker, "Publicerat": r.Publicerat, "Inträde": entry,
            "Belopp_fonster": r.Belopp_fonster, "Antal_personer": r.Antal_personer,
            "Period": ("före" if r.Publicerat < pd.Timestamp(delning) else "efter") if delning else "alla",
        }
        for dim, (mat, etik) in tillstand.items():
            rad[dim] = etik[int(mat.at[entry, r.Ticker])]
        for namn in HORISONTER:
            rad[f"AR_{namn}"] = ar[namn].at[entry, r.Ticker]
            rad[f"R_{namn}"] = raw[namn].at[entry, r.Ticker]
        rows.append(rad)
    return pd.DataFrame(rows)


# ------------------------------------------------------------ statistik
class Placebo:
    """Slumpdragningar: samma bolag, slumpmässigt inträdesdatum med samma
    tekniska värde (t.ex. 'fall' eller 'over_MA200') som signalen."""
    def __init__(self, dates, ar, tillstand, n, seed=1, start="2024-01-01"):
        self.dates, self.ar, self.tillstand, self.n = dates, ar, tillstand, n
        self.rng = np.random.default_rng(seed)
        self.cache = {}
        self.start = dates.searchsorted(pd.Timestamp(start))   # slumpdatum från första insiderköpet

    def giltiga(self, tk, horisont, dim, varde):
        nyckel = (tk, horisont, dim, varde)
        if nyckel not in self.cache:
            a = self.ar[horisont][tk].to_numpy()
            ok = ~np.isnan(a)
            if dim != "alla":
                mat, etik = self.tillstand[dim]
                kod = {v: k for k, v in etik.items()}[varde]
                ok &= mat[tk].to_numpy() == kod
            ok[:self.start] = False
            self.cache[nyckel] = (np.flatnonzero(ok), a)
        return self.cache[nyckel]

    def medianer(self, tickers, horisont, dim, varde):
        kol = []
        for tk in tickers:
            idx, a = self.giltiga(tk, horisont, dim, varde)
            if len(idx) == 0:
                continue
            kol.append(a[self.rng.choice(idx, size=self.n)])
        if not kol:
            return None
        return np.median(np.vstack(kol), axis=0)  # n medianer


def delmangder(g: pd.DataFrame, dims=DIMENSIONER):
    """(dimension, värde, delmängd): hela mängden och varje tekniskt läge."""
    yield "alla", "alla", g
    for d in dims:
        if d not in g.columns:      # t.ex. Nyhet när nyheter.csv saknas
            continue
        for v, gv in g.groupby(d):
            yield d, v, gv


def sammanfatta(h: pd.DataFrame, placebo: Placebo, kostnad: float) -> pd.DataFrame:
    rader = []
    perioder = [(p, h[h["Period"] == p]) for p in h["Period"].unique()]
    if h["Period"].nunique() > 1:  # "alla" = före + efter ihop
        perioder.append(("alla", h))
    for per, hp in perioder:
        for sig, gs in hp.groupby("Signal"):
            for dim, varde, g in delmangder(gs):
                for namn in HORISONTER:
                    sub = g.dropna(subset=[f"AR_{namn}"])
                    n = len(sub)
                    if n == 0:
                        continue
                    v = sub[f"AR_{namn}"].to_numpy() - kostnad
                    med = float(np.median(v))
                    vk = np.clip(v, *np.quantile(v, [0.01, 0.99])) if n >= 20 else v   # kapad vid 1:a/99:e percentilen
                    medel = float(np.mean(vk))
                    t = float(np.mean(vk) / (np.std(vk, ddof=1) / np.sqrt(n))) if n > 2 and np.std(vk) > 0 else np.nan
                    pm = placebo.medianer(sub["Ticker"], namn, dim, varde)
                    pct = float((pm < (med + kostnad)).mean() * 100) if pm is not None else np.nan
                    rader.append({
                        "Signal": sig, "Period": per, "Dimension": dim, "Varde": varde,
                        "Horisont": namn, "N": n,
                        "Median_AR_%": round(med * 100, 2), "Medel_AR_%": round(medel * 100, 2),
                        "Positiva_%": round(float((v > 0).mean() * 100), 1), "t": round(t, 2),
                        "Placebo_pct": round(pct, 1),
                    })
    return pd.DataFrame(rader)


# ------------------------------------------------------------ stop loss
def simulera_utgang(pris, idxpris, typ, p, hall, fyllning):
    """Simulerar en position från inträde; pris[0] är inträdeskursen.
    Returnerar (avkastning, indexavkastning, dagar, stoppad, atervand) eller None om
    innehavstiden inte är slut och positionen inte stoppats."""
    entry = pris[0]
    peak = entry
    for t in range(1, len(pris)):
        x = pris[t]
        if np.isnan(x):
            continue
        peak = max(peak, x)
        if typ == "fast":
            niva = entry * (1 - p)
        elif typ == "trail":
            niva = peak * (1 - p)
        else:
            niva = -np.inf
        if x <= niva:
            utpris = niva if fyllning == "stopp" else x
            slut = pris[hall] if len(pris) > hall and not np.isnan(pris[hall]) else np.nan
            atervand = bool(slut > utpris) if not np.isnan(slut) else np.nan
            return utpris / entry - 1, idxpris[t] / idxpris[0] - 1, t, True, atervand
    if len(pris) <= hall or np.isnan(pris[hall]):
        return None
    return pris[hall] / entry - 1, idxpris[hall] / idxpris[0] - 1, hall, False, np.nan


def benchmarkbana(px, pos, hall, bench, cache):
    """Benchmarkens kursbana från startdagen pos (relativt, startar på 1). För tvärsnitt
    är det medianen eller kapat medel av alla aktiers relativa kurs; samma bana för alla
    händelser med samma startdag, därför cachas den."""
    if bench not in TVARSNITT:
        b = px[bench].to_numpy()[pos:pos + hall + 1]
        return b / b[0]
    if pos not in cache:
        a = px[aktiekolumner(px)].to_numpy()[pos:pos + hall + 1]
        rel = a / a[0]
        with np.errstate(all="ignore"):
            if bench == MEDIAN:
                cache[pos] = np.nanmedian(np.where(np.isfinite(rel), rel, np.nan), axis=1)
            else:
                cache[pos] = np.nanmean(np.clip(np.where(np.isfinite(rel), rel, np.nan), 0.1, 4.0), axis=1)
    return cache[pos]


def kor_stopp(handelser, dates, px, hall, kostnad, fyllning, bench=INDEX):
    rader = []
    cache = {}
    fel = datafel_kumulativt(px)
    for r in handelser.itertuples():
        pos = dates.get_loc(r.Inträde)
        bana = px[r.Ticker].to_numpy()[pos:pos + hall + 1]
        # Samma urval för alla regler: bara positioner som hunnit få full innehavstid.
        # Annars räknas stoppade nya positioner med men inte icke-stoppade, vilket
        # drar ner stoppresultaten i förhållande till "ingen stopp".
        if len(bana) <= hall or np.isnan(bana[hall]):
            continue
        if fel[r.Ticker].iat[pos + hall] - fel[r.Ticker].iat[pos] > 0:   # troligt datafel i perioden
            continue
        idxbana = benchmarkbana(px, pos, hall, bench, cache)
        for regel, (typ, p) in STOPPREGLER.items():
            res = simulera_utgang(bana, idxbana, typ, p, hall, fyllning)
            if res is None:
                continue
            ret, iret, dagar, stoppad, atervand = res
            rader.append({
                "Signal": r.Signal, "Regel": regel, "Ticker": r.Ticker,
                **{d: getattr(r, d) for d in DIMENSIONER if hasattr(r, d)},
                "Ret": ret - kostnad, "AR": ret - iret - kostnad, "Dagar": dagar,
                "Stoppad": stoppad, "Atervand": atervand,
            })
    return pd.DataFrame(rader)


def sammanfatta_stopp(s: pd.DataFrame) -> pd.DataFrame:
    delar = []
    for sig, gs in s.groupby("Signal"):
        for dim, varde, g in delmangder(gs):
            for regel, gr in g.groupby("Regel", sort=False):
                n = len(gr)
                ret = gr["Ret"]
                stoppade = gr[gr["Stoppad"]]
                medel = ret.clip(*ret.quantile([0.01, 0.99])).mean() if n >= 20 else ret.mean()
                delar.append({
                    "Signal": sig, "Dimension": dim, "Varde": varde, "Regel": regel, "N": n,
                    "Median_ret_%": round(ret.median() * 100, 2),
                    "Medel_ret_%": round(medel * 100, 2),
                    "Median_AR_%": round(gr["AR"].median() * 100, 2),
                    "Positiva_%": round((ret > 0).mean() * 100, 1),
                    "P5_ret_%": round(ret.quantile(0.05) * 100, 1),
                    "Samsta_%": round(ret.min() * 100, 1),
                    "Stoppade_%": round(gr["Stoppad"].mean() * 100, 1),
                    "Medel_dagar": round(gr["Dagar"].mean(), 0),
                    "Stoppade_som_atervande_%": round(float(stoppade["Atervand"].astype(float).mean() * 100), 1) if len(stoppade) else np.nan,
                })
    return pd.DataFrame(delar)


# ------------------------------------------------------------ körning
def kor(kop_fil, tickermap, kurser_fil, delning=None, kostnad=2 * KOSTNAD_PER_AFFAR, placebo_n=300,
        ut_mapp=MAPP, stopp=False, hall=126, fyllning="stangning", nyheter_mapp=MAPP,
        benchmark=INDEX):
    k, tackning = las_kop(kop_fil, tickermap)
    kurser = las_kurser(kurser_fil)
    if INDEX not in kurser.columns:
        raise SystemExit(f"Indexet {INDEX} saknas i kurser.csv")
    delar = [nyhetsdimension(tickermap, Path(nyheter_mapp) / "nyheter.csv", Path(nyheter_mapp) / "nyhetsbolag.csv"),
             likviditetsdimensioner(kurser_fil, Path(kurser_fil).parent / "aktier.csv"),
             rapportdimension(tickermap, Path(nyheter_mapp) / "nyheter.csv")]
    if delar[0] is None:
        print("Obs: nyheter.csv saknas, dimensionen Nyhet hoppas över (kör nyheter.py).")
    if delar[1] is None:
        print("Obs: kurser.csv saknar volym, Likviditet och Storlek hoppas över (kör kurser.py).")
    delar = [x for x in delar if x is not None]
    extra = (lambda px: {k: v for x in delar for k, v in x(px).items()}) if delar else None
    dates, px, raw, ar, tillstand = bygg_marknad(kurser, extra, benchmark)

    alla = []
    for namn, (typ, param) in SIGNALER.items():
        c = karens(kandidater(k, typ, param))
        if c.empty:
            continue
        h = hantera_handelser(c, namn, dates, px, raw, ar, tillstand, delning)
        print(f"{namn:15s} {len(c):5d} händelser, {len(h):5d} med kurs vid inträdet")
        alla.append(h)
    if not alla:
        print("Inga händelser.")
        return None, None, None
    handelser = pd.concat(alla, ignore_index=True)
    placebo = Placebo(dates, ar, tillstand, placebo_n, start=k["Publicerat"].min())
    res = sammanfatta(handelser, placebo, kostnad)
    handelser.to_csv(Path(ut_mapp) / "backtest_handelser.csv", index=False)
    res.to_csv(Path(ut_mapp) / "backtest_resultat.csv", index=False)
    print(f"\nTäckning: {tackning['Köp med ticker']} av {tackning['Köp']} köp har ticker "
          f"({tackning['Köp med ticker'] / max(tackning['Köp'], 1) * 100:.0f} %); "
          f"{tackning['Borttagna för stora belopp']} köp över {MAX_BELOPP/1e6:.0f} Mkr borttagna som prisfel")
    # survivorship: andel köp (före ticker-filtret) som har ticker och kurser, per år
    tk, ar_ = tackning["Ticker serie"], tackning["Ar serie"]
    med_kurs = tk.isin(set(kurser.columns))
    tab = pd.DataFrame({"år": ar_, "ticker": tk.notna(), "kurs": med_kurs}).groupby("år").agg(
        köp=("ticker", "size"), med_ticker=("ticker", "mean"), med_kurs=("kurs", "mean"))
    print("Andel av alla köp med ticker och med kurser, per år (survivorship: lägre i början):")
    print((tab.assign(med_ticker=(tab.med_ticker * 100).round(1), med_kurs=(tab.med_kurs * 100).round(1))).to_string())

    stoppres = None
    if stopp:
        s = kor_stopp(handelser, dates, px, hall, kostnad, fyllning, benchmark)
        stoppres = sammanfatta_stopp(s)
        stoppres.to_csv(Path(ut_mapp) / "backtest_stopp.csv", index=False)
    return handelser, res, stoppres


def main():
    global BRUSFILTER
    ap = argparse.ArgumentParser()
    ap.add_argument("--kop", default=str(MAPP / "insiderkop.csv"))
    ap.add_argument("--tickermap", default=str(MAPP / "ticker_map.csv"))
    ap.add_argument("--kurser", default=str(MAPP / "kurser.csv"))
    ap.add_argument("--delning", default=None, help="Datum: före = reglerna, efter = test (t.ex. 2026-01-01)")
    ap.add_argument("--kostnad", type=float, default=2 * KOSTNAD_PER_AFFAR,
                    help="Total kostnad för köp + sälj som andel, standard 0,16 %% (0,08 %% per affär)")
    ap.add_argument("--placebo", type=int, default=300)
    ap.add_argument("--utan-brusfilter", action="store_true", help="Behåll arbetstagarrep., sparprogram och regelbundna sparare")
    ap.add_argument("--stopp", action="store_true", help="Simulera stop loss")
    ap.add_argument("--hall", type=int, default=126, help="Max innehavstid i handelsdagar (standard 126 = 6 mån)")
    ap.add_argument("--benchmark", default=INDEX,
                    help="Jämförelse: ^OMXSPI (standard), ^OMXSSCPI (Small Cap), MEDIAN (medianaktien i datan över samma period), MEDEL (genomsnittsaktie, kapad) eller annan ticker i kurser.csv")
    ap.add_argument("--fyllning", choices=["stangning", "stopp"], default="stangning")
    a = ap.parse_args()
    if a.utan_brusfilter:
        BRUSFILTER = False

    _, res, stoppres = kor(a.kop, a.tickermap, a.kurser, a.delning, a.kostnad, a.placebo,
                           MAPP, a.stopp, a.hall, a.fyllning, benchmark=a.benchmark)
    if res is None:
        return
    pd.set_option("display.width", 220, "display.max_columns", None, "display.max_rows", 600)
    visa = res[res["Dimension"] == "alla"]
    print(f"\nÖveravkastning mot {a.benchmark} efter kostnad, alla händelser.")
    print("Per horisont: Median_AR_% | andel som slog index (Positiva_%) | Placebo_pct | N")
    for per, gp in visa.groupby("Period", sort=False):
        bred = gp.pivot(index="Signal", columns="Horisont", values=["Median_AR_%", "Positiva_%", "Placebo_pct", "N"])
        ut = pd.DataFrame(index=bred.index)
        for h in HORISONTER:
            if h in bred["N"].columns:
                ut[h] = [f"{m:6.1f} | {p:4.0f} % | {pl:4.0f} | {int(n):5d}" if pd.notna(m) else ""
                         for m, p, pl, n in zip(bred["Median_AR_%"][h], bred["Positiva_%"][h],
                                                bred["Placebo_pct"][h], bred["N"][h])]
        print(f"\nPeriod: {per}")
        print(ut.reindex([x for x in SIGNALER if x in ut.index]).to_string())
    print("\nTekniska lägen (trend, RSI, fall) finns i backtest_resultat.csv: filtrera på Dimension.")
    if stoppres is not None:
        v = stoppres[stoppres["Dimension"] == "alla"].drop(columns=["Dimension", "Varde"])
        print(f"\nStop loss-simulering (max {a.hall} dagar, fyllning: {a.fyllning}):")
        print(v.to_string(index=False))
        print("\nFull tabell i backtest_stopp.csv")
    print("Händelser i backtest_handelser.csv")


if __name__ == "__main__":
    main()
