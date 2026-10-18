"""Placeras dagliga rekommendationslistor (riktkursändringar) -> riktkurs_nyhet.csv

Hittar sidorna via Placeras sitemap (artiklar/ÅÅÅÅ-MM-articles.xml), läser dem och tolkar raderna
"<analyshus> höjer/sänker riktkursen för <bolag> till X (Y)". Respekterar robots.txt, 1 anrop/sekund,
identifierar sig. Sparar bara tolkade fakta (inte sidornas text).

Användning: python placera_arkiv.py [FRAN=2021-01-01] [TILL=idag]
Skriver: riktkurs_nyhet.csv (Datum,Firma,Bolag,Ny,Gammal,Valuta,Rek,Typ,Kalla), placera_sidor.csv (bearbetade sidor),
placera_ejtolkat.txt (rader med 'riktkurs' som tolken inte förstod), placera_logg.txt skrivs av workflowen.
"""
import csv, html, json, os, re, sys, time, urllib.robotparser, datetime as dt
import requests
from bs4 import BeautifulSoup

UA = "insiderbot-research/1.0 (+https://github.com/wilhelmjohn/insiderbot-run)"
BAS = "https://www.placera.se"
S = requests.Session(); S.headers["User-Agent"] = UA
FRAN = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "2021-01-01"
TILL = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else dt.date.today().isoformat()
UT = "riktkurs_nyhet.csv"; SIDOR = "placera_sidor.csv"; EJ = "placera_ejtolkat.txt"
KOL = ["Datum", "Firma", "Bolag", "Ny", "Gammal", "Valuta", "Rek", "Typ", "Kalla"]

def hamta(url, tillat404=False):
    time.sleep(1.0)
    for f in range(3):
        try:
            r = S.get(url, timeout=40)
            if r.status_code == 404 and tillat404: return None
            if r.status_code == 200: return r
            print("  HTTP", r.status_code, url, flush=True)
        except Exception as e:
            print("  FEL", e, url, flush=True)
        time.sleep(5 * (f + 1))
    return None

rob = hamta(BAS + "/robots.txt")
if rob is None:
    print("KAN INTE LÄSA robots.txt: avbryter"); sys.exit(0)
rp = urllib.robotparser.RobotFileParser(); rp.parse(rob.text.splitlines())
def tillaten(u): return rp.can_fetch(UA, u)

NUM = r"\d[\d\s ]*(?:[.,]\d+)?"
VAL = r"(danska kronor|norska kronor|danska kr|norska kr|svenska kronor|amerikanska dollar|dollar|brittiska pund|pund|schweiziska franc|schweiziska kr|euro|kronor|kr)"
def tal(s):
    s = re.sub(r"[\s ]", "", s).replace(",", ".")
    try: return float(s)
    except: return None
def valuta(s):
    s = (s or "kronor").lower()
    return {"danska kronor": "DKK", "norska kronor": "NOK", "danska kr": "DKK", "norska kr": "NOK", "euro": "EUR", "dollar": "USD",
            "amerikanska dollar": "USD", "pund": "GBP", "brittiska pund": "GBP", "schweiziska franc": "CHF",
            "schweiziska kr": "CHF"}.get(s, "SEK")
FIRMA = r"(?P<firma>[^.]+?)"
VERB = r"(?:har )?(?:höjer|sänker|höjt|sänkt|upprepar|bibehåller|justerar)"
RK = rf"(?:sin )?riktkurs(?:en)?"
NYV = rf"(?P<ny>{NUM}) ?(?P<val>{VAL})?"
P = [
 # A: "X sänker riktkursen för Y till 215 danska kronor (245), upprepar köp"
 re.compile(rf"^{FIRMA} {VERB} {RK} för (?P<bolag>.+?) till {NYV}(?: ?\((?P<gammal>{NUM})\))?(?:,? (?:upprepar|höjer|sänker|rekommendationen)\s*(?P<rek>[^.]*?))?\.?$", re.I),
 # B: "... riktkursen för Y till 360 kronor från (tidigare) 330 kronor. Rekommendationen köp upprepas."
 re.compile(rf"^{FIRMA} {VERB} {RK} för (?P<bolag>.+?) till {NYV} från (?:tidigare )?(?P<gammal>{NUM})(?: ?{VAL})?\.?(?:.*?(?:Rekommendationen|rekommendationen) (?P<rek>\w+(?: \w+)?) (?:upprepas|bibehålls))?", re.I),
 # C: "X höjer Y till köp (behåll)[ på lång sikt], (ny) riktkurs 11 euro (10)"
 re.compile(rf"^{FIRMA} (?:höjer|sänker|upprepar) (?P<bolag>.+?) (?:till|med) (?P<rek>[^,(]+?)(?: \((?P<grek>[^)]+)\))?(?: på lång sikt)?,? (?:och )?(?:ny |upprepar )?riktkurs(?:en)? (?:på )?{NYV}(?: ?\((?P<gammal>{NUM})\))?", re.I),
 # D: "X höjer sin rekommendation för Y till köp från behåll. Riktkursen höjs till 700 från 560 kronor."
 re.compile(rf"^{FIRMA} (?:höjer|sänker) sin rekommendation för (?P<bolag>.+?) till (?P<rek>.+?) från (?P<grek>[^.]+)\. Riktkursen (?:höjs|sänks) till {NYV} från (?P<gammal>{NUM})", re.I),
 # F: "X sänker sin (långsiktiga) rekommendation för Y (, på tre års sikt,) till R från G. Riktkursen är (fortsatt) 140 kronor."
 re.compile(rf"^{FIRMA} (?:höjer|sänker) sin (?:långsiktiga )?rekommendation för (?P<bolag>.+?)(?:, på [^,]+,)? till (?P<rek>.+?) från (?P<grek>[^.]+)\. Riktkursen är (?:fortsatt )?{NYV}", re.I),
 # E: "X inleder/återupptar bevakning av/på Y med (rekommendationen) köp och riktkurs 460 kronor"
 re.compile(rf"^{FIRMA} (?:inleder|startar|återupptar|har inlett|har återupptagit) bevakning (?:av|på) (?P<bolag>.+?) med (?:rekommendation(?:en)? )?(?P<rek>\w+(?: \w+)?) och riktkurs(?:en)? (?:på )?{NYV}", re.I),
]
PG = [  # utan bolagsnamn i texten (bolaget står i tabellens första kolumn)
 re.compile(rf"^{FIRMA} {VERB} {RK} till {NYV}(?: ?\((?P<gammal>{NUM})\))?(?:,? (?:upprepar|höjer|sänker)\s*(?P<rek>[^.]*?))?\.?$", re.I),
 re.compile(rf"^{FIRMA} (?:höjer|sänker|upprepar|inleder bevakning)(?: till| med)? (?P<rek>[^,(]+?)(?: \((?P<grek>[^)]+)\))?,? (?:och )?(?:ny |upprepar )?riktkurs(?:en)? (?:på )?{NYV}(?: ?\((?P<gammal>{NUM})\))?\.?$", re.I),
 re.compile(rf"^{FIRMA} (?:höjer|sänker) sin (?:långsiktiga )?rekommendation till (?P<rek>.+?) från (?P<grek>[^.]+)\. Riktkursen (?:höjs|sänks|är)(?: fortsatt)?(?: från (?P<gammal>{NUM}) ?(?:{VAL})? till)? {NYV}", re.I),
]
def rensa_bolag(b):
    t = b.split()
    while len(t) > 1 and t[0].islower(): t = t[1:]
    return " ".join(t)
def tolka(t, ctx=None):
    t = re.sub(r"\s+", " ", t.replace(" ", " ")).strip()
    for i, p in enumerate(P + (PG if ctx else [])):
        m = p.match(t)
        if m:
            d = m.groupdict()
            ny, gam = tal(d["ny"]), tal(d["gammal"]) if d.get("gammal") else None
            if ny is None: return None
            return dict(Firma=d["firma"].strip(), Bolag=rensa_bolag((d.get("bolag") or ctx).strip()), Ny=ny, Gammal=gam, Valuta=valuta(d.get("val")),
                        Rek=(d.get("rek") or "").strip(), Typ=("start" if re.search(r"inleder|inlett|startar|återupptar|återupptagit", t, re.I) else "rk"))
    return None

def sidrader(htm):
    s = BeautifulSoup(htm, "lxml")
    dat = None
    for sc in s.find_all("script", type="application/ld+json"):
        try:
            j = json.loads(sc.string or "")
            for o in (j if isinstance(j, list) else [j]):
                if isinstance(o, dict) and o.get("datePublished"): dat = o["datePublished"][:10]
        except Exception: pass
    rader, ej = [], []
    for br in s.find_all("br"): br.replace_with("\n")
    for tr in s.find_all("tr"):
        c = tr.find_all(["td", "th"])
        if len(c) != 2: continue
        bol = re.sub(r"\s+", " ", c[0].get_text(" ", strip=True)).strip()
        for t in c[1].get_text(" ", strip=False).split("\n"):
            t = re.sub(r"\s+", " ", t).strip()
            if "riktkurs" not in t.lower() or not bol or bol == "Aktie": continue
            r = tolka(t, bol)
            if r: rader.append(r)
            else: ej.append(f"[{bol}] {t}")
        tr.decompose()
    cur = None
    for el in s.find_all(["h3", "h2", "p", "td", "li", "div"]):
        if el.name in ("h3", "h2"):
            st = el.find("strong")
            tx = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
            cur = tx if st is not None and 0 < len(tx) < 70 else None
            continue
        if el.find(["p", "td", "li", "div"]): continue
        for t in el.get_text(" ", strip=False).split("\n"):
            t = re.sub(r"\s+", " ", t).strip()
            if "riktkurs" not in t.lower(): continue
            r = tolka(t, cur)
            if r: rader.append(r)
            elif len(t) < 400: ej.append(t)
    return dat, rader, ej

# --- upptäck sidor via sitemap
idx = hamta(BAS + "/sitemap.xml")
manader = re.findall(r"https://www\.placera\.se/artiklar/(\d{4}-\d{2})-articles\.xml", idx.text) if idx else []
f0, t0 = FRAN[:7], TILL[:7]
SLUG = re.compile(r"aktierekar|aktierekommendationer|rekommendationer-i-oversikt|alla-nya-rekar|dagens-rekar|riktkurs|kopstampel|kopstampl", re.I)
sidor = {}
for m in sorted(set(manader)):
    if not (f0 <= m <= t0): continue
    u = f"{BAS}/artiklar/{m}-articles.xml"
    if not tillaten(u): continue
    x = hamta(u)
    if x is None: continue
    locs = re.findall(r"<loc>([^<]+)</loc>", x.text)
    traff = [l for l in locs if SLUG.search(l)]
    print(f"sitemap {m}: {len(locs)} artiklar, {len(traff)} rekar-sidor", flush=True)
    for l in traff: sidor[l] = m
    rest = [l.rsplit("/", 1)[-1] for l in locs if not SLUG.search(l) and re.search(r"riktkurs|kopstampel|rekommend|rekar|rekyl", l, re.I)]
    if rest: print(f"   ej matchade men liknande ({len(rest)}):", ", ".join(rest[:25]), flush=True)
nyhet = hamta(BAS + "/news-sitemap.xml")
if nyhet:
    for l in re.findall(r"<loc>([^<]+)</loc>", nyhet.text):
        if SLUG.search(l): sidor.setdefault(l, "nyhet")
print("sidor totalt:", len(sidor), flush=True)

klara = set()
if os.path.exists(SIDOR):
    klara = {r["Url"] for r in csv.DictReader(open(SIDOR, encoding="utf-8"))}
os.makedirs("placera_debug", exist_ok=True)
nytt_ut = not os.path.exists(UT); nytt_sid = not os.path.exists(SIDOR)
fut = open(UT, "a", newline="", encoding="utf-8"); wut = csv.writer(fut)
fsi = open(SIDOR, "a", newline="", encoding="utf-8"); wsi = csv.writer(fsi)
fej = open(EJ, "a", encoding="utf-8")
if nytt_ut: wut.writerow(KOL)
if nytt_sid: wsi.writerow(["Datum", "Url", "Antal"])
antal_sid = antal_rad = 0
for url in sorted(sidor):
    if url in klara or not tillaten(url): continue
    r = hamta(url)
    if r is None: continue
    dat, rader, ej = sidrader(r.text)
    if ej and len(ej) > len(rader) and len(os.listdir("placera_debug")) < 6:
        i = r.text.find(ej[0].split("] ")[-1][:40]) if ej else -1
        open(f"placera_debug/{dat or 'x'}.html", "w", encoding="utf-8").write(r.text[max(0, i - 6000):i + 3000] if i > 0 else r.text[100000:106000])
    if not dat:
        mm = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", url)
        dat = f"{mm.group(1)}-{mm.group(2)}-{mm.group(3)}" if mm else ""
    if dat and not (FRAN <= dat <= TILL): continue
    sett = set()
    for x in rader:
        k = (x["Firma"], x["Bolag"], x["Ny"], x["Gammal"])
        if k in sett: continue
        sett.add(k)
        wut.writerow([dat, x["Firma"], x["Bolag"], x["Ny"], "" if x["Gammal"] is None else x["Gammal"], x["Valuta"], x["Rek"], x["Typ"], url])
    for t in ej[:50]: fej.write(f"{dat}\t{t}\n")
    wsi.writerow([dat, url, len(sett)])
    antal_sid += 1; antal_rad += len(sett)
    if antal_sid % 25 == 0:
        fut.flush(); fsi.flush(); fej.flush()
        print(f"  {antal_sid} sidor, {antal_rad} rader (senast {dat})", flush=True)
fut.close(); fsi.close(); fej.close()
print(f"KLAR: {antal_sid} nya sidor, {antal_rad} nya rader, {FRAN}..{TILL}")
