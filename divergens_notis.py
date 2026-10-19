"""Notiser från Divergensscreen. Jämför divergens_status.json med divergens_state.json.

  python divergens_notis.py --lage prel        # under dagen: förvarning
  python divergens_notis.py --lage bekraftat   # efter stängning: bekräftat köpläge
  lägg till --prov för att bara skriva ut

Förvarning (under dagen): en aktie får en ny köpsignal om dagens kurs står sig till stängningen.
  Högst en förvarning per aktie och dag.
Köpläge (efter stängning): signalen är bekräftad på stängningskursen. En notis per signal, med köp, stopp, mål och antal.
Inget skickas annars. Notisen länkar till screenen (artefakten).
"""
import argparse, json, os, subprocess, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LANK = os.environ.get("DIVERGENS_LANK", "https://claude.ai/artifact/V5DoqLGTqMiLmyFWFMFe88")
STATE = Path("divergens_state.json")

ap = argparse.ArgumentParser()
ap.add_argument("--lage", choices=["prel", "bekraftat"], required=True)
ap.add_argument("--prov", action="store_true")
a = ap.parse_args()

idag = datetime.now(ZoneInfo("Europe/Stockholm")).strftime("%Y-%m-%d")
status = json.load(open("divergens_status.json", encoding="utf-8"))
st = json.load(open(STATE, encoding="utf-8")) if STATE.exists() else {}
if st.get("datum") != idag:
    st["datum"], st["forvarnat"] = idag, []
st.setdefault("kopnotis", {})

f = lambda x: f"{x:,.2f}".replace(",", " ").replace(".", ",")
kort = lambda t: t.rsplit(".", 1)[0]


def rad(t, v):
    n = v["niv"]; val = "" if v.get("valuta", "SEK") == "SEK" else f" {v['valuta']}"
    return (f"{v['namn']} ({kort(t)}): köp {f(n['kop'])}{val}, stopp {f(n['stopp'])}, mål {f(n['m2'])} / {f(n['m3'])}, "
            f"{n['antal']} st (risk 1 000 kr)")


nya = []
for t, v in status.items():
    n = v.get("niv")
    if v["status"] != "Köpläge" or not n:
        continue
    if a.lage == "prel":
        if n["signaldag"] == idag and t not in st["forvarnat"]:
            nya.append((t, v)); st["forvarnat"].append(t)
    else:
        if st["kopnotis"].get(t) != n["signaldag"]:
            nya.append((t, v)); st["kopnotis"][t] = n["signaldag"]

# rensa gamla signaler (äldre än 30 dagar) ur minnet
st["kopnotis"] = {t: d for t, d in st["kopnotis"].items() if d >= (datetime.now() - __import__("datetime").timedelta(days=30)).strftime("%Y-%m-%d")}

if not nya:
    print("Inga nya notiser.")
else:
    if a.lage == "prel":
        titel = "Förvarning: " + ", ".join(v["namn"] for _, v in nya)
        text = ("Kan bli köpläge vid stängningen om kursen står sig (preliminärt, kurser med ca 15 min fördröjning).\n"
                + "\n".join(rad(t, v) for t, v in nya))
        prio = 3
    else:
        titel = "Köpläge: " + ", ".join(v["namn"] for _, v in nya)
        text = "Bekräftat på stängningskursen.\n" + "\n".join(rad(t, v) for t, v in nya)
        prio = 4
    print(titel); print(text)
    if not a.prov:
        r = subprocess.run([sys.executable, "notis.py", "--sida", text, "--titel", titel, "--lank", LANK, "--prio", str(prio)])
        if r.returncode:
            sys.exit(r.returncode)
if not a.prov:
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
