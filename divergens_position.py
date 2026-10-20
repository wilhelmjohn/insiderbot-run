"""Lägg till eller stäng en egen position i Divergensscreen (divergens_positioner.json i datarepot).

  python divergens_position.py lagg TICKER ANTAL KURS [--datum ÅÅÅÅ-MM-DD] [--stopp PRIS]
  python divergens_position.py stang TICKER
  python divergens_position.py lista

Stoppen tas från screenens signal (1 % under bottnen) om --stopp inte anges. Mål 2R och 3R räknas från din köpkurs:
R = köpkurs - stopp. Säljnotiserna skickas av divergens_notis.py."""
import json, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PF = Path("divergens_positioner.json")
pf = json.load(open(PF, encoding="utf-8")) if PF.exists() else {"positioner": []}
a = sys.argv[1:]
opt = lambda k: a[a.index(k) + 1] if k in a else None
if not a or a[0] == "lista":
    for p in pf["positioner"]:
        print(("STÄNGD " if p.get("stangd") else "ÖPPEN  ") + f"{p['ticker']} {p['antal']} st à {p['kurs']} ({p['datum']}), stopp {p['stopp']}, notiser {p.get('notis', {})}")
    sys.exit()
if a[0] == "lagg":
    t, antal, kurs = a[1], int(a[2]), float(a[3].replace(",", "."))
    datum = opt("--datum") or datetime.now(ZoneInfo("Europe/Stockholm")).strftime("%Y-%m-%d")
    stopp = opt("--stopp")
    if stopp is None:
        st = json.load(open("divergens_status.json", encoding="utf-8")).get(t, {})
        src = st.get("niv") or st.get("imorgon")
        if not src: sys.exit(f"Ingen signal för {t} i divergens_status.json; ange --stopp.")
        stopp = src["stopp"]
    stopp = float(str(stopp).replace(",", "."))
    if not stopp < kurs: sys.exit("Stoppen måste ligga under köpkursen.")
    R = kurs - stopp
    pf["positioner"].append(dict(ticker=t, antal=antal, kurs=kurs, datum=datum, stopp=round(stopp, 4), notis={}))
    print(f"{t}: {antal} st à {kurs}, stopp {stopp:.2f} (risk {100*R/kurs:.1f} %), mål 2R {kurs+2*R:.2f}, 3R {kurs+3*R:.2f}")
elif a[0] == "stang":
    for p in pf["positioner"]:
        if p["ticker"] == a[1] and not p.get("stangd"):
            p["stangd"] = datetime.now(ZoneInfo("Europe/Stockholm")).strftime("%Y-%m-%d"); print(f"Stängde {a[1]}")
json.dump(pf, open(PF, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
