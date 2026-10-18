"""Kvalitativa texter för bevakningslistan: underlag, behovsbedömning och sparning.

  python bev_sf.py behov [--max N]          vilka bolag som behöver ny text och varför
  python bev_sf.py dossier [--kompakt] T..  underlag per bolag (T = ticker utan .ST, t.ex. PLEJD). "alla" ger samtliga fall i bevakning.csv
  python bev_sf.py spara T                  läser JSON på stdin (kort, bild, belagt, tolkning, bevaka, handelser, kallor) och sparar i bev_sammanfattning.json
  python bev_sf.py rk [--max N]       listar fall vars analytikerriktkurs saknas eller är äldre än 21 dagar (först), därefter övriga med uppsida
  python bev_sf.py spara-rk T         läser JSON på stdin {rk: snittriktkurs i kr eller null, antal, kalla: https-länk, not: kort anmärkning, gammal: true om alla riktkurser är äldre än fyra månader} och sparar i bev_riktkurs.json
  python bev_sf.py spara-djup T             läser JSON på stdin (fraga, varfor, slutsats, delar{affaren,kassaflode,varderng,katalysatorer,insyn,risk: {p 0-5, t}}, handelser[{d,t}], jamforelse, timing, konsensus, scenarier[{n,f,lag,hog}], kallor) och sparar i bev_djup.json
  python bev_sf.py kontroll                 validerar att alla fall har text och att fälten följer reglerna

Texterna ligger i bev_sammanfattning.json (visas i den utfällda raden på sidan). Vad som redan beaktats (kurs, MFN-länkar, insynsrader,
vändningssignal) ligger i bev_sf_state.json och används bara av 'behov'. Skriv aldrig för hand i state-filen: 'spara' uppdaterar den.
Körs med datamappen som arbetsmapp (koden kopieras in dit, se uppgiftens instruktion)."""
import json, re, sys
from pathlib import Path
import numpy as np, pandas as pd

HER = Path(__file__).resolve().parent
TEXT = HER / "bev_sammanfattning.json"
STATE = HER / "bev_sf_state.json"
DJUP = HER / "bev_djup.json"
RK = HER / "bev_riktkurs.json"
DELAR = [("affaren", "Affären"), ("kassaflode", "Kassaflöde och balansräkning"), ("varderng", "Värdering"), ("katalysatorer", "Katalysatorer"), ("insyn", "Insyn och ägare"), ("risk", "Risk")]
IDAG = pd.Timestamp.now(tz="Europe/Stockholm").tz_localize(None).normalize()
GRANS = dict(handelser_n=(3, 10), handelse_len=(8, 150), kort=(60, 300), bild=(120, 900), tolkning=(40, 1000), belagt_n=(2, 7), belagt_len=(15, 330), bevaka_n=(1, 3), bevaka_len=(15, 200), villkor_n=(2, 4), villkor_len=(15, 260))
FORBJUDET = re.compile(r"\b(köp nu|sälj nu|bör köpa|bör sälja|rekommenderar (?:att )?(?:köpa|sälja)|köpläge|säkert köp|kan inte förlora)\b", re.I)
STARK_TYP = {"rapport", "vinstvarning", "prognoshojning", "nyemission", "m_and_a", "ledning"}


def jlas(p):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def jskriv(p, x):
    p.write_text(json.dumps(x, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def f(x, d=1):
    if isinstance(x, str):
        try: x = float(x)
        except ValueError: return x or "–"
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:,.{d}f}".replace(",", " ").replace(".", ",")


def las():
    kr = pd.read_csv(HER / "kurser.csv", usecols=["Datum", "Ticker", "Stang", "Volym"], parse_dates=["Datum"])
    p = kr.pivot_table(index="Datum", columns="Ticker", values="Stang", aggfunc="last").sort_index()
    v = kr.pivot_table(index="Datum", columns="Ticker", values="Volym", aggfunc="last").sort_index()
    b = pd.read_csv(HER / "bevakning.csv")
    d = dict(p=p, v=v, b=b.set_index("Ticker"), tick=[t for t in b.Ticker if t in p.columns or True])
    d["idx"] = pd.read_csv(HER / "marknad.csv", parse_dates=["Datum"]).set_index("Datum").Index.dropna()
    d["si"] = pd.read_csv(HER / "sektor_info.csv").set_index("Ticker")
    d["tm"] = pd.read_csv(HER / "ticker_map.csv").drop_duplicates("Ticker").set_index("Ticker")
    d["mfn"] = pd.read_csv(HER / "nyheter_mfn.csv", parse_dates=["Publicerat"]).fillna("")
    d["rap"] = pd.read_csv(HER / "rapporter.csv", parse_dates=["Publicerat"]).fillna("")
    d["ik"] = pd.read_csv(HER / "insiderkop.csv", parse_dates=["Publiceringsdatum"])
    return d


def kort_t(t):
    return t.replace(".ST", "")


def namn_till_tick(arg):
    return arg if "." in arg else arg + ".ST"


def avk(c, n):
    return None if len(c) <= n else float((c.iloc[-1] / c.iloc[-1 - n] - 1) * 100)


def peers(d, t):
    """Peers: samma bransch (Yahoo industry) bland svenska bolag över 1 mdr SEK, annars samma sektor. Returnerar (namn, lista)."""
    si = d["si"]
    if t not in si.index: return "", []
    ok = [x for x in si.index if x.endswith(".ST") and x != t and x in d["p"].columns and (si.marketCap.get(x) or 0) > 1e9]
    for kol in ("industry", "sector"):
        lst = [x for x in ok if si.loc[x, kol] == si.loc[t, kol]]
        if len(lst) >= 4: return f"{si.loc[t, kol]} ({len(lst)} svenska bolag >1 mdr)", lst
    return "", []


def graf(p, si, t, n=250):
    """Serier till grafen: bolagets kurs och branschens (medianen av dagsavkastningen bland peers, daglig ombalansering), båda satta till 100 första dagen."""
    c = p[t].dropna().tail(n)
    if len(c) < 60: return None
    nm, pl = peers({"si": si, "p": p}, t)
    k = (c / c.iloc[0] * 100).round(1)
    if pl:
        r = p[pl].pct_change().median(axis=1).reindex(c.index).fillna(0.0); r.iloc[0] = 0.0
        b = ((1 + r).cumprod() * 100).round(1)
    else:
        b = k * 0 + 100
    return dict(d=[i.strftime("%Y-%m-%d") for i in c.index], k=[float(x) for x in k], b=[float(x) for x in b], bn=nm or "ingen branschgrupp")


# Utfall för formationsutbrott i den egna kursdatan (alla .ST-bolag, backtest): andel där kursen gick åt signalens håll efter 20 dagar,
# snittavkastning mot börsen (procentenheter) efter 40 dagar, andel där målet nåddes inom 40 dagar.
FSTAT = {("huvud-skuldra", "sälj"): (52, -1.0, 33), ("dubbeltopp", "sälj"): (47, 0.3, 39), ("dubbelbotten", "köp"): (47, 0.1, 52),
         ("omvänd huvud-skuldra", "köp"): (44, 1.0, 43), ("rektangel", "köp"): (48, 0.4, 50), ("rektangel", "sälj"): (53, -1.7, 44)}


def teknik(p, oh, t, n=250):
    """Formationer (utbrott genom nacklinje) inom grafens fönster, med nivå, mål och historiskt utfall. Beskrivning, ingen prognos."""
    import formationer
    c = p[t].dropna()
    if len(c) < 80: return []
    try: fm = formationer.hitta(c, max_dagar=n, ohlc=oh)
    except Exception: return []
    c0 = float(c.tail(n).iloc[0]); ut = []
    for h in fm:
        a = FSTAT.get((h["namn"], h["typ"]))
        ut.append(dict(namn=h["namn"], typ=h["typ"], dag=h["dag"].strftime("%Y-%m-%d"), niva=round(float(h["niva"]), 1), mal=round(float(h["mal"]), 1),
                       ni=round(h["niva"] / c0 * 100, 1), mi=round(h["mal"] / c0 * 100, 1), kurs=round(float(c.iloc[-1]), 1),
                       nadd=bool((h["typ"] == "sälj" and c.loc[h["dag"]:].min() <= h["mal"]) or (h["typ"] == "köp" and c.loc[h["dag"]:].max() >= h["mal"])),
                       stat=a))
    return ut


BLOCK_PCT = 2.0   # ett enskilt köp över så här stor andel av börsvärdet är sannolikt blockaffär, nyemission eller ägarbyte


def blockflagga(d, t, dagar=120):
    """Största enskilda förvärv senaste 'dagar' som andel av börsvärdet. Flaggar misstänkt blockaffär/nyemission (kontrolleras alltid för hand)."""
    ik = insyn(d, t, IDAG - pd.Timedelta(days=dagar)); ik = ik[ik.Karaktär.astype(str).str.contains("Förvärv", case=False)] if len(ik) else ik
    bm = d["b"].Borsvarde_MSEK.get(t) if t in d["b"].index else np.nan
    if not len(ik) or pd.isna(bm) or bm <= 0: return None
    r = ik.sort_values("Belopp", ascending=False).iloc[0]; pct = r.Belopp / 1e6 / bm * 100
    rest = ik.Belopp.sum() - r.Belopp
    return dict(pct=round(float(pct), 1), mkr=round(float(r.Belopp) / 1e6, 1), datum=str(r.Publiceringsdatum.date()), rest_mkr=round(float(rest) / 1e6, 2), flagga=bool(pct >= BLOCK_PCT))


def insyn(d, t, sedan):
    isin = d["tm"].ISIN.get(t)
    if isin != isin or isin is None: return d["ik"].iloc[0:0]
    x = d["ik"][(d["ik"].ISIN == isin) & (d["ik"].Publiceringsdatum >= sedan)].copy()
    x["id"] = x.Publiceringsdatum.dt.strftime("%Y-%m-%d") + "|" + x["Person i ledande ställning"].astype(str) + "|" + x.Volym.astype(str) + "|" + x.Karaktär
    return x.sort_values("Publiceringsdatum", ascending=False)


def dossier(d, t, kompakt=False):
    b = d["b"].loc[t]; p = d["p"]; L = []
    c = p[t].dropna() if t in p.columns else pd.Series(dtype=float)
    si = d["si"].loc[t] if t in d["si"].index else None
    L.append(f"=== {kort_t(t)} · {b.Bolag} · {si.industry if si is not None else '?'} · {si.sector if si is not None else '?'} · börsvärde {f(b.Borsvarde_MSEK,0)} MSEK ===")
    L.append(f"FALL: {b.Tillstand}, kategori {b.Kategori}, poäng {f(b.Poang,0)}; insiderköp {int(b.Antal_personer)} pers, {f(b.Belopp/1e3,0)} kSEK, första publ {b.Forsta_publ} (affär {b.Affar_datum}); "
             f"{b.Personer if isinstance(b.Personer, str) else ''}{'; MED I LISTAN GENOM VÄNDNINGSSIGNAL' if isinstance(b.Lagts_till, str) else ''}")
    if len(c) < 30:
        L.append("KURS: för lite kursdata i kurser.csv (utländsk notering?): kolla webben");
    else:
        hi = c.tail(252).max(); lo = c.tail(252).min()
        L.append(f"KURS {f(c.iloc[-1],2)} ({c.index[-1].date()}); 52v-topp {f(hi,2)} ({c.tail(252).idxmax().date()}) → {f((c.iloc[-1]/hi-1)*100,0)} %; 52v-botten {f(lo,2)} ({c.tail(252).idxmin().date()}) → +{f((c.iloc[-1]/lo-1)*100,0)} %; köpkurs insyn {f(b.Kopkurs,2)} ({f(b['Utv_sedan_kop_%'],1)} % sedan)")
        pn, pl = peers(d, t)
        big = [x for x in d["si"].index if x.endswith(".ST") and x in p.columns and x != t and (d["si"].marketCap.get(x) or 0) > 2e9]
        idx = d["idx"]; rows = []
        for n, nm in ((1, "1 d"), (5, "5 d"), (21, "1 mån"), (63, "3 mån"), (126, "6 mån"), (250, "12 mån")):
            a = avk(c, n)
            if a is None: continue
            dat0, dat1 = c.index[-1 - n], c.index[-1]
            def med(cols):
                x = (p[cols].loc[dat1] / p[cols].loc[dat0] - 1).dropna() * 100
                return float(x.median()) if len(x) else None
            ix = (float(idx.asof(dat1) / idx.asof(dat0) - 1) * 100) if len(idx) else None
            rows.append(f"{nm}: bolaget {f(a,1)} % | börsindex {f(ix,1)} % | bransch {f(med(pl),1) if pl else '–'} % | bred ({len(big)} bolag >2 mdr) {f(med(big),1)} %")
        L.append("AVKASTNING (" + (pn or "ingen branschgrupp") + "):"); L += ["  " + r for r in rows]
        ret = c.pct_change() * 100; vol = d["v"][t].reindex(c.index) if t in d["v"].columns else pd.Series(index=c.index, dtype=float); vm = vol.rolling(20).median().shift(1)
        bd = (p[pl].pct_change() * 100).median(axis=1) if pl else pd.Series(dtype=float)
        idr = idx.pct_change() * 100
        mfn = d["mfn"][d["mfn"].Ticker == t].sort_values("Publicerat", ascending=False)
        stor = ret.tail(120)[ret.tail(120).abs() >= 5].sort_values(key=lambda s: -s.abs()).head(8).sort_index()
        if len(stor):
            L.append("STÖRSTA DAGSRÖRELSER (≥5 %, 120 d):")
            for dt, v_ in stor.items():
                nb = mfn[(mfn.Publicerat.dt.normalize() >= dt - pd.Timedelta(days=1)) & (mfn.Publicerat.dt.normalize() <= dt)]
                ttl = " | ".join(nb.Rubrik.str[:90].tolist()[:2]) if len(nb) else "inga MFN-nyheter samma/föregående dag i underlaget: sök på webben"
                q = vol.loc[dt] / vm.loc[dt] if vm.loc[dt] and vm.loc[dt] > 0 else float("nan")
                L.append(f"  {dt.date()} {f(v_,1)} % (bransch {f(bd.get(dt, np.nan),1)} %, börsindex {f(idr.get(dt, np.nan),1)} %) volym {f(q,1)}× · {ttl}")
        sen = ret.tail(7)
        L.append("SENASTE DAGAR: " + " ".join(f"{i.strftime('%m-%d')} {f(v_,1)}%" for i, v_ in sen.items()) + f"; volym senaste dagen {f(vol.iloc[-1]/vm.iloc[-1] if vm.iloc[-1] else np.nan,1)}× normalt")
    L.append(f"TEKNIK: RSI {f(b.RSI14,0)}, över MA200: {b.Over_MA200}; stop {f(b.Stop,2)} ({f(b['Stop_%'],0)} %), brytkurs {f(b.Brytkurs,2)} ({f(b['Brytkurs_%'],0)} %) {b.Teknik if isinstance(b.Teknik, str) else ''}")
    if isinstance(b.Rod_flagga, str): L.append(f"RÖD FLAGGA: {b.Rod_flagga}")
    rp = d["rap"][d["rap"].Ticker == t].sort_values("Publicerat", ascending=False).head(4)
    L.append("RAPPORTER (reaktion): " + ("; ".join(f"{r.Publicerat.date()} dag {f(r.Reaktion_dag,1)} %, 2 d {f(r.Reaktion_2d,1)} %, utfall {r.Utfall}" for r in rp.itertuples()) if len(rp) else "saknas i underlaget: kolla bolagets finanskalender och senaste rapport på webben"))
    L.append(f"RAPPORTDATA: senast {b.Rapport_senast if isinstance(b.Rapport_senast, str) else '–'}; nästa {b.Rapport_nasta_uppsk if isinstance(b.Rapport_nasta_uppsk, str) else '–'} ({b.Rapport_kalla if isinstance(b.Rapport_kalla, str) else 'okänd källa'})")
    ik = insyn(d, t, IDAG - pd.Timedelta(days=270))
    bf = blockflagga(d, t)
    if bf and bf["flagga"]:
        L.append(f"MISSTÄNKT BLOCKAFFÄR/NYEMISSION: största enskilda köp {bf['mkr']} Mkr ({bf['datum']}) = {bf['pct']} % av börsvärdet; övriga köp i fönstret {bf['rest_mkr']} Mkr. KONTROLLERA på webben om det var marknadsköp, nyemission, blockaffär eller ägarbyte innan analysen, skriv det överst, sänk Insyn-poängen.")
    if len(ik):
        L.append("INSYN (270 d, publicerat):")
        for r in ik.head(10 if not kompakt else 6).itertuples():
            L.append(f"  {r.Publiceringsdatum.date()} {r.Karaktär} {getattr(r, '_3', '')} ({str(r.Befattning)[:30]}): {f(r.Volym,0)} à {f(r.Pris,2)} = {f(r.Belopp/1e3,0)} kSEK")
    mfn = d["mfn"][d["mfn"].Ticker == t].sort_values("Publicerat", ascending=False)
    L.append(f"MFN/PRESSMEDDELANDEN ({len(mfn)} i underlaget sedan {mfn.Publicerat.min().date() if len(mfn) else '–'}):" + ("" if len(mfn) else " inget: sök på webben"))
    for r in mfn.head(12 if not kompakt else 8).itertuples():
        L.append(f"  {r.Publicerat.date()} [{r.Typ}] {r.Rubrik[:140]}")
    if isinstance(b.Nyhet_rubrik, str): L.append(f"NYHET FÖRE INSIDERKÖPET: {b.Nyhet_fore}: {b.Nyhet_rubrik[:140]}")
    if isinstance(b.Aterkop, str): L.append(f"ÅTERKÖP: {b.Aterkop}")
    return "\n".join(L)


def tillstand(d, t):
    """Det som texten ska spegla: vändningssignal (datum), senaste MFN-länkar och insynsrader."""
    import vandning
    c = d["p"][t].dropna() if t in d["p"].columns else pd.Series(dtype=float)
    sig = None
    b = d["b"].loc[t]
    if len(c) > 60 and t in d["v"].columns:
        s = vandning.signaler(c, d["v"][t], b.Forsta_publ, tillbaka=10 ** 6)
        if s: sig = str(s[0][0].date())
    return sig


def nytt_sedan(d, t, s, alder=0):
    c = d["p"][t].dropna() if t in d["p"].columns else pd.Series(dtype=float)
    sedan = pd.Timestamp(s["datum"]); skal = []; poang = 0
    mfn = d["mfn"][d["mfn"].Ticker == t]
    ny = mfn[~mfn.Lank.isin(s.get("mfn", [])) & (mfn.Publicerat >= sedan - pd.Timedelta(days=3))]
    nys = ny[ny.Typ.isin(STARK_TYP)] if len(ny) else ny
    if len(nys):
        skal.append(f"{len(nys)} nya viktiga nyheter: " + " | ".join(f"{r.Publicerat.date()} [{r.Typ}] {r.Rubrik[:60]}" for r in nys.head(3).itertuples())); poang += 10 + len(nys)
    if len(c[c.index > sedan]) and s.get("kurs"):
        tot = float((c.iloc[-1] / s["kurs"] - 1) * 100)
        dag = (c.pct_change() * 100)[c.index > sedan]
        dagd = dag.abs().idxmax() if len(dag) else None
        if abs(tot) >= 15: skal.append(f"kurs {f(tot,0)} % sedan {s['datum']}"); poang += 8 + abs(tot) / 10
        if dagd is not None and abs(dag.loc[dagd]) >= 8: skal.append(f"dagsrörelse {f(dag.loc[dagd],0)} % {dagd.date()}"); poang += 6
    ins = insyn(d, t, sedan - pd.Timedelta(days=3)); ins = ins[~ins.id.isin(s.get("insyn", []))]
    kop = float(ins[ins.Karaktär == "Förvärv"].Belopp.sum()) if len(ins) else 0.0
    sal = float(ins[ins.Karaktär == "Avyttring"].Belopp.sum()) if len(ins) else 0.0
    if kop >= 250_000 or sal >= 500_000: skal.append(f"nya insynsaffärer: köp {f(kop/1e3,0)} kSEK, sälj {f(sal/1e3,0)} kSEK"); poang += 5
    sig = tillstand(d, t)
    if sig and sig != s.get("vand"): skal.append(f"ny vändningssignal {sig}"); poang += 7
    if alder >= 35: skal.append(f"texten är {alder} dagar gammal"); poang += 3 + alder / 30
    return dict(skal=skal, poang=poang)


def behov(d, maxn=99):
    tx, st = jlas(TEXT), jlas(STATE); ut = []
    for t in d["tick"]:
        k = kort_t(t)
        if k not in tx or k not in st: ut.append((99, k, ["text saknas"])); continue
        n = nytt_sedan(d, t, st[k], (IDAG - pd.Timestamp(tx[k]["uppdaterad"])).days)
        if n["skal"]: ut.append((n["poang"], k, n["skal"]))
    ut.sort(key=lambda x: -x[0])
    return ut[:maxn]


def kontrollera(x):
    fel = []
    for k in ("kort", "bild", "tolkning"):
        v = x.get(k)
        if not isinstance(v, str) or not v.strip(): fel.append(f"{k} saknas"); continue
        a, b = GRANS[k]
        if not a <= len(v) <= b: fel.append(f"{k} har {len(v)} tecken (ska vara {a}-{b})")
        if "\n" in v: fel.append(f"{k} får inte innehålla radbrytningar")
        if FORBJUDET.search(v): fel.append(f"{k} innehåller råd om köp/sälj: skriv beskrivande")
    for nyckel, namn in (("belagt", "belagt"), ("bevaka", "bevaka"), ("villkor", "villkor")):
        lst = x.get(nyckel); n0, n1 = GRANS[nyckel + "_n"]; l0, l1 = GRANS[nyckel + "_len"]
        if not isinstance(lst, list) or not n0 <= len(lst) <= n1: fel.append(f"{namn} ska vara en lista med {n0}-{n1} punkter"); continue
        for s in lst:
            if not isinstance(s, str) or not l0 <= len(s) <= l1: fel.append(f"{namn}-punkt har fel längd: {str(s)[:40]}")
            elif FORBJUDET.search(s): fel.append(f"{namn} innehåller råd om köp/sälj")
    hv = x.get("handelser")
    if not isinstance(hv, list) or not GRANS["handelser_n"][0] <= len(hv) <= GRANS["handelser_n"][1]: fel.append(f"handelser ska vara en lista med {GRANS['handelser_n'][0]}-{GRANS['handelser_n'][1]} poster {{d, t}} (datum och text till grafen)")
    else:
        for h in hv:
            if not (isinstance(h, dict) and isinstance(h.get("d"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", h["d"]) and isinstance(h.get("t"), str) and GRANS["handelse_len"][0] <= len(h["t"]) <= GRANS["handelse_len"][1]): fel.append(f"ogiltig händelse: {str(h)[:50]}")
            elif FORBJUDET.search(h["t"]): fel.append("handelser innehåller råd om köp/sälj")
    kl = x.get("kallor")
    if not isinstance(kl, list) or not 1 <= len(kl) <= 8: fel.append("kallor ska vara 1-8 par [titel, url]")
    else:
        for k in kl:
            if not (isinstance(k, list) and len(k) == 2 and isinstance(k[1], str) and k[1].startswith("https://") and k[0].strip()): fel.append(f"ogiltig källa: {k}")
    return fel


def spara(d, k):
    t = namn_till_tick(k); x = json.loads(sys.stdin.read())
    if t not in d["b"].index: sys.exit(f"{t} finns inte i bevakning.csv")
    fel = kontrollera(x)
    if fel: sys.exit("Sparades inte:\n- " + "\n- ".join(fel))
    c = d["p"][t].dropna() if t in d["p"].columns else pd.Series([np.nan], index=[IDAG])
    tx, st = jlas(TEXT), jlas(STATE); k = kort_t(t)
    tx[k] = dict(kort=x["kort"].strip(), bild=x["bild"].strip(), belagt=[s.strip() for s in x["belagt"]], tolkning=x["tolkning"].strip(),
                 bevaka=[s.strip() for s in x["bevaka"]], villkor=[s.strip() for s in x["villkor"]], handelser=sorted(x["handelser"], key=lambda h: h["d"]), kallor=x["kallor"], uppdaterad=str(IDAG.date()))
    mfn = d["mfn"][d["mfn"].Ticker == t]; ik = insyn(d, t, IDAG - pd.Timedelta(days=270))
    st[k] = dict(datum=str(c.index[-1].date()), kurs=None if pd.isna(c.iloc[-1]) else round(float(c.iloc[-1]), 3), mfn=sorted(set(mfn.Lank.tolist())),
                 insyn=sorted(set(ik.id.tolist())), vand=tillstand(d, t) if len(c) > 60 else None)
    jskriv(TEXT, tx); jskriv(STATE, st)
    print(f"{k}: sparad ({len(tx[k]['kort'])}+{len(tx[k]['bild'])} tecken)")


def spara_rk(d, k):
    t = namn_till_tick(k); x = json.loads(sys.stdin.read())
    if t not in d["b"].index: sys.exit(f"{t} finns inte i bevakning.csv")
    fel = []
    rk, n, kalla, nt = x.get("rk"), x.get("antal"), x.get("kalla", ""), x.get("not", "")
    c = d["p"][t].dropna().iloc[-1] if t in d["p"].columns and d["p"][t].notna().any() else None
    if rk is not None:
        if not isinstance(rk, (int, float)) or rk <= 0: fel.append("rk ska vara ett positivt tal (snittriktkurs i kr) eller null")
        elif c is not None and not 0.2 <= rk / float(c) <= 10: fel.append(f"rk {rk} ligger orimligt långt från kursen {c:.2f}: kontrollera aktieslag och valuta")
        if not isinstance(n, int) or n < 1: fel.append("antal ska vara heltal >= 1 när rk anges")
        if not (isinstance(kalla, str) and kalla.startswith("https://")): fel.append("kalla ska vara en https-länk när rk anges")
    if not isinstance(nt, str) or len(nt) > 250: fel.append("not ska vara text på högst 250 tecken (vilka analytiker och datum; vad som är osäkert)")
    if fel: sys.exit("Sparades inte:\n- " + "\n- ".join(fel))
    r = jlas(RK); r[kort_t(t)] = dict(rk=rk, antal=n if rk is not None else 0, kalla=kalla if rk is not None else "", **{"not": nt.strip()}, gammal=bool(x.get("gammal")) and rk is not None, kollad=str(IDAG.date()))
    jskriv(RK, r)
    print(f"{kort_t(t)}: " + (f"riktkurs {rk} kr ({n} analytiker), uppsida {round((rk / float(c) - 1) * 100)} %" if rk is not None and c is not None else "ingen riktkurs hittad") + " sparad")


def lista_rk(d, maxn=99):
    r = jlas(RK); ut, ok = [], []
    for t in d["b"].index:
        k = kort_t(t); c = d["p"][t].dropna().iloc[-1] if t in d["p"].columns and d["p"][t].notna().any() else None
        e = r.get(k); alder = (IDAG - pd.Timestamp(e["kollad"])).days if e else None
        if e is None or alder > 21: ut.append((k, c, e, alder, t))
        else: ok.append((k, c, e, alder, t))
    for k, c, e, a, _t in sorted(ut, key=lambda z: -float(d["b"].loc[z[4]].get("Belopp", 0) or 0))[:maxn]:
        print(f"KOLLA {k}: kurs {c:.2f}; " + ("aldrig kollad" if e is None else f"kollad för {a} dagar sedan"))
    for k, c, e, a, _t in ok:
        print(f"OK    {k}: kurs {c:.2f}; " + (f"riktkurs {e['rk']} ({e['antal']} analytiker), uppsida {round((e['rk'] / float(c) - 1) * 100)} %" if e["rk"] is not None else "ingen riktkurs hittad") + f"; kollad {e['kollad']}")
    print(f"{len(ut)} fall behöver kollas, {len(ok)} är aktuella")


def kontrollera_djup(x):
    fel = []
    sl = x.get("slutsats")
    if not isinstance(sl, str) or not 80 <= len(sl) <= 700: fel.append("slutsats ska vara 80-700 tecken")
    elif FORBJUDET.search(sl): fel.append("slutsats innehåller råd om köp/sälj")
    fr = x.get("fraga")
    if not isinstance(fr, str) or not 20 <= len(fr) <= 250: fel.append("fraga ska vara 20-250 tecken (den konkreta frågan djupanalysen svarar på)")
    vf = x.get("varfor")
    if not isinstance(vf, str) or not 60 <= len(vf) <= 350: fel.append("varfor ska vara 60-350 tecken: 1-2 meningar överst om varför caset är intressant just nu (vad som sticker ut), utan råd")
    elif FORBJUDET.search(vf): fel.append("varfor innehåller råd om köp/sälj")
    dl = x.get("delar")
    if not isinstance(dl, dict): fel.append("delar saknas"); dl = {}
    for k, namn in DELAR:
        v = dl.get(k)
        if not (isinstance(v, dict) and isinstance(v.get("p"), int) and 0 <= v["p"] <= 5 and isinstance(v.get("t"), str) and 30 <= len(v["t"]) <= 450): fel.append(f"del {k} ska vara {{p: 0-5, t: 30-450 tecken}}"); continue
        if FORBJUDET.search(v["t"]): fel.append(f"del {k} innehåller råd om köp/sälj")
    hv = x.get("handelser")
    if not isinstance(hv, list) or not GRANS["handelser_n"][0] <= len(hv) <= GRANS["handelser_n"][1]: fel.append("handelser ska vara 3-10 poster {d, t} (datum och text till grafen)")
    else:
        for h in hv:
            if not (isinstance(h, dict) and isinstance(h.get("d"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", h["d"]) and isinstance(h.get("t"), str) and GRANS["handelse_len"][0] <= len(h["t"]) <= GRANS["handelse_len"][1]): fel.append(f"ogiltig händelse: {str(h)[:50]}")
    tm = x.get("timing")
    if not isinstance(tm, str) or not 60 <= len(tm) <= 600: fel.append("timing ska vara 60-600 tecken (kommentar om tidpunkten: kommande datum, nivåer, vad som gör tidpunkten bättre eller sämre; inget råd)")
    elif FORBJUDET.search(tm): fel.append("timing innehåller råd om köp/sälj")
    jf = x.get("jamforelse")
    if not isinstance(jf, str) or not 30 <= len(jf) <= 450: fel.append("jamforelse ska vara 30-450 tecken (bransch eller namngivna peers, och storleksgrupp; säg om data saknas)")
    ko = x.get("konsensus")
    if not isinstance(ko, str) or not 30 <= len(ko) <= 450: fel.append("konsensus ska vara 30-450 tecken (vad som hittats och från vem, eller att inget hittats)")
    sc = x.get("scenarier")
    if not isinstance(sc, list) or not 3 <= len(sc) <= 5: fel.append("scenarier ska vara 3-5 poster {n, f, lag, hog}")
    else:
        for r in sc:
            if not (isinstance(r, dict) and isinstance(r.get("n"), str) and isinstance(r.get("f"), str) and 20 <= len(r["f"]) <= 300 and all(isinstance(r.get(k), (int, float)) and r[k] > 0 for k in ("lag", "hog")) and r["lag"] <= r["hog"]): fel.append(f"ogiltigt scenario: {str(r)[:60]}")
            elif FORBJUDET.search(r["f"]): fel.append("scenario innehåller råd om köp/sälj")
    kl = x.get("kallor")
    if not isinstance(kl, list) or not 2 <= len(kl) <= 10: fel.append("kallor ska vara 2-10 par [titel, url]")
    else:
        for k in kl:
            if not (isinstance(k, list) and len(k) == 2 and isinstance(k[1], str) and k[1].startswith("https://") and k[0].strip()): fel.append(f"ogiltig källa: {k}")
    return fel


def spara_djup(d, k):
    t = namn_till_tick(k); x = json.loads(sys.stdin.read())
    if t not in d["b"].index: sys.exit(f"{t} finns inte i bevakning.csv")
    fel = kontrollera_djup(x)
    if fel: sys.exit("Sparades inte:\n- " + "\n- ".join(fel))
    c = d["p"][t].dropna() if t in d["p"].columns else pd.Series([np.nan], index=[IDAG])
    dj = jlas(DJUP); summa = sum(x["delar"][a]["p"] for a, _ in DELAR)
    dj[kort_t(t)] = dict(fraga=x["fraga"].strip(), varfor=x["varfor"].strip(), slutsats=x["slutsats"].strip(), delar={a: dict(p=x["delar"][a]["p"], t=x["delar"][a]["t"].strip()) for a, _ in DELAR},
                         summa=summa, poang=round(summa / 30 * 100), kallor=x["kallor"], konsensus=x["konsensus"].strip(), jamforelse=x["jamforelse"].strip(), handelser=sorted(x["handelser"], key=lambda h: h["d"]), timing=x["timing"].strip(),
                         scenarier=[dict(n=r["n"].strip(), f=r["f"].strip(), lag=r["lag"], hog=r["hog"], pl=round((r["lag"] / float(c.iloc[-1]) - 1) * 100), ph=round((r["hog"] / float(c.iloc[-1]) - 1) * 100)) for r in x["scenarier"]], datum=str(IDAG.date()),
                         kurs=None if pd.isna(c.iloc[-1]) else round(float(c.iloc[-1]), 3), bedomning="Min bedömning efter genomläst material. Går inte att backtesta; utfallet följs upp framåt.")
    jskriv(DJUP, dj)
    print(f"{kort_t(t)}: djupanalys sparad, poäng {dj[kort_t(t)]['poang']}/100 ({summa}/30)")


def main():
    a = sys.argv[1:]
    if not a: sys.exit(__doc__)
    cmd, rest = a[0], a[1:]
    if cmd == "kontroll":
        tx = jlas(TEXT); b = pd.read_csv(HER / "bevakning.csv"); bad = 0; ok = 0
        for t in b.Ticker:
            k = kort_t(t)
            if k not in tx: print(f"{k}: text saknas"); bad += 1; continue
            ok += 1
            for e in kontrollera(tx[k]): print(f"{k}: {e}"); bad += 1
        print(f"{ok} av {len(b)} fall har text, {bad} fel"); sys.exit(1 if bad else 0)
    d = las()
    if cmd == "behov":
        n = int(rest[rest.index("--max") + 1]) if "--max" in rest else 99
        r = behov(d, n)
        for _, t, s in r: print(f"{t}: " + "; ".join(s))
        print(f"{len(r)} fall behöver ny text" if r else "Inget fall behöver ny text")
    elif cmd == "dossier":
        komp = "--kompakt" in rest; rest = [x for x in rest if x != "--kompakt"]
        ts = list(d["b"].index) if rest == ["alla"] else [namn_till_tick(x) for x in rest]
        for t in ts: print(dossier(d, t, komp)); print()
    elif cmd == "spara":
        spara(d, rest[0])
    elif cmd == "spara-rk":
        spara_rk(d, rest[0])
    elif cmd == "rk":
        lista_rk(d, int(rest[rest.index("--max") + 1]) if "--max" in rest else 99)
    elif cmd == "spara-djup":
        spara_djup(d, rest[0])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
