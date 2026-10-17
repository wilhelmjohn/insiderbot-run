"""Bygger Biotechscreen: poäng för rusningsläge, varningar och grafer för svenska biotech- och läkemedelsbolag.
Läser bio_ohlc.csv, bio_info.csv, bio_katalysatorer.csv (händelser du eller jag lagt in), insiderkop.csv, insiderhandel.csv,
blankning.csv och ticker_map.csv. Kör: python bio_bygg.py [utfil]   (standard /tmp/biotech/biotech.html)"""
import json, re, sys
from pathlib import Path
import numpy as np, pandas as pd
import trapp_bygg as tb
import bio_sf
import bio_extra as bx

HER = Path(__file__).resolve().parent
UT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/biotech/biotech.html")
IDAG = pd.Timestamp.now(tz="Europe/Stockholm").tz_localize(None).normalize()
N = 260

# Branschsnitt för chans att nå nästa steg (BIO/Informa 2011-2020, avrundat). Gäller läkemedelsprojekt i allmänhet, inte det enskilda bolaget.
# Preklinik saknar ett enhetligt branschsnitt och visas därför utan procent.
FAS = {"marknad": ("Godkänd, säljs", None), "pre": ("Preklinik", None), "fas1": ("Fas 1", 52), "fas2": ("Fas 2", 29), "fas3": ("Fas 3", 58), "reg": ("Registrering (NDA/BLA)", 90)}
# Hur intressant en fas är för handel inför milstolpar (Fas 2/3 historiskt bäst risk/reward), samt ordning för "bolagets längst gångna fas"
FAS_VIKT = {"fas3": 15, "fas2": 15, "reg": 12, "fas1": 8, "marknad": 6, "pre": 3}
FAS_ORD = {"marknad": 0, "reg": 5, "fas3": 4, "fas2": 3, "fas1": 2, "pre": 1}
BOLAGSFAS = {"fas3": "Fas 3", "fas2": "Fas 2", "reg": "Registrering", "fas1": "Fas 1", "marknad": "Kommersiell", "pre": "Preklinik"}

def nz(x, d=None):
    return d if x is None or (isinstance(x, float) and np.isnan(x)) else x

def katalysatorfonster(d, planerad):
    """Poäng (0-35) för var i tid nästa händelse ligger. 45-70 dagar fram är den period då spekulativ volym brukar byggas, 30-90 d är run-up-zonen.
    Planerad milstolpe (bara en period känd) väger mindre än datumsatt."""
    if d is not None:
        if 45 <= d <= 70: return 35, "Optimalt fönster 45-70 d"
        if 30 <= d <= 90: return 27, "Run-up-zon 30-90 d"
        if 15 <= d < 30: return 18, "Närmar sig: 15-29 d"
        if 0 <= d < 15: return 8, "Mycket nära: <15 d"
        return 5, "Längre fram än 90 d"
    if planerad is not None:
        return (12 if planerad <= 90 else 6 if planerad <= 180 else 2), "Bara period känd"
    return 0, "Ingen känd händelse"

def vad_poang(z):
    """Del 1, VAD (0-100): pipeline och milstolpar 35, finansiell uthållighet 35, ägare och kvalitet 30."""
    P = {}
    d, pl = z["besked_dagar"], z["plan_dagar"]
    kat = 0 if d is None and pl is None else (12 if d <= 30 else 10 if d <= 90 else 6 if d <= 180 else 3) if d is not None else (5 if pl <= 90 else 3 if pl <= 180 else 1)
    if z["rapport"] is not None and 0 < z["rapport"] <= 45 and kat < 3: kat = 3
    P["fas"] = z["fas_vikt"]
    P["katalysator"] = kat
    P["potential"] = (4 if z["orphan"] == "Ja" else 0) + (4 if z["behov"] == "Ja" else 0)
    rw, sj = z["runway"], z["sjalvfin"]
    k = 35 if sj else 9 if rw is None else 35 if rw >= 24 else 29 if rw >= 18 else 21 if rw >= 12 else 12 if rw >= 9 else 5 if rw >= 6 else 0
    if z["utsp1"] is not None and z["utsp1"] > 30: k -= 5
    if not sj:
        if z["konv_lan"] == "Ja": k -= 3
        if z["to_ext"] == "Ja": k -= 2
    P["kassa"] = max(0, k)
    kop = z["kop180"]
    P["insider"] = 12 if kop >= 1e6 else 8 if kop >= 2.5e5 else 4 if kop > 0 else 0
    inst = z["inst"]
    P["agare"] = 8 if z["specialist"] else 5 if (inst is not None and inst >= 20) else 3 if (inst is not None and inst >= 5) else 0
    b = z["blank"]
    P["blankning"] = 4 if b is None else (5 if (b >= 5 and z["kop"] > 0) else 2 if b >= 5 else 4)
    o = z["oms"]
    P["likviditet"] = 5 if o >= 5 else 3 if o >= 1 else 1 if o >= 0.3 else 0
    return P

def nar_poang(z):
    """Del 2, NÄR (0-100): katalysatorfönster 35, trend (SMA20/50) 20, RSI 15, konsolidering och volym 20, ej redan rusat 10."""
    P = {}
    P["fonster"] = z["fonster_p"]
    t = z["tek"]
    c, s20, s50 = z["kurs"], t["sma20"], t["sma50"]
    if s20 is None or s50 is None: P["trend"] = 0
    elif c > s20 and c > s50 and s20 > s50: P["trend"] = 20
    elif c > s20 and c > s50: P["trend"] = 14
    elif c > s50: P["trend"] = 8
    else: P["trend"] = 0
    r = t["rsi"]
    base = 0 if r is None else 15 if 40 <= r <= 65 else 10 if 65 < r <= 70 else 8 if 30 <= r < 40 else 6 if r < 30 else 0
    P["rsi"] = 15 if (t["diverg"] and (r is not None and r <= 55)) else base
    P["konsolidering"] = 20 if (t["tight"] and t["volstiger"]) else 10 if t["tight"] else 5 if (t["volstiger"] and z["ret20"] > 0) else 0
    r20 = z["ret20"]
    P["rusat"] = 10 if r20 <= 10 else 6 if r20 <= 25 else 2 if r20 <= 40 else 0
    return P

def teknik(g, ret20):
    """Tekniska timingindikatorer ur dagsdata: SMA20/50, RSI14, positiv divergens, snäv konsolidering med stigande volym."""
    c, h, l, v = g.Close, g.High, g.Low, g.Volume
    s20 = c.rolling(20).mean(); s50 = c.rolling(50).mean(); rs = tb.rsi(c)
    last = float(c.iloc[-1])
    rng = float((h.tail(20).max() - l.tail(20).min()) / c.tail(20).mean() * 100)
    v10, v40 = float(v.tail(10).mean()), float(v.iloc[-50:-10].mean()) if len(v) >= 50 else 0.0
    vr = v10 / v40 if v40 > 0 else None
    # positiv divergens: lägre lägsta kurs de senaste 20 dagarna än de 20 dagarna innan, men högre lägsta RSI
    A_l, B_l = float(l.iloc[-40:-20].min()), float(l.iloc[-20:].min())
    A_r, B_r = float(rs.iloc[-40:-20].min()), float(rs.iloc[-20:].min())
    diverg = bool(B_l < A_l * 0.99 and B_r > A_r + 3 and float(rs.iloc[-1]) < 55)
    return dict(sma20=None if pd.isna(s20.iloc[-1]) else round(float(s20.iloc[-1]), 3), sma50=None if pd.isna(s50.iloc[-1]) else round(float(s50.iloc[-1]), 3),
                rsi=None if pd.isna(rs.iloc[-1]) else round(float(rs.iloc[-1]), 1), diverg=diverg, range20=round(rng, 1), tight=bool(rng <= 15 and abs(ret20) <= 12),
                volratio=None if vr is None else round(vr, 2), volstiger=bool(vr is not None and 1.15 <= vr <= 2.5)), rs

def sf_data(t, SFT, SFS, dsf):
    """Kvalitativ sammanfattning för sidan: texten ur bio_sammanfattning.json plus vad som hänt sedan den skrevs (räknas ut färskt vid varje bygge)."""
    x = SFT.get(t)
    if not x: return None
    alder = int((IDAG - pd.Timestamp(x["uppdaterad"])).days)
    r = dict(kort=x["kort"], tolk_kort=x.get("tolk_kort", ""), bild=x["bild"], belagt=x["belagt"], tolkning=x["tolkning"], bevaka=x.get("bevaka", []), kallor=x["kallor"], upd=x["uppdaterad"], alder=alder, ny=None, stale=False)
    s = SFS.get(t)
    if not s: return r
    n = bio_sf.nytt_sedan(dsf, t, s, alder)
    ny = n["ny"]
    if len(ny):
        ny = ny.assign(st=ny.apply(bio_sf.pmstark, axis=1)).sort_values(["st", "Datum"], ascending=[False, False]).drop_duplicates(["Datum", "Flagga"])
    vis = ny.head(6).sort_values("Datum", ascending=False) if len(ny) else ny
    r["ny"] = dict(sedan=s["datum"], kurs0=s["kurs"], kurs=round(n["kurs_nu"], 3), pct=None if n["tot"] is None else round(n["tot"], 1),
                   dagx=None if n["dagx"] is None else round(n["dagx"], 1), dagd=None if n["dagd"] is None else str(n["dagd"].date()),
                   pm=[dict(d=str(q.Datum.date()), r=q.Rubrik, l=q.Lank, f=[f for f in q.Flagga.split(",") if f], st=bool(q.st)) for q in vis.itertuples()],
                   pm_n=int(len(ny)), pm_starka=int(len(n["nys"].drop_duplicates(["Datum", "Flagga"]))) if len(n["nys"]) else 0,
                   kop=round(n["kop"]), sal=round(n["sal"]), ins_n=int(len(n["ins"])))
    r["stale"] = bool(n["skal"])
    return r

def main():
    o = pd.read_csv(HER / "bio_ohlc.csv", parse_dates=["Datum"]).sort_values(["Ticker", "Datum"])
    inf = pd.read_csv(HER / "bio_info.csv", parse_dates=["Nasta_rapport"]).set_index("Ticker")
    kat = pd.read_csv(HER / "bio_katalysatorer.csv", parse_dates=["Datum"])
    pip = pd.read_csv(HER / "bio_pipeline.csv", parse_dates=["Milstolpe_senast"])
    tm, bl, h, ik = tb.las_stod()
    bo = pd.read_csv(HER / "bio_bolag.csv").fillna("").set_index("Ticker")
    pmf = HER / "bio_pm.csv"
    pm = pd.read_csv(pmf, parse_dates=["Datum"]).fillna("") if pmf.exists() else pd.DataFrame(columns=["Ticker", "Datum", "Rubrik", "Lank", "Flagga"])
    cal = sorted(o.Datum.unique())[-N:]; ci = pd.DatetimeIndex(cal)
    SFT, SFS = bio_sf.jlas(bio_sf.TEXT), bio_sf.jlas(bio_sf.STATE)
    IDX, ST, SL = bx.las_index(), bx.las_studier(), bx.las_studielogg()
    FLG = bx.las_flaggning()
    FAS_PROB = {"fas2": FAS["fas2"], "fas3": FAS["fas3"]}
    dsf = dict(tm=tm, h=h, pm=pm, p=o.pivot_table(index="Datum", columns="Ticker", values="Close"))
    out = []
    for t, g in o.groupby("Ticker"):
        g = g.dropna(subset=["Close"]).drop_duplicates("Datum").reset_index(drop=True)
        if len(g) < 60 or t not in inf.index: continue
        i = inf.loc[t]; c = g.Close; last = float(c.iloc[-1])
        ret = lambda n: float((last / c.iloc[-1 - n] - 1) * 100) if len(c) > n else 0.0
        hi52, lo52 = float(g.High.tail(252).max()), float(g.Low.tail(252).min())
        oms = float((g.Close * g.Volume).tail(60).median() / 1e6)
        v21 = g.Volume.tail(21).iloc[:-1].mean(); vr = float(g.Volume.iloc[-1] / v21) if v21 > 0 else None
        cash = nz(i.totalCash); ocf = nz(i.operatingCashflow); rev = nz(i.totalRevenue)
        sjalvfin = ocf is not None and ocf > 0
        burn = -ocf if (ocf is not None and ocf < 0) else None
        runway = round(cash / burn * 12, 1) if (cash is not None and burn) else None
        isin = tm.ISIN.get(t)
        d90 = IDAG - pd.Timedelta(days=90)
        kp = ik[(ik.ISIN == isin) & (ik.Publiceringsdatum >= d90)] if isin == isin else ik.iloc[0:0]
        sl = h[(h.ISIN == isin) & (h.Publiceringsdatum >= d90) & (h.Karaktär == "Avyttring")] if isin == isin else h.iloc[0:0]
        kop = float(kp.Belopp.sum()) if len(kp) else 0.0
        namn = tm.Bolag.get(t, i.longName); b = bl[(bl.k == tb.norm(namn)) | (bl.k == tb.norm(i.longName))]
        blank = float(b.Niva.iloc[0]) if len(b) else None; blank_f = nz(float(b.Forandring_20d.iloc[0])) if len(b) else None
        ev = kat[(kat.Ticker == t) & (kat.Datum >= IDAG)].sort_values("Datum")
        ev_l = [dict(d=str(r.Datum.date()), h=r.Handelse, typ=r.Typ, k=r.Kalla, s=r.Sakerhet, dg=int((r.Datum - IDAG).days),
                     fas="" if pd.isna(r.Fas) else FAS.get(str(r.Fas), ("", None))[0], p=None if pd.isna(r.Fas) else FAS.get(str(r.Fas), ("", None))[1]) for r in ev.itertuples()]
        pl = pip[pip.Ticker == t]
        pipeline = [dict(p=r.Projekt, i=nz(r.Indikation, ""), fk="" if pd.isna(r.Fas) else str(r.Fas), fas="" if pd.isna(r.Fas) else FAS.get(str(r.Fas), (str(r.Fas), None))[0],
                         pr=None if pd.isna(r.Fas) else FAS.get(str(r.Fas), ("", None))[1], m=nz(r.Nasta_milstolpe, ""),
                         dg=None if pd.isna(r.Milstolpe_senast) else int((r.Milstolpe_senast - IDAG).days), k=nz(r.Kalla, ""), u=str(r.Uppdaterad)[:10]) for r in pl.itertuples()]
        # planerade milstolpar utan exakt datum räknas med som händelser (senast-datum), exakta händelser går först
        for p in pipeline:
            exakt = {e["d"] for e in ev_l if e["typ"] != "Planerat"}
            if p["dg"] is not None and p["dg"] >= 0 and p["m"] and p["fas"] in ("Fas 1", "Fas 2", "Fas 3", "Registrering (NDA/BLA)") and str((IDAG + pd.Timedelta(days=p["dg"])).date()) not in exakt:
                ev_l.append(dict(d=str((IDAG + pd.Timedelta(days=p["dg"])).date()), h=f"{p['p']}: {p['m']}", typ="Planerat", k=p["k"], s="Bolagets uppgift, tidpunkt ca", fas=p["fas"], p=p["pr"], dg=p["dg"]))
        # studieregistret (ClinicalTrials.gov): primära slutdatum för fas 2/3 räknas som planerade händelser, plus varningar och signaler
        srows, sev, sW, sU, sandr = bx.studie_info(t, ST, SL, IDAG, FAS_PROB)
        ev_l += sev
        ev_l.sort(key=lambda e: e["dg"])
        ex_l = [e["dg"] for e in ev_l if e["typ"] != "Planerat"]; pl_l = [e["dg"] for e in ev_l if e["typ"] == "Planerat"]
        besked = min(ex_l) if ex_l else None; plan = min(pl_l) if pl_l else None
        rap = int((i.Nasta_rapport - IDAG).days) if pd.notna(i.Nasta_rapport) else None
        if rap is not None and rap < 0: rap = None
        # Del 1/2: bolagsfas, bolagsdata, teknik
        koder = [x["fk"] for x in pipeline if x["fk"] in FAS]
        kliniska = [k_ for k_ in koder if k_ != "marknad"]
        lead_kod = max(kliniska, key=lambda k_: FAS_ORD[k_]) if kliniska else ("marknad" if "marknad" in koder else "")
        fas_vikt = max([FAS_VIKT[k_] for k_ in koder], default=0)
        bb = bo.loc[t] if t in bo.index else None
        sp = str(bb.Specialistagare) if bb is not None else ""
        sp_del = sp.split("Specialist:")[-1].strip() if "Specialist:" in sp else ""
        specialist = bool(sp_del) and not re.match(r"(inga|ingen|ej|okänt)", sp_del, re.I)
        kp180 = ik[(ik.ISIN == isin) & (ik.Publiceringsdatum >= IDAG - pd.Timedelta(days=180))] if isin == isin else ik.iloc[0:0]
        tek, rs_ = teknik(g, round(ret(20), 1))
        ev0 = next((e for e in ev_l if e["typ"] != "Planerat"), None) or (ev_l[0] if ev_l else None)
        fonster_p, fonster_t = katalysatorfonster(besked, plan)
        sweet = bool(besked is not None and 45 <= besked <= 70 and ev0 is not None and ev0.get("fas") in ("Fas 2", "Fas 3"))
        z = dict(t=t, namn=re.sub(r"\s*\(publ\.?\)", "", str(i.longName)).strip(), industri=i.industry, mc=round(nz(i.marketCap, 0) / 1e9, 2), kurs=round(last, 2),
                 d1=round(ret(1), 1), ret20=round(ret(20), 1), ret60=round(ret(60), 1), ret250=round(ret(250), 1),
                 fran_topp=round((last / hi52 - 1) * 100, 1), over_botten=round((last / lo52 - 1) * 100, 1), oms=round(oms, 2), volspik=None if vr is None else round(vr, 1),
                 cash=None if cash is None else round(cash / 1e6), burn=None if burn is None else round(burn / 1e6), runway=runway, sjalvfin=bool(sjalvfin),
                 rev=None if rev is None else round(rev / 1e6), utsp1=nz(i.Utspadning_1ar), utsp3=nz(i.Utspadning_3ar),
                 kop=round(kop), kop_n=int(len(kp)), sal=round(float(sl.Volym.mul(sl.Pris).sum())) if len(sl) else 0,
                 blank=blank, blank_f=blank_f, inst=None if nz(i.heldPercentInstitutions) is None else round(i.heldPercentInstitutions * 100, 1),
                 insid=None if nz(i.heldPercentInsiders) is None else round(i.heldPercentInsiders * 100, 1),
                 mal=nz(i.targetMeanPrice), analytiker=nz(i.numberOfAnalystOpinions), rek=nz(i.recommendationKey),
                 besked=ev_l, pipeline=pipeline, besked_dagar=besked, plan_dagar=plan, rapport=rap,
                 lead_kod=lead_kod, lead=BOLAGSFAS.get(lead_kod, "Ingen aktiv fas"), fas_vikt=fas_vikt, fas_lista=[BOLAGSFAS[k_] for k_ in sorted(set(koder), key=lambda k_: -FAS_ORD[k_])],
                 fas23=bool({"fas2", "fas3"} & set(koder)), sweet=sweet, fonster_p=fonster_p, fonster_t=fonster_t, tek=tek, specialist=specialist,
                 skuld=None if nz(i.totalDebt) is None else round(i.totalDebt / 1e6), kop180=round(float(kp180.Belopp.sum())) if len(kp180) else 0, kop180_n=int(len(kp180)),
                 orphan=str(bb.Orphan) if bb is not None else "Okänt", orphan_d=str(bb.Orphan_detalj) if bb is not None else "", behov=str(bb.Behov) if bb is not None else "Okänt",
                 behov_t=str(bb.Behov_text) if bb is not None else "", to_ext=str(bb.TO_ext) if bb is not None else "Okänt", konv_lan=str(bb.Konv_lan) if bb is not None else "Okänt",
                 to_text=str(bb.TO_konv) if bb is not None else "", agare_text=sp, indikation=str(bb.Huvudindikation) if bb is not None else "", bolag_kalla=str(bb.Kalla) if bb is not None else "")
        PV = vad_poang(z); PN = nar_poang(z); z["PV"] = PV; z["PN"] = PN
        z["vad"] = int(round(sum(PV.values()))); z["nar"] = int(round(sum(PN.values()))); z["samlad"] = int(round((z["vad"] * z["nar"]) ** 0.5))
        W, U = list(sW), list(sU)
        tk = z["tek"]
        if runway is not None and runway < 6: W.append(("Kassa räcker " + (f"{runway:.0f} mån" if runway >= 1 else "<1 mån"), "Under 6 månaders kassa: akut emission, ofta med stor rabatt, är mycket sannolik.", "hard"))
        elif runway is not None and runway < 9: W.append((f"Kassa räcker {runway:.0f} mån", "6-9 månaders kassa: nyemission är sannolik inom kort och brukar ske till rabatt.", "hard"))
        elif runway is not None and runway < 12: W.append((f"Kassa räcker {runway:.0f} mån", "9-12 månaders kassa: räkna med en emission inom ett år.", "mjuk"))
        if cash is None: W.append(("Kassa saknas", "Yahoo saknar kassadata. Läs bolagets senaste rapport.", "mjuk"))
        if z["konv_lan"] == "Ja" and not sjalvfin: W.append(("Konvertibel/lån", "Utestående konvertibel eller lån som kan ge nya aktier eller kräva amortering. Se avsnittet om finansiell uthållighet.", "mjuk"))
        if z["to_ext"] == "Ja" and not sjalvfin: W.append(("Utestående TO", "Teckningsoptioner till investerare eller långivare kan ge nya aktier och pressa kursen när de löses in.", "mjuk"))
        if z["skuld"] is not None and cash is not None and z["skuld"] * 1e6 > cash and not sjalvfin: W.append((f"Skulder {z['skuld']} MSEK > kassa", "Yahoos skuldpost (inkl. leasing) är större än kassan.", "mjuk"))
        if z["ret20"] >= 40: W.append((f"+{z['ret20']:.0f} % på 20 d", "Kursen har redan rusat. Mycket kan vara inprisat inför ett besked.", "hard"))
        elif z["ret20"] >= 25: W.append((f"+{z['ret20']:.0f} % på 20 d", "Stor uppgång på kort tid.", "mjuk"))
        if tk["rsi"] is not None and tk["rsi"] > 70: W.append((f"Överköpt, RSI {tk['rsi']:.0f}", "RSI över 70: kortsiktigt överköpt. Vänta in en vila innan du köper.", "mjuk"))
        if tk["sma50"] is not None and z["kurs"] < tk["sma50"]: W.append(("Under 50-dagarssnittet", "Kursen handlas under SMA 50: den kortsiktiga trenden är inte bekräftad.", "mjuk"))
        if z["utsp1"] is not None and z["utsp1"] >= 50: W.append((f"Utspädning {z['utsp1']:.0f} % på 1 år", "Antalet aktier har ökat kraftigt: tidigare emissioner har spätt ut ägarna.", "hard"))
        elif z["utsp1"] is not None and z["utsp1"] >= 20: W.append((f"Utspädning {z['utsp1']:.0f} % på 1 år", "Antalet aktier har ökat tydligt det senaste året.", "mjuk"))
        if besked is not None and besked <= 7: W.append((f"Besked om {besked} d", "Händelsen är nära. Binärt utfall: ta hem del av vinsten och håll posten liten.", "mjuk"))
        if besked is None and plan is None and rap is None: W.append(("Ingen känd händelse", "Ingen katalysator inlagd. Läs bolagets pipeline och lägg in datum.", "mjuk"))
        if blank is not None and blank >= 8: W.append((f"Blankat {blank:.1f} %", "Hög blankning: proffs satsar på fall." + (" Ökar." if blank_f and blank_f >= 0.5 else ""), "mjuk"))
        if oms < 1: W.append((f"Låg omsättning ({oms:.1f} MSEK/dag)", "Svårt att köpa och sälja stora poster utan att flytta kursen.", "mjuk"))
        if z["sal"] >= 1e6: W.append((f"Insiderförsäljning {z['sal'] / 1e6:.1f} MSEK", "Ledande befattningshavare sålde senaste 90 dagarna.", "mjuk"))
        if z["fran_topp"] <= -60: W.append((f"{z['fran_topp']:.0f} % från årstoppen", "Kraftigt nedtryckt kurs. Kan vara kapitulation eller ett misslyckande som inte vänt.", "mjuk"))
        if z["lead_kod"] in ("", "pre", "marknad") and not z["fas23"]: pass
        if sweet: U.append((f"Sweet spot: {besked} d", f"Datumsatt händelse ({ev0['h'][:70]}) om {besked} dagar i {ev0['fas']}: run-up-fönstret 45-70 dagar före data."))
        elif besked is not None and 30 <= besked <= 90: U.append((f"Run-up-zon: {besked} d", "Händelsen ligger 30-90 dagar fram, då spekulativ volym brukar byggas."))
        if z["fas23"]: U.append(("Fas 2/3", "Har projekt i fas 2 eller 3: historiskt bäst risk/reward inför milstolpar."))
        if z["orphan"] == "Ja": U.append(("Särläkemedel", z["orphan_d"] or "Särläkemedelsstatus (Orphan Drug Designation)."))
        if z["behov"] == "Ja": U.append(("Obesvarat behov", z["behov_t"] or "Området saknar tillräcklig behandling."))
        if tk["diverg"]: U.append(("Positiv RSI-divergens", "Kursen gjorde en lägre botten men RSI en högre: säljtrycket avtar."))
        if tk["tight"] and tk["volstiger"]: U.append(("Ackumulering?", f"Kursen rör sig sidledes ({tk['range20']:.0f} % spann på 20 d) medan volymen stiger ({tk['volratio']:.1f}× snittet): kan tyda på ackumulering."))
        if tk["sma20"] is not None and tk["sma50"] is not None and z["kurs"] > tk["sma20"] > tk["sma50"]: U.append(("Över SMA 20 och 50", "Kursen ligger över båda glidande medelvärdena och SMA 20 över SMA 50: kortsiktig trend bekräftad."))
        if specialist: U.append(("Specialistägare", sp_del[:140]))
        if z["kop180"] >= 2.5e5: U.append((f"Insynsköp 6 mån {z['kop180'] / 1e6:.1f} MSEK", f"{z['kop180_n']} köp av ledande befattningshavare senaste 180 dagarna."))
        if sjalvfin: U.append(("Självfinansierad", "Positivt kassaflöde från verksamheten: ingen emissionsrisk på kort sikt."))
        elif runway is not None and runway >= 24: U.append((f"Kassa för {runway:.0f} mån", "Pengarna räcker över två år med dagens förbrukning."))
        if blank is not None and blank >= 5 and kop > 0: U.append(("Möjlig blankningsklämma", "Hög blankning och insynsköp samtidigt: en positiv nyhet kan tvinga blankare att köpa tillbaka."))
        if blank_f is not None and blank_f <= -0.5: U.append((f"Blankning minskar {blank_f:+.1f} pp", "Blankarna täcker."))
        # pressmeddelanden: flaggade först (en per dag och flaggtyp, svenska/engelska dubbletter bort), annars de senaste
        q = pm[(pm.Ticker == t) & (pm.Datum >= IDAG - pd.Timedelta(days=45))].sort_values("Datum", ascending=False)
        q = q.assign(dg=(IDAG.normalize() - q.Datum).dt.days)
        fl = q[q.Flagga != ""].drop_duplicates(["Datum", "Flagga"])
        vis = pd.concat([fl.head(5), q[q.Flagga == ""].head(max(0, 3 - len(fl.head(5))))]).sort_values("Datum", ascending=False)
        z["pm"] = [dict(dg=int(r.dg), r=r.Rubrik, f=[x for x in r.Flagga.split(",") if x], l=r.Lank) for r in vis.itertuples()]
        STARKA = {"FDA", "Studieresultat", "Emission", "Avtal", "Godkännande", "Varning", "Notering"}
        z["pm_ny"] = int(sum(1 for r in fl.itertuples() if r.dg <= 7 and STARKA & set(r.Flagga.split(","))))
        em = fl[(fl.dg <= 21) & fl.Flagga.str.contains("Emission")]
        if len(em): W.append(("Emission aviserad", f"Pressmeddelande om emission {int(em.dg.iloc[0])} d sedan: kursen brukar pressas när rabatterade aktier erbjuds. Läs villkoren.", "mjuk"))
        va = fl[(fl.dg <= 21) & fl.Flagga.str.contains("Varning")]
        if len(va): W.append(("Negativt besked", f"Pressmeddelande med avbrott/miss {int(va.dg.iloc[0])} d sedan: {va.Rubrik.iloc[0][:80]}", "hard"))
        # nyare flaggor: planerad notering/emission, ägarförändring (flaggning), återköp
        q90 = pm[(pm.Ticker == t) & (pm.Datum >= IDAG - pd.Timedelta(days=90))].sort_values("Datum", ascending=False)
        nt = q90[q90.Flagga.str.contains("Notering")]
        if len(nt): W.append(("Notering/emission aviserad", f"Pressmeddelande {int((IDAG - nt.Datum.iloc[0]).days)} d sedan om planerad notering eller handelsplatsbyte: {nt.Rubrik.iloc[0][:90]}. En notering som kombineras med nyemission kan späda ut ägarna. Läs villkoren.", "mjuk"))
        ag = q90[q90.Flagga.str.contains("Ägare") & (q90.Datum >= IDAG - pd.Timedelta(days=30))]
        if len(ag): W.append(("Ägarförändring flaggad", f"Pressmeddelande {int((IDAG - ag.Datum.iloc[0]).days)} d sedan om ändrat innehav: {ag.Rubrik.iloc[0][:90]}. Se vem som köpt eller sålt och hur stor posten är.", "mjuk"))
        fi_ = bx.flagg_info(t, FLG, IDAG)
        if fi_ and fi_["dg"] <= 30 and not len(ag):
            W.append(("Flaggning publicerad", f"FI:s register Börsinformation visar ett flaggningsmeddelande publicerat {fi_['d']} ({fi_['dg']} d sedan; {fi_['n90']} senaste 90 d): någon har passerat en flaggningsgräns, uppåt eller nedåt. Vem och hur mycket står i registret, inte här.", "mjuk"))
        ar = q90[q90.Flagga.str.contains("Återköp") & (q90.Datum >= IDAG - pd.Timedelta(days=60))]
        if len(ar): U.append(("Återköp av egna aktier", f"Pressmeddelande {int((IDAG - ar.Datum.iloc[0]).days)} d sedan: {ar.Rubrik.iloc[0][:90]}. Bolaget köper tillbaka aktier."))
        z["ned"] = [dict(l=a, t=b_, sev=c_) for a, b_, c_ in W]; z["upp"] = [dict(l=a, t=b_) for a, b_ in U]
        z["studier"] = srows; z["studie_andr"] = sandr
        z["sek"] = bx.rel_index(g, IDX)
        hard = any(w[2] == "hard" for w in W)
        z["lage"] = "Röd varning" if hard else "Bra grund och timing" if z["vad"] >= 50 and z["nar"] >= 50 else "Bra grund, vänta på timing" if z["vad"] >= 50 else "Timing ok, svag grund" if z["nar"] >= 50 else "Svag grund och timing"
        gg = g.set_index("Datum").reindex(ci).ffill()
        rd = lambda s_: [None if pd.isna(v) else round(float(v), 2) for v in s_]
        evk = []
        if isin == isin:
            xx = ik[ik.ISIN == isin]
            for dt_, gx in xx.groupby(xx.Transaktionsdatum.where(xx.Transaktionsdatum.notna(), xx.Publiceringsdatum)):
                if dt_ >= ci[0]: evk.append([min(int(ci.searchsorted(dt_)), len(ci) - 1), round(float(gx.Belopp.sum()))])
        z["ch"] = dict(c=rd(gg.Close), h=rd(gg.High), l=rd(gg.Low), v=[int(v) if v == v else 0 for v in g.set_index("Datum").Volume.reindex(ci).fillna(0)],
                       m20=rd(g.set_index("Datum").Close.rolling(20).mean().reindex(ci).ffill()), m50=rd(g.set_index("Datum").Close.rolling(50).mean().reindex(ci).ffill()),
                       rsi=[None if pd.isna(v) else round(float(v), 1) for v in rs_.reindex(g.index).set_axis(g.Datum).reindex(ci).ffill()], ev=evk)
        z["sf"] = sf_data(t, SFT, SFS, dsf)
        out.append(z)
    out.sort(key=lambda z: -z["samlad"])
    # katalysatorkalender: alla kommande händelser (datumsatta och planerade) inom ett år
    kal = []
    for z in out:
        for e in z["besked"]:
            if e["dg"] > 365: continue
            dt = pd.Timestamp(e["d"])
            fp, ft = katalysatorfonster(e["dg"] if e["typ"] != "Planerat" else None, e["dg"] if e["typ"] == "Planerat" else None)
            kal.append(dict(t=z["t"], namn=z["namn"], d=e["d"], dg=e["dg"], h=e["h"], typ="Planerat" if e["typ"] == "Planerat" else "Datum", fas=e.get("fas", ""), p=e.get("p"),
                            per=f"Q{(dt.month - 1) // 3 + 1} {dt.year}" if e["typ"] == "Planerat" else "", fonster=ft, vad=z["vad"], nar=z["nar"], sweet=z["sweet"] and e["typ"] != "Planerat"))
    kal.sort(key=lambda e: (e["dg"], e["t"]))
    data = dict(aktier=out, kalender=kal, sektor=bx.sektor_rubrik(IDX), bransch=bx.las_bransch(IDAG), sf_saknas=[z["t"] for z in out if not z["sf"]], genererad=str(IDAG.date()), datum=[str(d.date()) for d in ci])
    html = (HER / "bio_mall.html").read_text(encoding="utf-8").replace("/*DATA*/", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    UT.parent.mkdir(parents=True, exist_ok=True); UT.write_text(html, encoding="utf-8")
    print(f"{len(out)} bolag, senaste kurs {data['datum'][-1]} -> {UT} ({UT.stat().st_size / 1e3:.0f} kB)")

if __name__ == "__main__":
    main()
