#!/usr/bin/env python3
"""
Reservkälla för bevakningslistan: pressmeddelanden via MFN/Cision för bolag vars nyheter inte finns i Nasdaq-datan
(Spotlight, NGM, utländska noteringar) och för återköp, som Nasdaq-datan medvetet inte sparar.

Körs i Actions (sandboxen når inte MFN/Cision). Sökningen av bolagets flöde följer bio_pm.py men kontrollerar att
bolagsnamnet finns i flödet, så att en felaktig adress inte ger ett annat bolags nyheter. Fungerande flöde sparas i
bev_mfn_kalla.csv, bolag utan flöde provas om var 14:e dag.

Utdata:
  nyheter_mfn.csv   Publicerat, Bolag, Bolag_id, Marknad, Kategori, Rubrik, Typ, Sprak, Ticker, Lank, Kalla
                    (Bolag_id = "t:"+ticker, samma nyckel som bevakning.py använder; Typ som nyheter.py, plus "aterkop")
  bev_mfn_kalla.csv Ticker, Namn, Typ, Slug, Url, Kontrollerad, Antal
  bev_mfn_logg.txt  vad som hittades per bolag

Användning: python bev_mfn.py [--prov] [--max-minuter N]
"""
import csv
import re
import sys
import time
import unicodedata
from pathlib import Path

import pandas as pd

import bio_pm as pm
import nyheter as ny

HER = Path(__file__).resolve().parent
UT = HER / "nyheter_mfn.csv"
KALLA = HER / "bev_mfn_kalla.csv"
LOGG = HER / "bev_mfn_logg.txt"
KOL = ["Publicerat", "Bolag", "Bolag_id", "Marknad", "Kategori", "Rubrik", "Typ", "Sprak", "Ticker", "Lank", "Kalla"]
DAGAR = 400
PROVA_OM = 14
PROV = "--prov" in sys.argv

# återköp av egna aktier. Incitamentsprogram (C-aktier, teckningsoptioner) och bemyndiganden på stämma räknas inte.
AATERKOP = re.compile(r"återköp av (egna )?aktier|återköp av [^.;]{0,40}aktier|återköp av serie|återköpsprogram|aktieåterköp|"
                      r"share buy-?backs?|buy-?backs? of (own |the )?(company's )?shares|buy-?backs? (of|in) [A-Z]|share repurchase|repurchase of (own )?shares|"
                      r"buybacks? (of )?shares", re.I)
EJ_AATERKOP = re.compile(r"c-aktier|class c|incentiv|incitament|teckningsopt|warrant|bemyndiga\w* styrelsen|authori[sz]\w* the board|authori[sz]ation to|kallelse|notice|årsstämma|annual general|"
                         r"förslag (till|om)|proposal|antal aktier och röster|number of shares and votes|"
                         r"obligation|\bbonds?\b|\bnotes?\b|skuldebrev|konvertibel|convertible|inlösen|återköpserbjudande|repurchase offer|"
                         r"regler|\brules\b|lagändring|nya regler", re.I)     # skuldåterköp är inte aktieåterköp
ENGELSKA = re.compile(r"\b(the|of|announces?|to|and|for|with|has|its)\b", re.I)


def fold(s):
    return unicodedata.normalize("NFKD", str(s).lower()).encode("ascii", "ignore").decode()


def nyckelord(namn):
    """Orden som måste synas i flödet: första ordet, eller de två första om första är kort (t.ex. 'AJ Bell')."""
    n = re.sub(r"\(.*?\)|\b(ab|plc|ag|inc|publ|holding|group|aktiebolag)\b", " ", str(namn), flags=re.I)
    ord_ = [w for w in re.findall(r"[a-z0-9]+", fold(n))]
    if not ord_:
        return []
    return ord_[:1] if len(ord_[0]) >= 4 else ord_[:2]


def stammer(text, items, namn):
    """Namnet ska finnas som hela ord i flödets text eller rubriker."""
    key = nyckelord(namn)
    if not key:
        return False
    tok = re.findall(r"[a-z0-9]+", fold((text or "") + " " + " ".join(i[1] + " " + i[3] for i in items)))
    if len(key) == 1:
        return key[0] in set(tok)
    return any(tok[i:i + len(key)] == key for i in range(len(tok) - len(key) + 1))


def urls(typ, slug):
    if typ == "cision":
        return [f"https://news.cision.com/se/{slug}?format=rss", f"https://news.cision.com/{slug}?format=rss"]
    if typ == "mfn":
        return [f"https://mfn.se/all/a/{slug}?format=rss", f"https://mfn.se/all/a/{slug}/rss", f"https://mfn.se/a/{slug}/rss"]
    return [f"https://mfn.se/all/a/{slug}"]


def hamta_flode(typ, url, slug):
    text = pm.hamta(url, 12)
    return text, (pm.lista_html(text, slug) if typ == "mfn-html" else pm.rss(text, typ))


def sok(namn):
    """Letar upp bolagets flöde. Returnerar (typ, slug, url, items) eller None."""
    for slug in pm.slugar(namn)[:5]:
        for typ in ("cision", "mfn", "mfn-html"):
            for url in urls(typ, slug):
                text, items = hamta_flode(typ, url, slug)
                if items and stammer(text, items, namn):
                    return typ, slug, url, items
                if typ == "mfn-html":
                    break
    return None


def prioritet(bev):
    """Ordning: bolag utan nyheter i Nasdaq-datan först (där MFN är enda källan), därefter resten."""
    try:
        tm = pd.read_csv(HER / "ticker_map.csv", keep_default_na=False, dtype=str)
        tm = tm[tm.Status.isin(["ok", "manuell"]) & (tm.Ticker != "")]
        id_tick = dict(zip(tm.Bolag_id, tm.Ticker))
        har = set(pd.read_csv(HER / "nyheter.csv", usecols=["Bolag_id"], dtype=str).Bolag_id.map(id_tick).dropna())
        return list(bev.assign(_h=bev.Ticker.isin(har)).sort_values("_h", kind="stable").index)
    except Exception:
        return list(range(len(bev)))


def klassa(rubrik, ing="", publicerat=None):
    """Typ som nyheter.py, plus 'aterkop' (egna aktier) och 'rapport_datum' (bolaget anger när en rapport kommer; är inte själva rapporten).
    'brus' returneras för sådant som inte ska sparas."""
    if publicerat is not None and pd.notna(publicerat) and ny.rapportdatum_i_rubrik(rubrik, publicerat) is not None:
        return "rapport_datum"
    if AATERKOP.search(rubrik) and not EJ_AATERKOP.search(rubrik + " " + ing[:200]):
        return "aterkop"
    return ny.klassa(rubrik)


def main():
    t0 = time.time()
    maxmin = 0.0
    if "--max-minuter" in sys.argv:
        maxmin = float(sys.argv[sys.argv.index("--max-minuter") + 1])
    bev = pd.read_csv(HER / "bevakning.csv")[["Bolag", "Ticker"]].drop_duplicates("Ticker")
    bev = bev.iloc[prioritet(bev)].reset_index(drop=True)
    nu = pd.Timestamp.now().normalize()
    kal = pd.read_csv(KALLA, dtype=str).fillna("") if KALLA.exists() else pd.DataFrame(columns=["Ticker", "Namn", "Typ", "Slug", "Url", "Kontrollerad", "Antal"])
    kal = kal.set_index("Ticker", drop=False)
    gamla = pd.read_csv(UT, dtype=str).fillna("") if UT.exists() else pd.DataFrame(columns=KOL)
    sedda = set(gamla.Lank)
    nya, logg = [], []
    for r in bev.itertuples():
        if maxmin and (time.time() - t0) / 60 > maxmin:
            logg.append(f"{r.Ticker}: hoppad (tidsgräns)")
            continue
        namn, tk = str(r.Bolag), r.Ticker
        k = kal.loc[tk] if tk in kal.index else None
        items, typ, slug, url = [], "", "", ""
        if k is not None and k["Typ"]:
            typ, slug, url = k["Typ"], k["Slug"], k["Url"]
            _, items = hamta_flode(typ, url, slug)
            if not items:            # flödet har flyttats eller upphört: sök om
                k = None
        if k is None or not typ:
            sen = pd.Timestamp(k["Kontrollerad"]) if k is not None and k["Kontrollerad"] else None
            if k is not None and not k["Typ"] and sen is not None and (nu - sen).days < PROVA_OM:
                logg.append(f"{tk}: inget flöde (provas igen efter {PROVA_OM} d)")
                continue
            f = sok(namn)
            if f:
                typ, slug, url, items = f
        if items:
            kal.loc[tk] = dict(Ticker=tk, Namn=namn, Typ=typ, Slug=slug, Url=url, Kontrollerad=str(nu.date()), Antal=str(len(items)))
            n_ny = 0
            for d, titel, lank, ing in items:
                if lank in sedda or pd.isna(d):
                    continue
                kl = klassa(titel, ing, d)
                if kl == "brus":
                    continue
                nya.append({"Publicerat": d.strftime("%Y-%m-%d %H:%M:%S"), "Bolag": namn, "Bolag_id": "t:" + tk, "Marknad": "MFN/Cision", "Kategori": "",
                            "Rubrik": titel, "Typ": kl, "Sprak": "en" if ENGELSKA.search(titel) else "sv", "Ticker": tk, "Lank": lank, "Kalla": typ})
                sedda.add(lank); n_ny += 1
            logg.append(f"{tk}: {typ}:{slug} {len(items)} i flödet, {n_ny} nya sparade")
        else:
            kal.loc[tk] = dict(Ticker=tk, Namn=namn, Typ="", Slug="", Url="", Kontrollerad=str(nu.date()), Antal="0")
            logg.append(f"{tk}: INGET FLÖDE ({namn})")
        time.sleep(0.4)
    alla = pd.concat([gamla, pd.DataFrame(nya, columns=KOL)], ignore_index=True)
    alla["Publicerat"] = pd.to_datetime(alla["Publicerat"], errors="coerce")
    # klassa om det som redan är sparat, så att ändrade regler gäller även äldre rader (ingressen finns inte sparad)
    alla["Typ"] = [klassa(r, "", d) for r, d in zip(alla.Rubrik, alla.Publicerat)]
    alla = alla[alla.Typ != "brus"]
    alla = alla[alla.Publicerat >= nu - pd.Timedelta(days=DAGAR)].sort_values("Publicerat", ascending=False)
    alla["Publicerat"] = alla.Publicerat.dt.strftime("%Y-%m-%d %H:%M:%S")
    sv = f"{len(nya)} nya, {len(alla)} totalt; {(alla.Typ == 'aterkop').sum()} återköp, {(alla.Typ == 'rapport').sum()} rapporter; " \
         f"{int((kal.Typ == '').sum())} av {len(kal)} bolag utan flöde"
    print(sv)
    for s in logg:
        print(" ", s)
    if PROV:
        return
    alla.to_csv(UT, index=False, quoting=csv.QUOTE_NONNUMERIC)
    kal.to_csv(KALLA, index=False)
    LOGG.write_text(pd.Timestamp.now().strftime("%Y-%m-%d %H:%M UTC") + "\n" + sv + "\n" + "\n".join(logg) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
