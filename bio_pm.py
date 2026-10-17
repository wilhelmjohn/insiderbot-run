"""Hämtar pressmeddelanden för biotechbolagen (körs i Actions, som har öppen webbåtkomst) och skickar push vid viktiga nyheter.
Källor, i ordning: Cision-flöde (RSS), MFN-flöde (RSS), MFN:s listsida (HTML). Vilken källa som fungerade för varje bolag skrivs till bio_pm_status.csv.
Resultat: bio_pm.csv (Ticker, Datum, Rubrik, Lank, Kalla, Flagga, Text). Första körningen sparar bara utgångsläget utan push.
Användning: python bio_pm.py [--prov]"""
import csv, html, os, re, sys, time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
import pandas as pd, requests

HER = Path(__file__).resolve().parent
UT, STATUS = HER / "bio_pm.csv", HER / "bio_pm_status.csv"
PROV = "--prov" in sys.argv
H = {"User-Agent": "Mozilla/5.0 (compatible; insiderbot/1.0)", "Accept": "application/rss+xml, application/xml, text/html;q=0.8, */*;q=0.5"}
DAGAR = 120
# ord som gör att ett pressmeddelande flaggas (och kan ge push)
VIKTIGT = {
    "FDA": r"\bFDA\b|\bPDUFA\b|complete response|\bCRL\b|\bNDA\b|\bBLA\b|\bEMA\b|\bCHMP\b",
    "Studieresultat": r"topline|toppdata|top-line|interim (results|data|analys)|delresultat|primary endpoint|primärt mål|(positiva|positive|negativa|negative) (data|resultat|results)|phase (1|2|3|i|ii|iii)\w*\b.*(results|data)|fas (1|2|3|i|ii|iii)\w*\b.*(resultat|data)",
    "Emission": r"emission|rights issue|företrädesemission|riktad nyemission|nyemission|kapitalanskaffning|private placement|directed (share )?issue|riktad (nyemission|emission)",
    "Avtal": r"licen[sc]\w*[^.;]{0,30}(agreement|avtal)|licens- och samarbetsavtal|partneravtal|samarbetsavtal|förvärv|acquisition|uppköp|bud på|offer to (the )?shareholders|erbjudande till aktieägarna",
    "Godkännande": r"marknadsgodkänn|godkänt|godkänner|godkännande från|approved|approval (of|from|by) (the )?(fda|ema|european|u\.s\.|us )|fda approval|ce.?märk",
    "Varning": r"vinstvarning|profit warning|avbryter|discontinu|futility|futilitet|nådde ej|did not meet|misslyck|clinical hold",
    # planerad notering, uppflyttning eller nyemission i samband med notering (t.ex. utkast till SEC inför Nasdaq-notering av ADS)
    "Notering": r"potential listing|possible listing|listing of (the )?(ADSs?|American depositary)|notering (av|på|vid) (ADS|Nasdaq|NYSE)|planerad notering|ansöker om notering|ansökan om notering|draft registration statement|registration statement on form|initial public offering|\bIPO\b|uplisting|listbyte|byte av (marknadsplats|handelsplats|lista)|flytt(ar)? (till|av) (huvudlistan|Nasdaq Stockholm|main market)|moves? to (the )?(main market|Nasdaq Stockholm)",
    # ägarförändringar: flaggningsmeddelanden och större innehav
    "Ägare": r"flaggning|flagging|förändring(ar)? i (aktie)?innehav|ändring i innehav|change in (the )?holdings?|holdings? notification|notification of (a )?(major )?(share)?holdings?|major shareholding|större aktieägare|ny storägare|largest shareholder|ownership change",
    # återköp av egna aktier (C-aktier för incitamentsprogram undantas i flagga())
    "Återköp": r"återköp av (egna )?aktier|återköpsprogram|share buy-?back|buy-?back of (own )?shares|repurchase of (own )?shares|repurchase (programme|program)",
}
STARK = {"FDA", "Studieresultat", "Emission", "Avtal", "Godkännande", "Varning", "Notering"}
# rutinmeddelanden som aldrig ska ge push
RUTIN = r"antal(et)? aktier och röster|number of shares and votes|kallelse|notice (to attend|of)|gives notice|kallar till|årsstämma(n)? den|valberedning|nomination committee|incitament|C-aktier|teckningsoptioner|delårsrapport|interim report|webcast|webbkonferens|inbjudan|invitation|investor presentation|bokslutskommuniké|year-end report|financial calendar|finansiell kalender|ändring av antalet|transaktion.*(person i ledande|PDMR)|managers.? transactions|stabiliser"
# fraser som inte betyder det de ser ut att betyda
STRYK = r"(subject to )?approval (by|of)[^.;]{0,50}(general meeting|bolagsstämma)|under förutsättning[^.;]{0,40}(bolagsstämma|general meeting)[^.;]{0,20}godkänn\w*|godkännande av[^.;]{0,50}(bolagsstämma|årsstämma|styrelse)|godkänd[^.;]{0,20}årsredovisning"

def slugar(namn):
    """Möjliga MFN/Cision-namn. Provar först utan 'holding/group', sedan med hela namnet (t.ex. klaria-pharma-holding)."""
    def ren(n, strip):
        n = re.sub(r"\(publ\.?\)|\bAB\b", "", str(n), flags=re.I)
        if strip: n = re.sub(r"\bholding\b|\bgroup\b|\binternational\b", "", n, flags=re.I)
        return [w for w in re.sub(r"[^a-zåäö0-9 ]", " ", n.lower()).split()]
    ut = []
    for ord_ in (ren(namn, True), ren(namn, False)):
        for s in ["-".join(ord_), "".join(ord_), "-".join(ord_[:2]), "".join(ord_[:2]), ord_[0] if ord_ else ""]:
            s = s.replace("å", "a").replace("ä", "a").replace("ö", "o")
            if s and s not in ut: ut.append(s)
    return ut

def hamta(url, tid=25):
    try:
        r = requests.get(url, headers=H, timeout=tid)
        return r.text if r.status_code == 200 else None
    except Exception:
        return None

def rss(text, kalla):
    """Tolkar RSS eller Atom. Returnerar lista (datum, rubrik, länk, ingress)."""
    if not text or "<" not in text[:300]: return []
    try: rot = ET.fromstring(text.encode("utf-8", "ignore"))
    except ET.ParseError: return []
    ut = []
    for it in list(rot.iter("item")) + list(rot.iter("{http://www.w3.org/2005/Atom}entry")):
        def g(*namn):
            for n in namn:
                e = it.find(n)
                if e is not None and (e.text or e.get("href")): return (e.text or e.get("href")).strip()
            return ""
        titel = html.unescape(g("title", "{http://www.w3.org/2005/Atom}title"))
        lank = g("link", "{http://www.w3.org/2005/Atom}link")
        dat = g("pubDate", "{http://www.w3.org/2005/Atom}updated", "{http://www.w3.org/2005/Atom}published")
        try: d = pd.Timestamp(parsedate_to_datetime(dat)).tz_localize(None) if dat and "," in dat[:6] else pd.Timestamp(dat).tz_localize(None)
        except Exception: d = pd.NaT
        ing = re.sub(r"<[^>]+>", " ", html.unescape(g("description", "{http://www.w3.org/2005/Atom}summary")))
        if titel and lank: ut.append((d, titel, lank, re.sub(r"\s+", " ", ing)[:400]))
    return ut

def lista_html(text, slug):
    """Reserv: plocka länkar och rubriker ur MFN:s listsida."""
    ut = []
    for m in re.finditer(r'<a[^>]+href="([^"]*?/a/%s/[^"]+)"[^>]*>(.*?)</a>' % re.escape(slug), text or "", re.S):
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
        if len(t) > 12:
            l = m.group(1); l = l if l.startswith("http") else "https://mfn.se" + l
            ut.append((pd.NaT, t, l, ""))
    return ut

def kallor(ticker, namn):
    for slug in slugar(namn):
        for kalla, url in (("cision", f"https://news.cision.com/se/{slug}?format=rss"), ("cision", f"https://news.cision.com/{slug}?format=rss"),
                           ("mfn", f"https://mfn.se/all/a/{slug}?format=rss"), ("mfn", f"https://mfn.se/all/a/{slug}/rss"), ("mfn", f"https://mfn.se/a/{slug}/rss")):
            x = rss(hamta(url), kalla)
            if x: return kalla + ":" + slug, x
        x = lista_html(hamta(f"https://mfn.se/all/a/{slug}"), slug)
        if x: return "mfn-html:" + slug, x
    return "", []

def flagga(rubrik, text="", insider=False):
    """Flaggor ur rubrik + ingress. Rutinmeddelanden ger inga flaggor. insider = MAR-märkt insiderinformation."""
    t = re.sub(STRYK, " ", rubrik + " " + text, flags=re.I)
    if re.search(RUTIN, rubrik, re.I): return "Insiderinfo" if insider else ""
    f = [k for k, pat in VIKTIGT.items() if re.search(pat, t, re.I)]
    if "Återköp" in f and re.search(r"class c|c-aktier|c shares|incentive|incitament|teckningsoption|warrant", rubrik + " " + text, re.I): f.remove("Återköp")
    if insider: f.append("Insiderinfo")
    return ",".join(f)

def brodtext(url):
    """Returnerar (ren text, insiderinformation?). MFN-sidan börjar med metadata och taggar som tas bort."""
    t = hamta(url, 30)
    if not t: return "", False
    t = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t))).strip()
    insider = "inside-information" in t[:1500]
    tok = t.split(" ")
    sist = max([i for i, w in enumerate(tok[:80]) if re.match(r"^:?[\w-]+:[\w:.-]+$", w)] or [-1])
    i = sist + 1
    while i < len(tok) and re.fullmatch(r"[A-Z]{2}", tok[i]): i += 1
    return " ".join(tok[i:])[:1500] if sist >= 0 else t[:1500], insider

def rensa(t):
    """Tar bort MFN:s metadata/taggar i början av en sparad sidtext."""
    tok = str(t).split(" ")
    sist = max([i for i, w in enumerate(tok[:80]) if re.match(r"^:?[\w-]+:[\w:.-]+$", w)] or [-1])
    i = sist + 1
    while i < len(tok) and re.fullmatch(r"[A-Z]{2}", tok[i]): i += 1
    return " ".join(tok[i:])[:1500] if sist >= 0 else str(t)[:1500]

def main():
    inf = pd.read_csv(HER / "bio_info.csv").set_index("Ticker")
    tick = (HER / "bio_tickers.txt").read_text().split()
    finns = UT.exists()
    gamla = pd.read_csv(UT).fillna("") if finns else pd.DataFrame(columns=["Ticker", "Datum", "Rubrik", "Lank", "Kalla", "Flagga", "Text"])
    sedda = set(gamla.Lank)
    nu = pd.Timestamp.now().normalize()
    # efterhand: hämta saknad brödtext för de senaste 30 dagarna (högst 150 per körning) och räkna om flaggorna med nya reglerna
    if finns:
        saknas = gamla[(gamla.Text == "") & (pd.to_datetime(gamla.Datum, errors="coerce") >= nu - pd.Timedelta(days=30))].index[:150]
        for i in saknas:
            gamla.at[i, "Text"], ins = brodtext(gamla.at[i, "Lank"])
            if ins: gamla.at[i, "Text"] = "[INSIDERINFO] " + gamla.at[i, "Text"]
        gamla["Text"] = [t if t.startswith("[INSIDERINFO]") else rensa(t) for t in gamla.Text]
        gamla["Flagga"] = [flagga(r, "", t.startswith("[INSIDERINFO]") or "inside-information" in t[:600]) for r, t in zip(gamla.Rubrik, gamla.Text)]
    nya, status = [], []
    for t in tick:
        namn = inf.longName.get(t, t)
        kalla, items = kallor(t, namn)
        status.append(dict(Ticker=t, Namn=namn, Kalla=kalla or "ingen", Antal=len(items)))
        print(t, kalla or "INGEN KÄLLA", len(items), flush=True)
        for d, titel, lank, ing in items:
            if lank in sedda: continue
            txt, ins = brodtext(lank) if finns else ("", False)
            nya.append(dict(Ticker=t, Datum=str(d.date()) if pd.notna(d) else str(nu.date()), Rubrik=titel, Lank=lank, Kalla=kalla.split(":")[0],
                            Flagga=flagga(titel, ing, ins), Text=("[INSIDERINFO] " if ins else "") + txt))
            sedda.add(lank)
        time.sleep(0.5)
    alla = pd.concat([gamla, pd.DataFrame(nya)], ignore_index=True)
    alla["Datum"] = pd.to_datetime(alla["Datum"], errors="coerce")
    alla = alla[alla.Datum >= nu - pd.Timedelta(days=DAGAR)].sort_values("Datum", ascending=False)
    alla["Datum"] = alla.Datum.dt.date.astype(str)
    if not PROV:
        alla.to_csv(UT, index=False, quoting=csv.QUOTE_NONNUMERIC); pd.DataFrame(status).to_csv(STATUS, index=False)
    print(f"{len(nya)} nya pressmeddelanden, {sum(1 for s in status if s['Kalla'] == 'ingen')} bolag utan källa; "
          f"{(alla.Flagga != '').sum()} flaggade av {len(alla)}")
    # push: bara på nya, med stark flagga, ingen dubblett (svenska/engelska), inte första körningen
    viktiga, sett = [], set()
    for n in nya:
        fl = set(n["Flagga"].split(",")) - {""}
        if not (fl & STARK) or pd.Timestamp(n["Datum"]) < nu - pd.Timedelta(days=3): continue
        nyckel = (n["Ticker"], n["Datum"], n["Flagga"])
        if nyckel in sett: continue
        sett.add(nyckel); viktiga.append(n)
    if not finns or not viktiga:
        return
    rader = [f"{n['Ticker'].replace('.ST', '')}: {n['Rubrik'][:90]} [{n['Flagga']}]" for n in viktiga[:8]]
    titel = f"Biotech: {len(viktiga)} nytt pressmeddelande" + ("n" if len(viktiga) > 1 else "")
    print(titel); print("\n".join(rader))
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if PROV or not topic: return
    import notis
    notis.KLICK_URL = "https://claude.ai/artifact/DvuBNP1sJaDNbmwAjifFss"
    hog = any(set(n["Flagga"].split(",")) & {"FDA", "Varning", "Emission", "Studieresultat"} for n in viktiga)
    notis.skicka([{"title": titel, "message": "\n".join(rader) + (f"\n+{len(viktiga) - 8} till" if len(viktiga) > 8 else ""),
                   "priority": 4 if hog else 3, "tags": ["microscope"]}], topic, os.environ.get("NTFY_SERVER", "https://ntfy.sh"))

if __name__ == "__main__":
    main()
