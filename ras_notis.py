"""Notiser från Rasbevakning (papperstest, ingen handel). Läser DATA i ras.html efter ras_bygg.py.

  python ras_notis.py [ras.html] [--prov]      (--prov skriver bara ut)

Kan blankas i morgon (prio 3): ett ras där i morgon är sista väntedagen (dag 3), utan insynsköp, och som just nu
  följer villkoren (under rasdagens stängning och sämre än index sedan raset). En notis per ras.
Blankad på papper (prio 4): en ny signal i facit med signaldag = senaste kursdag. En notis per signal.
Vad som redan skickats sparas i ras_notis_state.json. Utan NTFY_TOPIC skickas inget (notis.py avbryter tyst).
"""
import argparse, json, os, re, subprocess, sys
from pathlib import Path

LANK = "https://claude.ai/artifact/9u7xvSdRFgmmofUkYTNhBk"
STATE = Path("ras_notis_state.json")

ap = argparse.ArgumentParser()
ap.add_argument("html", nargs="?", default="ras.html")
ap.add_argument("--prov", action="store_true")
a = ap.parse_args()

D = json.loads(re.search(r"const DATA=(\{.*?\});\n", Path(a.html).read_text(encoding="utf-8")).group(1))
M = D["meta"]; V = M.get("vanta", 3); RK = M.get("risk_kr", 500)
skickat = set(json.loads(STATE.read_text())) if STATE.exists() else set()
tk = lambda t: t.replace(".ST", "")
nf = lambda x: f"{x:,.2f}".replace(",", " ").replace(".", ",")
pc = lambda x: f"{x:+.1f} %".replace(".", ",").replace("-", "−")
notiser = []

for r in D["vantar"]:
    if r.get("kvar") != 1 or r.get("insyn") or r.get("just_nu") != "Följer villkoren": continue
    nid = f"morgon|{r['t']}|{r['rasdag']}"
    c = [x for x in r["ch"]["c"][r["ch"]["ras"]:] if x is not None]
    stopp = max(c) * 1.01; risk = (stopp - r["kurs"]) / r["kurs"]
    text = (f"Ras {r['rasdag']}: {pc(r['fall'])}. Blankas på papper i morgon (dag {V}) om stängningen ligger under "
            f"{nf(r['rasstang'])} och aktien fortsatt går sämre än index. Nu {nf(r['kurs'])}, {pc(r['sedan'])} sedan raset, "
            f"{pc(r['rel'])} mot index. Stopp blir minst {nf(stopp)} (risk nu {f"{risk * 100:.1f}".replace(".", ",")} %; över 8 % blankas den inte).")
    notiser.append((nid, f"Ras: {r['namn']} ({tk(r['t'])}) kan blankas i morgon", text, 3))

for r in D["facit"]:
    if r["signaldag"] != M["kursdag"]: continue
    nid = f"signal|{r['t']}|{r['signaldag']}"
    antal = f"{r['antal']} st, " if r.get("antal") else ""
    text = (f"Blankad på papper till {nf(r['ingang'])} (ras {r['rasdag']}). Stopp {nf(r['stopp0'])}, 2R {nf(r['m2'])} "
            f"(köp tillbaka halva, stopp till ingången), 3R {nf(r['m3'])}, annars ut efter 10 handelsdagar. {antal}{RK} kr risk. "
            "Papperstest, ingen handel.")
    notiser.append((nid, f"Ras: {r['namn']} ({tk(r['t'])}) blankad på papper", text, 4))

nya = [n for n in notiser if n[0] not in skickat]
for nid, titel, text, prio in nya:
    print(f"[{prio}] {titel}\n    {text}")
    if a.prov or not os.environ.get("NTFY_TOPIC", "").strip(): continue   # utan ämne: inget skickas eller sparas
    res = subprocess.run([sys.executable, "notis.py", "--sida", text, "--titel", titel, "--lank", LANK, "--knapp", "Öppna Rasbevakning", "--prio", str(prio)])
    if res.returncode == 0: skickat.add(nid)
if not nya: print("Rasnotiser: inget nytt.")
if not a.prov:
    STATE.write_text(json.dumps(sorted(skickat), ensure_ascii=False, indent=0))
