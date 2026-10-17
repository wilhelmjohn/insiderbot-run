"""Kvalitativa sammanfattningar för Biotechscreen: underlag, behovsbedömning och sparning.

  python bio_sf.py behov [--max N]     vilka bolag som behöver ny text och varför (kursrörelse, nya pressmeddelanden, insyn, ålder)
  python bio_sf.py dossier [--kompakt] T [T ...]   underlag per bolag: kurs mot sektorn, största dagsrörelser, pressmeddelanden, rapportutdrag,
                                       insyn, kassa, pipeline och katalysatorer. "alla" ger samtliga bolag.
  python bio_sf.py spara T             läser JSON på stdin (kort, tolk_kort, bild, belagt, tolkning, bevaka, kallor) och sparar i bio_sammanfattning.json
  python bio_sf.py kontroll            validerar att alla bolag har en text och att fälten följer reglerna

Texterna ligger i bio_sammanfattning.json (det som visas på sidan). Vad som redan beaktats (pressmeddelanden, insynsrader, kurs vid skrivtillfället)
ligger i bio_sf_state.json och används bara av 'behov'. Skriv aldrig för hand i state-filen: 'spara' uppdaterar den."""
import json, re, sys
from pathlib import Path
import numpy as np, pandas as pd

HER = Path(__file__).resolve().parent
TEXT = HER / "bio_sammanfattning.json"
STATE = HER / "bio_sf_state.json"
IDAG = pd.Timestamp.now(tz="Europe/Stockholm").tz_localize(None).normalize()
STARK = {"FDA", "Studieresultat", "Emission", "Avtal", "Godkännande", "Varning", "Insiderinfo", "Notering"}
RAPPORT = re.compile(r"delårs|bokslut|interim|year-end|quarter|kvartal|halvårs", re.I)
GRANS = dict(kort=(60, 300), tolk_kort=(60, 300), bild=(120, 900), tolkning=(40, 1000), belagt_n=(2, 7), belagt_len=(15, 330), bevaka_n=(1, 3), bevaka_len=(15, 200))
FORBJUDET = re.compile(r"\b(köp nu|sälj nu|bör köpa|bör sälja|rekommenderar (?:att )?(?:köpa|sälja)|köpläge|säkert köp|kan inte förlora)\b", re.I)

def las():
    sys.path.insert(0, str(HER))
    import trapp_bygg as tb
    tm, bl, h, ik = tb.las_stod()
    d = dict(tm=tm, bl=bl, h=h, ik=ik)
    d["o"] = pd.read_csv(HER / "bio_ohlc.csv", parse_dates=["Datum"]).sort_values(["Ticker", "Datum"])
    d["inf"] = pd.read_csv(HER / "bio_info.csv").set_index("Ticker")
    d["pm"] = pd.read_csv(HER / "bio_pm.csv", parse_dates=["Datum"]).fillna("")
    d["pip"] = pd.read_csv(HER / "bio_pipeline.csv").fillna("")
    d["kat"] = pd.read_csv(HER / "bio_katalysatorer.csv").fillna("")
    d["bo"] = pd.read_csv(HER / "bio_bolag.csv").fillna("").set_index("Ticker")
    d["tick"] = [t for t in (HER / "bio_tickers.txt").read_text().split() if t]
    d["p"] = d["o"].pivot_table(index="Datum", columns="Ticker", values="Close")
    import bio_extra as bx
    d["idx"], d["st"] = bx.las_index(), bx.las_studier()
    return d

def jlas(p):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

def jskriv(p, x):
    p.write_text(json.dumps(x, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")

def f(x, d=1):
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:,.{d}f}".replace(",", " ").replace(".", ",")

def pmstark(r):
    return bool(STARK & set(x for x in str(r.Flagga).split(",") if x)) or bool(RAPPORT.search(r.Rubrik))

def insynsrader(d, t, sedan):
    isin = d["tm"].ISIN.get(t)
    if isin != isin: return d["h"].iloc[0:0]
    x = d["h"][(d["h"].ISIN == isin) & (d["h"].Publiceringsdatum >= sedan)].rename(columns={"Person i ledande ställning": "Person"}).copy()
    x["id"] = x.Publiceringsdatum.dt.strftime("%Y-%m-%d") + "|" + x.Person.astype(str) + "|" + x.Volym.astype(str) + "|" + x.Karaktär
    return x.sort_values("Publiceringsdatum", ascending=False)

def dossier(d, t, kompakt=False):
    p = d["p"]; o = d["o"][d["o"].Ticker == t].drop_duplicates("Datum").set_index("Datum")
    c = o.Close.dropna(); i = d["inf"].loc[t]; L = []
    L.append(f"=== {t} · {re.sub(r'[(]publ[.]?[)]', '', str(i.longName)).strip()} · {i.industry} ===")
    last = c.iloc[-1]; hi = o.High.tail(252).max(); lo = o.Low.tail(252).min()
    dhi = o.High.tail(252).idxmax().date(); dlo = o.Low.tail(252).idxmin().date()
    L.append(f"KURS {f(last,2)} kr ({c.index[-1].date()}); 52v-topp {f(hi,2)} ({dhi}), {f((last/hi-1)*100,0)} %; 52v-botten {f(lo,2)} ({dlo}), {f((last/lo-1)*100,0)} % över; börsvärde {f((i.marketCap or 0)/1e9,2)} mdr SEK")
    def avk(n):
        if len(c) <= n: return None
        a = (c.iloc[-1] / c.iloc[-1 - n] - 1) * 100
        sek = (p.iloc[-1] / p.iloc[-1 - n] - 1) * 100
        return a, float(sek.median())
    rows = []
    for n, nm in ((21, "1 mån"), (63, "3 mån"), (126, "6 mån"), (250, "12 mån")):
        r = avk(n)
        if r: rows.append(f"{nm} {f(r[0],0)} % (sektormedian {f(r[1],0)} %)")
    L.append("AVKASTNING " + "; ".join(rows))
    if d.get("idx"):
        import bio_extra as bx
        rl = bx.rel_index(o.reset_index()[["Datum", "Close"]], d["idx"])
        for k, v in rl.items():
            L.append(f"MOT {k.replace('^', '')}: 20 d {f(v['r20'],0)} % (bolaget {f(v['o20'],0)} pp {'över' if (v['o20'] or 0) >= 0 else 'under'}), 60 d {f(v['r60'],0)} % ({f(v['o60'],0)} pp), 250 d {f(v['r250'],0)} % ({f(v['o250'],0)} pp); "
                     f"indexet förklarar {f(v['r2'],0)} % av dagsrörelserna (beta {f(v['beta'],2)}, fördröjning {v['lag']} dag)")
    me = c.groupby([c.index.year, c.index.month]).last().tail(13)
    L.append("MÅNADSSLUT " + " ".join(f"{y}-{m:02d}:{f(v,2)}" for (y, m), v in me.items()))
    ret = c.pct_change() * 100; vol = o.Volume.reindex(c.index); vmed = vol.rolling(40).median().shift(1)
    sektor_d = (p.pct_change() * 100).median(axis=1)
    big = ret.tail(260)[ret.tail(260).abs() >= 7].sort_values(key=lambda s: -s.abs()).head(10).sort_index()
    pm = d["pm"][d["pm"].Ticker == t]
    if len(big):
        L.append(f"STÖRSTA DAGSRÖRELSER (≥7 %, 260 d; pressmeddelanden finns bara från {d['pm'].Datum.min().date()}):")
        for dt, v in big.items():
            nb = pm[(pm.Datum >= dt - pd.Timedelta(days=1)) & (pm.Datum <= dt)]
            ttl = " | ".join(nb.Rubrik.str[:90].tolist()[:3]) if len(nb) else ("PM-UNDERLAG SAKNAS (före " + str(d['pm'].Datum.min().date()) + "): sök orsaken på webben (Börskollens bolagslista, MFN, Placera) innan du skriver 'okänd'" if dt < d['pm'].Datum.min() else "inget pressmeddelande samma/föregående dag: kolla webben innan du skriver 'okänd'")
            vm = vol.loc[dt] / vmed.loc[dt] if vmed.loc[dt] and vmed.loc[dt] > 0 else float("nan")
            ix = ""
            if d.get("idx"):
                import bio_extra as bx
                ix = "; " + ", ".join(f"{k.replace('^', '')} {f(a,1)} % (dagen före {f(b,1)} %)" for k, (a, b) in bx.index_dag(d["idx"], dt).items())
            L.append(f"  {dt.date()} {f(v,1)} % (peer-median bland de svenska bolagen {f(sektor_d.get(dt, np.nan),1)} %{ix}) volym {f(vm,1)}× normalt · {ttl}")
    # kursrörelser per 20-dagarsperiod nyligen
    L.append(f"VOLYM senaste 20 d mot 6 mån: {f(o.Volume.tail(20).mean()/max(o.Volume.tail(126).mean(),1),2)}×; omsättning {f((c*o.Volume).tail(60).median()/1e6,1)} MSEK/dag")
    # finans
    cash, ocf, rev = i.totalCash, i.operatingCashflow, i.totalRevenue
    burn = -ocf if pd.notna(ocf) and ocf < 0 else None
    L.append(f"FINANS (Yahoo, kan släpa): kassa {f((cash or np.nan)/1e6,0)} MSEK; operativt kassaflöde 12 mån {f((ocf if pd.notna(ocf) else np.nan)/1e6,0)} MSEK; runway "
             + (f"{cash/burn*12:.0f} mån" if burn and pd.notna(cash) else "självfinansierad/okänd") + f"; skulder {f((i.totalDebt or np.nan)/1e6,0)} MSEK; omsättning {f((rev if pd.notna(rev) else np.nan)/1e6,0)} MSEK; "
             f"nettoresultat {f((i.netIncomeToCommon if pd.notna(i.netIncomeToCommon) else np.nan)/1e6,0)} MSEK; fler aktier 1 år {f(i.Utspadning_1ar,0)} %, 3 år {f(i.Utspadning_3ar,0)} %; analytiker {f(i.numberOfAnalystOpinions,0)} st, snittmål {f(i.targetMeanPrice,2)}")
    if t in d["bo"].index:
        b = d["bo"].loc[t]
        L.append(f"BOLAGSDATA (handinlagt {b.Uppdaterad}): {b.Huvudindikation} | Orphan: {b.Orphan} {b.Orphan_detalj} | Behov: {b.Behov} | TO extern: {b.TO_ext}; konv/lån: {b.Konv_lan}; {b.TO_konv} | Ägare: {b.Specialistagare}")
    for r in d["pip"][d["pip"].Ticker == t].itertuples():
        L.append(f"PIPELINE {r.Projekt} · {r.Indikation} · fas {r.Fas or '–'} · nästa: {r.Nasta_milstolpe} · senast {r.Milstolpe_senast or '–'} (källa {str(r.Kalla)[:60]}, {r.Uppdaterad})")
    for r in d["kat"][d["kat"].Ticker == t].itertuples():
        L.append(f"KATALYSATOR {r.Datum} {r.Handelse} ({r.Typ}, {r.Sakerhet})")
    if d.get("st") is not None:
        import bio_extra as bx
        rader, _, sW, sU, sandr = bx.studie_info(t, d["st"], bx.las_studielogg(), IDAG, {"fas2": ("Fas 2", 29), "fas3": ("Fas 3", 58)})
        for r in rader[:8]:
            L.append(f"STUDIEREGISTER {r['nct']} {r['fas']} {r['ssv']} · primärt slutdatum {r['ps'] or '–'} ({'faktiskt' if r['pst'] == 'ACTUAL' else 'beräknat'}) · uppdaterad {r['u']}{' · resultat upplagt' if r['res'] else ''} · {r['t'][:90]}")
        for w in sW: L.append(f"STUDIEREGISTER-VARNING {w[0]}: {w[1]}")
        for a in sandr: L.append(f"STUDIEÄNDRING {a['d']} {a['nct']} {a['typ']}: {a['fran']} -> {a['till']}")
    fi_ = bx.flagg_info(t, bx.las_flaggning(), IDAG)
    if fi_: L.append(f"FLAGGNING (FI Börsinformation): senaste meddelande publicerat {fi_['d']} ({fi_['dg']} d sedan), {fi_['n90']} de senaste 90 dagarna. Innehållet (vem, hur mycket, upp eller ned) framgår inte av tabellen{'; länk ' + fi_['lank'] if fi_['lank'] else ''}.")
    # insyn
    ins = insynsrader(d, t, IDAG - pd.Timedelta(days=200))
    if len(ins):
        L.append("INSYN (200 d, publicerat):")
        for r in ins.head(14).itertuples():
            L.append(f"  {r.Publiceringsdatum.date()} {r.Karaktär} {r.Person} ({r.Befattning}){' närstående' if r.Närstående == 'Ja' else ''}: {f(r.Volym,0)} st à {f(r.Pris,2)} = {f(r.belopp/1e3,0)} kSEK")
    else:
        L.append("INSYN (200 d): inga rapporterade affärer")
    bl = d["bl"]; nm = d["tm"].Bolag.get(t, i.longName)
    import trapp_bygg as tb
    bb = bl[(bl.k == tb.norm(nm)) | (bl.k == tb.norm(i.longName))]
    if len(bb): L.append(f"BLANKNING {f(bb.Niva.iloc[0],2)} % ({bb.Datum.iloc[0]}), förändring 20 d {f(bb.Forandring_20d.iloc[0],2)} pp")
    # pressmeddelanden
    pm = pm.sort_values("Datum", ascending=False)
    L.append(f"PRESSMEDDELANDEN ({len(pm)} st sedan {pm.Datum.min().date() if len(pm) else '–'}; flagga i hakparentes):")
    seen = set()
    for r in pm.itertuples():
        k = (r.Datum, r.Flagga) if kompakt else (r.Datum, r.Rubrik[:40].lower())
        if k in seen: continue
        seen.add(k)
        L.append(f"  {r.Datum.date()}{' ['+r.Flagga+']' if r.Flagga else ''} {r.Rubrik[:140 if not kompakt else 110]}")
        if len(seen) >= (30 if not kompakt else 16): break
    rap = pm[pm.Rubrik.map(lambda s: bool(RAPPORT.search(s))) & (pm.Text.str.len() > 200)].head(2 if not kompakt else 1)
    for r in rap.itertuples():
        L.append(f"RAPPORTUTDRAG {r.Datum.date()} {r.Rubrik[:80]}: {r.Text[:900 if not kompakt else 700]}")
    stor = pm[(pm.Flagga != "") & (pm.Text.str.len() > 200) & ~pm.Rubrik.map(lambda s: bool(RAPPORT.search(s)))].drop_duplicates("Datum").head(4 if not kompakt else 2)
    for r in stor.itertuples():
        L.append(f"PM-UTDRAG {r.Datum.date()} [{r.Flagga}] {r.Rubrik[:80]}: {r.Text[:500 if not kompakt else 380]}")
    return "\n".join(L)

def nytt_sedan(d, t, s, alder=0):
    """Vad som hänt sedan texten skrevs (s = bolagets post i bio_sf_state.json): nya pressmeddelanden, kursförändring och nya insynsaffärer.
    Används av 'behov' och av bio_bygg.py, så att sidan och nattuppgiften bedömer samma sak med samma tröskelvärden."""
    sedan = pd.Timestamp(s["datum"]); c = d["p"][t].dropna(); skal = []; poang = 0
    pm = d["pm"][d["pm"].Ticker == t]
    ny = pm[~pm.Lank.isin(s.get("pm", [])) & (pm.Datum >= sedan - pd.Timedelta(days=3))]
    nys = ny[ny.apply(pmstark, axis=1)] if len(ny) else ny
    if len(nys):
        skal.append(f"{len(nys)} nya viktiga pressmeddelanden: " + " | ".join(f"{r.Datum.date()} {r.Rubrik[:70]}" for r in nys.drop_duplicates('Datum').head(3).itertuples())); poang += 10 + len(nys)
    tot = dagx = dagd = None
    if len(c[c.index > sedan]):
        tot = float((c.iloc[-1] / s["kurs"] - 1) * 100)
        dag = (c.pct_change() * 100)[c.index > sedan]
        if len(dag): dagd = dag.abs().idxmax(); dagx = float(dag.loc[dagd])
        if abs(tot) >= 15: skal.append(f"kurs {f(tot,0)} % sedan {s['datum']}"); poang += 8 + abs(tot) / 10
        if dagx is not None and abs(dagx) >= 10: skal.append(f"dagsrörelse {f(dagx,0)} % {dagd.date()}"); poang += 6
    ins = insynsrader(d, t, sedan - pd.Timedelta(days=3))
    ins = ins[~ins.id.isin(s.get("insyn", []))]
    kop = float(ins[ins.Karaktär == "Förvärv"].belopp.sum()) if len(ins) else 0.0
    sal = float(ins[ins.Karaktär == "Avyttring"].belopp.sum()) if len(ins) else 0.0
    if kop >= 250_000 or sal >= 500_000: skal.append(f"nya insynsaffärer: köp {f(kop/1e3,0)} kSEK, sälj {f(sal/1e3,0)} kSEK"); poang += 5
    if alder >= 35: skal.append(f"texten är {alder} dagar gammal"); poang += 3 + alder / 30
    return dict(sedan=sedan, ny=ny, nys=nys, tot=tot, dagx=dagx, dagd=dagd, ins=ins, kop=kop, sal=sal, skal=skal, poang=poang, kurs_nu=float(c.iloc[-1]), kurs_dag=c.index[-1])

def behov(d, maxn=99):
    tx, st = jlas(TEXT), jlas(STATE); ut = []
    for t in d["tick"]:
        if t not in d["inf"].index: continue
        if t not in tx or t not in st:
            ut.append((99, t, ["text saknas"])); continue
        if not tx[t].get("tolk_kort") or not tx[t].get("bevaka"):
            ut.append((98, t, ["saknar tolk_kort/bevaka"])); continue
        n = nytt_sedan(d, t, st[t], (IDAG - pd.Timestamp(tx[t]["uppdaterad"])).days)
        nyo = len(n["ny"]) - len(n["nys"])
        if n["skal"]: ut.append((n["poang"], t, n["skal"] + ([f"+{nyo} rutinmeddelanden"] if nyo else [])))
    ut.sort(key=lambda x: -x[0])
    return ut[:maxn]

def kontrollera(x, ticker=None):
    fel = []
    for k in ("kort", "tolk_kort", "bild", "tolkning"):
        v = x.get(k)
        if not isinstance(v, str) or not v.strip(): fel.append(f"{k} saknas"); continue
        a, b = GRANS[k]
        if not a <= len(v) <= b: fel.append(f"{k} har {len(v)} tecken (ska vara {a}-{b})")
        if "\n" in v: fel.append(f"{k} får inte innehålla radbrytningar")
        if FORBJUDET.search(v): fel.append(f"{k} innehåller råd om köp/sälj: skriv beskrivande")
    b = x.get("belagt")
    if not isinstance(b, list) or not GRANS["belagt_n"][0] <= len(b) <= GRANS["belagt_n"][1]: fel.append(f"belagt ska vara en lista med {GRANS['belagt_n'][0]}-{GRANS['belagt_n'][1]} punkter")
    else:
        for s in b:
            if not isinstance(s, str) or not GRANS["belagt_len"][0] <= len(s) <= GRANS["belagt_len"][1]: fel.append(f"belagt-punkt har fel längd: {str(s)[:40]}")
            elif FORBJUDET.search(s): fel.append("belagt innehåller råd om köp/sälj")
    bv = x.get("bevaka")
    if not isinstance(bv, list) or not GRANS["bevaka_n"][0] <= len(bv) <= GRANS["bevaka_n"][1]: fel.append(f"bevaka ska vara en lista med {GRANS['bevaka_n'][0]}-{GRANS['bevaka_n'][1]} punkter (vad som är värt att följa)")
    else:
        for s in bv:
            if not isinstance(s, str) or not GRANS["bevaka_len"][0] <= len(s) <= GRANS["bevaka_len"][1]: fel.append(f"bevaka-punkt har fel längd: {str(s)[:40]}")
            elif FORBJUDET.search(s): fel.append("bevaka innehåller råd om köp/sälj")
    kl = x.get("kallor")
    if not isinstance(kl, list) or not 1 <= len(kl) <= 8: fel.append("kallor ska vara 1-8 par [titel, url]")
    else:
        for k in kl:
            if not (isinstance(k, list) and len(k) == 2 and isinstance(k[1], str) and k[1].startswith("https://") and k[0].strip()): fel.append(f"ogiltig källa: {k}")
    return fel

def spara(d, t):
    x = json.loads(sys.stdin.read())
    if t not in d["tick"]: sys.exit(f"{t} finns inte i bio_tickers.txt")
    fel = kontrollera(x)
    if fel: sys.exit("Sparades inte:\n- " + "\n- ".join(fel))
    c = d["p"][t].dropna(); tx, st = jlas(TEXT), jlas(STATE)
    tx[t] = dict(kort=x["kort"].strip(), tolk_kort=x["tolk_kort"].strip(), bild=x["bild"].strip(), belagt=[s.strip() for s in x["belagt"]], tolkning=x["tolkning"].strip(),
                 bevaka=[s.strip() for s in x["bevaka"]], kallor=x["kallor"], uppdaterad=str(IDAG.date()))
    pm = d["pm"][d["pm"].Ticker == t]
    ins = insynsrader(d, t, IDAG - pd.Timedelta(days=200))
    st[t] = dict(datum=str(c.index[-1].date()), kurs=round(float(c.iloc[-1]), 3), pm=sorted(set(pm.Lank.tolist())), insyn=sorted(set(ins.id.tolist())))
    jskriv(TEXT, tx); jskriv(STATE, st)
    print(f"{t}: sparad ({len(tx[t]['kort'])}+{len(tx[t]['bild'])} tecken), kurs {st[t]['kurs']} {st[t]['datum']}")

def main():
    a = sys.argv[1:]
    if not a: sys.exit(__doc__)
    cmd, rest = a[0], a[1:]
    if cmd == "kontroll":
        tx = jlas(TEXT); tick = [t for t in (HER / "bio_tickers.txt").read_text().split() if t]; bad = 0
        for t in tick:
            if t not in tx: print(f"{t}: text saknas"); bad += 1; continue
            for e in kontrollera(tx[t]): print(f"{t}: {e}"); bad += 1
        print(f"{len(tick) - len([t for t in tick if t not in tx])} av {len(tick)} bolag har text, {bad} fel")
        sys.exit(1 if bad else 0)
    d = las()
    if cmd == "behov":
        n = int(rest[rest.index("--max") + 1]) if "--max" in rest else 99
        r = behov(d, n)
        for _, t, s in r: print(f"{t}: " + "; ".join(s))
        print(f"{len(r)} bolag behöver ny text" if r else "Inget bolag behöver ny text")
    elif cmd == "dossier":
        komp = "--kompakt" in rest; rest = [x for x in rest if x != "--kompakt"]
        ts = d["tick"] if rest == ["alla"] else rest
        for t in ts: print(dossier(d, t, komp)); print()
    elif cmd == "spara":
        spara(d, rest[0])
    else:
        sys.exit(__doc__)

if __name__ == "__main__":
    main()
