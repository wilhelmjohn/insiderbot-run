"""Kvalitativa texter för bevakningslistan: underlag, behovsbedömning och sparning.

  python bev_sf.py behov [--max N]          vilka bolag som behöver ny text och varför
  python bev_sf.py dossier [--kompakt] T..  underlag per bolag (T = ticker utan .ST, t.ex. PLEJD). "alla" ger samtliga fall i bevakning.csv
  python bev_sf.py spara T                  läser JSON på stdin (kort, bild, belagt, tolkning, bevaka, kallor) och sparar i bev_sammanfattning.json
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
IDAG = pd.Timestamp.now(tz="Europe/Stockholm").tz_localize(None).normalize()
GRANS = dict(kort=(60, 300), bild=(120, 900), tolkning=(40, 1000), belagt_n=(2, 7), belagt_len=(15, 330), bevaka_n=(1, 3), bevaka_len=(15, 200))
FORBJUDET = re.compile(r"\b(köp nu|sälj nu|bör köpa|bör sälja|rekommenderar (?:att )?(?:köpa|sälja)|köpläge|säkert köp|kan inte förlora)\b", re.I)
STARK_TYP = {"rapport", "vinstvarning", "prognoshojning", "nyemission", "m_and_a", "ledning"}


def jlas(p):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def jskriv(p, x):
    p.write_text(json.dumps(x, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def f(x, d=1):
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
    for nyckel, namn in (("belagt", "belagt"), ("bevaka", "bevaka")):
        lst = x.get(nyckel); n0, n1 = GRANS[nyckel + "_n"]; l0, l1 = GRANS[nyckel + "_len"]
        if not isinstance(lst, list) or not n0 <= len(lst) <= n1: fel.append(f"{namn} ska vara en lista med {n0}-{n1} punkter"); continue
        for s in lst:
            if not isinstance(s, str) or not l0 <= len(s) <= l1: fel.append(f"{namn}-punkt har fel längd: {str(s)[:40]}")
            elif FORBJUDET.search(s): fel.append(f"{namn} innehåller råd om köp/sälj")
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
                 bevaka=[s.strip() for s in x["bevaka"]], kallor=x["kallor"], uppdaterad=str(IDAG.date()))
    mfn = d["mfn"][d["mfn"].Ticker == t]; ik = insyn(d, t, IDAG - pd.Timedelta(days=270))
    st[k] = dict(datum=str(c.index[-1].date()), kurs=None if pd.isna(c.iloc[-1]) else round(float(c.iloc[-1]), 3), mfn=sorted(set(mfn.Lank.tolist())),
                 insyn=sorted(set(ik.id.tolist())), vand=tillstand(d, t) if len(c) > 60 else None)
    jskriv(TEXT, tx); jskriv(STATE, st)
    print(f"{k}: sparad ({len(tx[k]['kort'])}+{len(tx[k]['bild'])} tecken)")


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
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
