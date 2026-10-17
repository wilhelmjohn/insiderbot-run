"""Extra datakällor för Biotechscreen och Trappscreen (körs i Actions; sandlådan når inte källorna). Kraschar aldrig: varje källa har egen felhantering.

  python extra_hamta.py            -> allt nedan
  python extra_hamta.py index      -> bio_index.csv: XBI (amerikansk biotech-ETF) och OMXSPI, för sektorjämförelse
  python extra_hamta.py studier    -> bio_studier.csv + bio_studier_logg.csv: ClinicalTrials.gov per bolag (status, primärt slutdatum, resultat)
  python extra_hamta.py edgar      -> edgar_traffar.csv: nya F-1-/prospektfilningar hos SEC för bevakade bolag (Hansa)

Loggen (vad som hämtades och vad som gick fel) skrivs till extra_logg.txt så att den går att läsa efter körningen.
Push (ntfy) skickas bara vid riktigt viktiga ändringar: studie avbruten/pausad/återtagen, resultat upplagt, ny SEC-filning."""
import json, os, re, sys, time, traceback
from pathlib import Path
import pandas as pd, requests

HER = Path(__file__).resolve().parent
LOGG = []
IDAG = pd.Timestamp.now(tz="Europe/Stockholm").tz_localize(None).normalize()
UA = os.environ.get("SEC_UA", "").strip() or "insiderbot/1.0 (personligt forskningsprojekt)"
CT = "https://clinicaltrials.gov/api/v2/studies"

def logg(*a):
    s = " ".join(str(x) for x in a); LOGG.append(s); print(s, flush=True)

def skriv_logg():
    (HER / "extra_logg.txt").write_text(pd.Timestamp.now().strftime("%Y-%m-%d %H:%M UTC") + "\n" + "\n".join(LOGG) + "\n", encoding="utf-8")

def push(rader, titel, hog=False):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic or not rader: return
    try:
        import notis
        notis.KLICK_URL = "https://claude.ai/artifact/DvuBNP1sJaDNbmwAjifFss"
        notis.skicka([{"title": titel, "message": "\n".join(rader[:8]), "priority": 4 if hog else 3, "tags": ["microscope"]}], topic, os.environ.get("NTFY_SERVER", "https://ntfy.sh"))
    except Exception as ex:
        logg("push-fel", ex)

# ---------------------------------------------------------------- index
def index():
    import yfinance as yf
    tick = ["XBI", "^OMXSPI"]
    d = yf.download(tick, period="3y", interval="1d", auto_adjust=False, group_by="ticker", threads=False, progress=False)
    res = []
    for t in tick:
        try:
            x = d[t].dropna(subset=["Close"]).reset_index()
            x["Datum"] = pd.to_datetime(x["Date"]).dt.tz_localize(None).dt.normalize(); x["Ticker"] = t
            res.append(x[["Datum", "Ticker", "Close"]]); logg("index", t, len(x), "rader, senast", str(x.Datum.iloc[-1].date()), round(float(x.Close.iloc[-1]), 2))
        except Exception as ex:
            logg("index-fel", t, ex)
    if res:
        pd.concat(res).to_csv(HER / "bio_index.csv", index=False)
    else:
        logg("index: ingen data, behåller gammal fil")

# ---------------------------------------------------------------- studier (ClinicalTrials.gov)
SPONSOR_SOK = {  # sökord där bolagsnamnet i Yahoo inte räcker
    "SOBI.ST": "Swedish Orphan Biovitrum", "BIOG-B.ST": "BioGaia", "BIOA-B.ST": "BioArctic", "BINV.ST": "BioInvent",
    "DMYD-B.ST": "Diamyd Medical", "IRLAB-A.ST": "IRLAB Therapeutics", "CRNO-B.ST": "Cereno Scientific", "HAMLET-B.ST": "Hamlet BioPharma",
}
AKTIV = {"RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING", "SUSPENDED"}
FARA = {"TERMINATED", "SUSPENDED", "WITHDRAWN"}
FAS_SV = {"PHASE1": "Fas 1", "PHASE2": "Fas 2", "PHASE3": "Fas 3", "PHASE4": "Fas 4", "EARLY_PHASE1": "Fas 1", "NA": ""}

def sponsor_ord(t, namn):
    if t in SPONSOR_SOK: return SPONSOR_SOK[t]
    n = re.sub(r"\(publ\.?\)|\bAB\b|\bholding\b|\bgroup\b|\bltd\b|\binc\b", " ", str(namn), flags=re.I)
    n = re.sub(r"\s+", " ", n).strip()
    return n

def norm(s): return re.sub(r"[^a-z0-9]", "", str(s).lower())

def ct_datum(st, nyckel):
    d = (st.get(nyckel) or {}).get("date")
    return d or "", (st.get(nyckel) or {}).get("type", "")

def ct_hamta(term):
    """Alla studier där term är huvudsponsor. Provar först med fältlista, annars utan."""
    ut, token = [], None
    for sida in range(6):
        p = {"query.lead": term, "pageSize": 100, "format": "json", "countTotal": "true"}
        if token: p["pageToken"] = token
        r = requests.get(CT, params=p, headers={"User-Agent": UA}, timeout=40)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        j = r.json(); ut += j.get("studies", []); token = j.get("nextPageToken")
        if not token: break
        time.sleep(0.4)
    return ut

def studier():
    inf = pd.read_csv(HER / "bio_info.csv").set_index("Ticker")
    tick = (HER / "bio_tickers.txt").read_text().split()
    gamla = pd.read_csv(HER / "bio_studier.csv").fillna("") if (HER / "bio_studier.csv").exists() else pd.DataFrame()
    rader, fel = [], 0
    for t in tick:
        term = sponsor_ord(t, inf.longName.get(t, t)); nyckel = norm(term)
        try:
            st = ct_hamta(term)
        except Exception as ex:
            logg("studier-fel", t, term, str(ex)[:160]); fel += 1; continue
        n0 = len(rader)
        for s in st:
            ps = s.get("protocolSection", {}); idm = ps.get("identificationModule", {}); stm = ps.get("statusModule", {})
            sp = (ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor") or {}).get("name", "")
            if nyckel not in norm(sp): continue
            fas = "/".join(x for x in (FAS_SV.get(f, f) for f in ps.get("designModule", {}).get("phases", []) or []) if x)
            ps_d, ps_t = ct_datum(stm, "primaryCompletionDateStruct"); sl_d, _ = ct_datum(stm, "completionDateStruct"); up_d, _ = ct_datum(stm, "lastUpdatePostDateStruct")
            status = stm.get("overallStatus", "")
            if status not in AKTIV and not (up_d and pd.Timestamp(up_d if len(up_d) > 7 else up_d + "-28") >= IDAG - pd.Timedelta(days=180)): continue
            rader.append(dict(Ticker=t, NCT=idm.get("nctId", ""), Titel=(idm.get("briefTitle") or "")[:200], Fas=fas, Status=status, PrimarSlut=ps_d, PrimarSlutTyp=ps_t,
                              Slut=sl_d, Uppdaterad=up_d, HarResultat=bool(s.get("hasResults")), Sponsor=sp,
                              Tillstand="; ".join((ps.get("conditionsModule", {}).get("conditions") or [])[:3])[:120], Start=ct_datum(stm, "startDateStruct")[0],
                              Antal=((ps.get("designModule", {}).get("enrollmentInfo") or {}).get("count", ""))))
        logg("studier", t, term, "->", len(st), "träffar,", len(rader) - n0, "sparade")
        time.sleep(0.4)
    if not rader:
        logg("studier: inga rader, behåller gammal fil"); return
    ny = pd.DataFrame(rader).drop_duplicates(["Ticker", "NCT"])
    ny["Hamtad"] = str(IDAG.date())
    # ändringar mot förra körningen
    andr = []
    if len(gamla) and "NCT" in gamla.columns:
        g0 = gamla.drop_duplicates(["Ticker", "NCT"]).set_index(["Ticker", "NCT"])
        for r in ny.itertuples():
            k = (r.Ticker, r.NCT)
            if k in g0.index:
                o = g0.loc[k]
                if str(o.Status) != r.Status: andr.append(dict(Datum=str(IDAG.date()), Ticker=r.Ticker, NCT=r.NCT, Titel=r.Titel, Typ="Status", Fran=o.Status, Till=r.Status))
                if str(o.PrimarSlut) != r.PrimarSlut and r.PrimarSlut: andr.append(dict(Datum=str(IDAG.date()), Ticker=r.Ticker, NCT=r.NCT, Titel=r.Titel, Typ="Primärt slutdatum", Fran=o.PrimarSlut, Till=r.PrimarSlut))
                if (not bool(o.HarResultat) or str(o.HarResultat) == "False") and r.HarResultat: andr.append(dict(Datum=str(IDAG.date()), Ticker=r.Ticker, NCT=r.NCT, Titel=r.Titel, Typ="Resultat upplagt", Fran="", Till="ja"))
            elif r.Status in AKTIV:
                andr.append(dict(Datum=str(IDAG.date()), Ticker=r.Ticker, NCT=r.NCT, Titel=r.Titel, Typ="Ny studie", Fran="", Till=r.Status))
    # fel i en enskild bolagshämtning ska inte radera bolagets gamla rader
    if fel and len(gamla):
        saknas = set(gamla.Ticker) - set(ny.Ticker)
        if saknas: ny = pd.concat([ny, gamla[gamla.Ticker.isin(saknas)]], ignore_index=True)
    ny.sort_values(["Ticker", "Status", "PrimarSlut"]).to_csv(HER / "bio_studier.csv", index=False)
    logg("studier: sparade", len(ny), "studier för", ny.Ticker.nunique(), "bolag;", len(andr), "ändringar")
    lf = HER / "bio_studier_logg.csv"
    if andr:
        a = pd.DataFrame(andr)
        if lf.exists(): a = pd.concat([pd.read_csv(lf), a], ignore_index=True).drop_duplicates(["Datum", "Ticker", "NCT", "Typ", "Till"])
        a.to_csv(lf, index=False)
        if len(gamla):
            vikt = [x for x in andr if (x["Typ"] == "Status" and x["Till"] in FARA) or x["Typ"] == "Resultat upplagt"]
            push([f"{x['Ticker'].replace('.ST', '')}: {x['Typ']} {x['NCT']} ({x['Fran']} -> {x['Till']}) {x['Titel'][:60]}" for x in vikt], f"Biotech: {len(vikt)} studieändring" + ("ar" if len(vikt) > 1 else ""), hog=True)

# ---------------------------------------------------------------- SEC/EDGAR
EDGAR_BOLAG = [("HNSA.ST", "Hansa Biopharma")]
EDGAR_FORMER = ("F-1", "F-1/A", "424B4", "424B1", "424B3", "F-3", "S-1", "S-1/A")

def edgar():
    """Bevakar om bolagen lämnat in publika registreringsdokument hos SEC. Ett konfidentiellt utkast (DRS) syns inte; det blir synligt när F-1 lämnas in öppet."""
    f = HER / "edgar_traffar.csv"
    gamla = pd.read_csv(f).fillna("") if f.exists() else pd.DataFrame(columns=["Ticker", "Bolag", "Formular", "Datum", "Lank", "Sett"])
    sedda = set(gamla.Lank); nya = []
    for t, namn in EDGAR_BOLAG:
        traff = []
        try:
            r = requests.get("https://efts.sec.gov/LATEST/search-index", params={"q": f'"{namn}"', "forms": ",".join(EDGAR_FORMER), "dateRange": "custom", "startdt": "2026-01-01", "enddt": str(IDAG.date())},
                             headers={"User-Agent": UA, "Accept": "application/json"}, timeout=30)
            logg("edgar efts", namn, "HTTP", r.status_code)
            if r.status_code == 200:
                for h in (r.json().get("hits", {}).get("hits") or []):
                    s = h.get("_source", {}); nm = " ".join(s.get("display_names") or [])
                    if norm(namn.split()[0]) not in norm(nm): continue
                    adsh = s.get("adsh", ""); cik = (s.get("ciks") or [""])[0]
                    traff.append((s.get("form", ""), s.get("file_date", ""), f"https://www.sec.gov/Archives/edgar/data/{int(cik) if str(cik).isdigit() else cik}/{adsh.replace('-', '')}/" if adsh else ""))
        except Exception as ex:
            logg("edgar-fel", namn, str(ex)[:160])
        try:  # reserv: bolagssökning (Atom)
            r = requests.get("https://www.sec.gov/cgi-bin/browse-edgar", params={"company": namn, "owner": "include", "count": 40, "action": "getcompany", "output": "atom"},
                             headers={"User-Agent": UA}, timeout=30)
            logg("edgar browse", namn, "HTTP", r.status_code, "tecken", len(r.text))
            if r.status_code == 200:
                for m in re.finditer(r"<entry>.*?<title>(.*?)</title>.*?<link[^>]*href=\"([^\"]+)\".*?<updated>(.*?)</updated>", r.text, re.S):
                    if any(m.group(1).startswith(x) for x in EDGAR_FORMER) or "F-1" in m.group(1):
                        traff.append((m.group(1).split()[0], m.group(3)[:10], m.group(2)))
        except Exception as ex:
            logg("edgar-fel2", namn, str(ex)[:160])
        for form, dat, lank in traff:
            if lank and lank not in sedda:
                sedda.add(lank); nya.append(dict(Ticker=t, Bolag=namn, Formular=form, Datum=dat, Lank=lank, Sett=str(IDAG.date())))
        logg("edgar", namn, "->", len(traff), "träffar,", sum(1 for n in nya if n["Ticker"] == t), "nya")
    if nya or not f.exists():
        pd.concat([gamla, pd.DataFrame(nya)], ignore_index=True).to_csv(f, index=False)
    if nya: push([f"{n['Bolag']}: {n['Formular']} hos SEC {n['Datum']} {n['Lank']}" for n in nya], "SEC: ny filning", hog=True)

# ---------------------------------------------------------------- FI: finns ett hämtbart flaggningsregister?
FC_URL = "https://finanscentralen.fi.se/search/"

def fc_tabell(t, tid):
    """Rader (celltexter, länkar) ur en tabell på Finanscentralens bolagssida."""
    import html as _h
    m = re.search(r'<table[^>]*id="ctl00_main_%s"[^>]*>(.*)' % tid, t, re.S)
    if not m: return None
    kropp = m.group(1)
    nasta = re.search(r'<table[^>]*id="ctl00_main_gv', kropp)       # nästa tabell på sidan: klipp där
    if nasta: kropp = kropp[:nasta.start()]
    ut = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", kropp, re.S):
        celler = [re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", td))).strip() for td in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if not celler or not re.match(r"\d{4}-\d{2}-\d{2}", celler[0]): continue
        ut.append((celler, [_h.unescape(h) for h in re.findall(r'href="([^"]+)"', tr) if not h.lower().startswith("javascript") and h.strip() not in ("#", "")]))
    return ut

def fc_namn(longname):
    n = re.sub(r"\(.*?\)|\b(AB|publ|Holding|Group|Inc|Plc)\b\.?", " ", str(longname), flags=re.I)
    return re.sub(r"\s+", " ", n).strip()

def flaggning():
    """Flaggningsmeddelanden per bolag ur FI:s Finanscentralen (bolag på reglerad marknad med Sverige som hemland; First North-bolag saknas där).
    Tabellen anger bara när meddelandet publicerades (innehållet ligger bakom länken). Nya rader skapar en push (inte första körningen)."""
    f = HER / "bio_flaggning.csv"
    finns = f.exists()
    gamla = pd.read_csv(f).fillna("") if finns else pd.DataFrame(columns=["Ticker", "Bolag", "Publicerad", "Lank", "Sett"])
    sedda = set(zip(gamla.Ticker, gamla.Publicerad)); nya = []
    inf = pd.read_csv(HER / "bio_info.csv").set_index("Ticker")
    S = requests.Session(); S.headers.update({"User-Agent": "Mozilla/5.0 (compatible; insiderbot/1.0)", "Accept": "text/html,*/*"})
    nu = str(pd.Timestamp.now().date()); utan = []
    for t in (HER / "bio_tickers.txt").read_text().split():
        namn = fc_namn(inf.longName.get(t, t)); forsta = namn.split()[0] if namn else t
        funnet = None
        for sok in dict.fromkeys([namn, forsta]):
            try:
                h = fc_sok(S, sok)
            except Exception as ex:
                logg("flaggning-fel", t, sok, str(ex)[:100]); continue
            txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
            m0 = re.search(r"([^{};\"]{3,90}?) \( Klicka för att visa", txt)
            if m0 and fold_(forsta) in fold_(m0.group(1)):
                funnet = (h, m0.group(1).strip()); break
            time.sleep(0.6)
        if not funnet:
            utan.append(t); continue
        h, bolag = funnet
        rader = fc_tabell(h, "gvwFlaggings")
        if rader is None:
            logg("flaggning: ingen tabell", t, bolag); continue
        for celler, lank in rader:
            pub = celler[0][:16]
            if (t, pub) in sedda: continue
            sedda.add((t, pub))
            nya.append(dict(Ticker=t, Bolag=bolag, Publicerad=pub, Lank=(FC_URL + lank[0]) if lank and not lank[0].startswith("http") else (lank[0] if lank else "https://finanscentralen.fi.se/search/search.aspx"), Sett=nu))
        logg("flaggning", t, bolag, "rader", len(rader), "senast", rader[0][0][0][:16] if rader else "-", "länkar", rader[0][1][:1] if rader else "-")
        time.sleep(0.8)
    logg("flaggning: ingen träff i Finanscentralen för", ", ".join(utan) if utan else "-")
    if nya:
        alla = pd.concat([gamla, pd.DataFrame(nya)], ignore_index=True).sort_values(["Ticker", "Publicerad"], ascending=[True, False])
        alla.to_csv(f, index=False)
    if finns and nya:
        ny_ = [n for n in nya if n["Publicerad"] >= str((pd.Timestamp.now() - pd.Timedelta(days=5)).date())]
        if ny_:
            push([f"{n['Ticker'].replace('.ST', '')}: flaggningsmeddelande {n['Publicerad']}" for n in ny_], f"Biotech: {len(ny_)} ny flaggning" + ("ar" if len(ny_) > 1 else ""))

def fold_(s):
    import unicodedata
    return unicodedata.normalize("NFKD", str(s).lower()).encode("ascii", "ignore").decode()

def fc_sok(S, namn):
    """Finanscentralen (FI:s Börsinformation): söker bolag på namn, returnerar svarets HTML. Bara bolag på reglerad marknad med Sverige som hemland finns."""
    import html as _h
    g = S.get("https://finanscentralen.fi.se/search/search.aspx", timeout=30)
    dolda = {m[0]: _h.unescape(m[1]) for m in re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', g.text)}
    dolda.update({"ctl00$main$txtCompanyName": namn, "ctl00$main$txtOrganizationNumber": "", "ctl00$main$txtOrganizationShortName": "", "ctl00$main$btnSearch": "Sök"})
    return S.post("https://finanscentralen.fi.se/search/search.aspx", data=dolda, timeout=40).text

def probe():
    """Utforskar Finanscentralens bolagssida (flaggningar, kurspåverkande information). Skriver inga datafiler."""
    from io import StringIO as _SIO
    H = {"User-Agent": "Mozilla/5.0 (compatible; insiderbot/1.0)", "Accept": "text/html,*/*"}
    S = requests.Session(); S.headers.update(H)
    for namn in ("Hansa Biopharma", "Oncopeptides", "Camurus", "Cinclus Pharma", "Calliditas"):
        try:
            t = fc_sok(S, namn)
            txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))
            logg("fc", namn, "tecken", len(t), "bolagssida" if "Följande information finns" in txt else "ej bolagssida", "listrader", len(re.findall(r"gvwCompan|gvCompan|lnkCompany", t)))
            if "Följande information finns" not in txt:
                logg("  text", txt[-500:])
                for m in sorted(set(re.findall(r'<(?:table|a)[^>]*(?:id|href)="([^"]*(?:Compan|Select)[^"]*)"', t)))[:10]: logg("  id", m[:140])
                continue
            m0 = re.search(r"\n?([^<>]{3,60}) \( Klicka för att visa", txt); logg("  bolag", m0.group(1) if m0 else "-")
            for tid in ("gvwFlaggings", "gvStockAffect"):
                try:
                    tb = pd.read_html(_SIO(t), attrs={"id": "ctl00_main_" + tid})
                    x = tb[0]
                    logg("  tabell", tid, list(x.columns), len(x))
                    logg(x.head(6).to_string()[:1800])
                except Exception as ex:
                    logg("  ingen tabell", tid, str(ex)[:100])
        except Exception as ex:
            logg("fc-fel", namn, str(ex)[:160])

def main():
    val = [a for a in sys.argv[1:] if not a.startswith("-")] or ["index", "studier", "edgar", "flaggning"]
    for namn, fn in (("index", index), ("studier", studier), ("edgar", edgar), ("flaggning", flaggning), ("probe", probe)):
        if namn not in val: continue
        try: fn()
        except Exception:
            logg("FEL i", namn); logg(traceback.format_exc()[-900:])
    skriv_logg()

if __name__ == "__main__":
    main()
