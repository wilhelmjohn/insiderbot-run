"""Notiser från Divergensscreen. Jämför divergens_status.json med divergens_state.json.

  python divergens_notis.py --lage prel        # under dagen: förvarning
  python divergens_notis.py --lage bekraftat   # efter stängning: bekräftat köpläge
  lägg till --prov för att bara skriva ut

Förvarning (under dagen): en aktie får en ny köpsignal om dagens kurs står sig till stängningen.
  Högst en förvarning per aktie och dag.
Köpläge (efter stängning): signalen är bekräftad på stängningskursen. En notis per signal, med köp, stopp, mål och antal.
I morgon kanske (efter stängning, låg prioritet): en av två stängningar över en ny botten är klar, så aktien blir köpläge
  om nästa dag också stänger över bottnen. En notis per botten.
Sälj (mina positioner i divergens_positioner.json, prio 5): stoppen nådd (kolla att ordern gått), målet 2R (sälj halva,
  flytta stopp till köpkurs), målet 3R (sälj resten), 10 handelsdagar (sälj resten, bara efter stängning). En notis per händelse.
Bevakning (alla som Närmar sig plus aktier i divergens_bevaka.json, prio 3): ändrad status, stochastic blir översåld eller ny lägsta stängning
  på 20 dagar, jämfört med förra stängningen. Under dagen (preliminärt) och efter stängning; samma händelse högst en gång
  per dag. "Kan bli köpläge i morgon" bara efter stängning.
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
POS = status.pop("_positioner", [])
BEV = status.pop("_bevakning", {})
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

# kvällens heads-up: aktier som kan bli köpläge i morgon (en av två stängningar över bottnen klar)
st.setdefault("imorgon", {})
snart = []
if a.lage == "bekraftat":
    for t, v in status.items():
        m = v.get("imorgon")
        if v["status"] == "Närmar sig" and m and st["imorgon"].get(t) != m["bottendag"]:
            snart.append((t, v)); st["imorgon"][t] = m["bottendag"]
    st["imorgon"] = {t: d for t, d in st["imorgon"].items() if d >= (datetime.now() - __import__("datetime").timedelta(days=30)).strftime("%Y-%m-%d")}
if snart:
    def rad2(t, v):
        m = v["imorgon"]; val = "" if v.get("valuta", "SEK") == "SEK" else f" {v['valuta']}"
        return (f"{v['namn']} ({kort(t)}): stängning över {f(m['botten'])}{val} i morgon ger köpläge. "
                f"Stopp {f(m['stopp'])}, ca {m['antal']} st vid dagens kurs")
    titel2 = "I morgon kanske: " + ", ".join(v["namn"] for _, v in snart)
    text2 = "En av två stängningar över bottnen är klar.\n" + "\n".join(rad2(t, v) for t, v in snart)
    print(titel2); print(text2)
    if not a.prov:
        r = subprocess.run([sys.executable, "notis.py", "--sida", text2, "--titel", titel2, "--lank", LANK, "--prio", "2"])
        if r.returncode:
            sys.exit(r.returncode)

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
# ---------- extra bevakning: notis vid ändrat läge (under dagen preliminärt, efter stängning bekräftat) ----------
# Jämförs mot läget vid förra stängningen (st["bevakning"], uppdateras bara efter stängning).
# Samma händelse skickas högst en gång per dag och aktie (st["bevakning_dag"]).
if BEV:
    forra = st.get("bevakning", {}); rader_b = []
    dag = st.get("bevakning_dag", {})
    if dag.get("datum") != idag: dag = {"datum": idag, "skickat": {}}
    for t, b in BEV.items():
        f0 = forra.get(t) or dict(b.get("igar", {}), _ny=True)   # ny i bevakningen: jämför med gårdagens stängning
        val = "" if t.endswith(".ST") else (" NOK" if t.endswith(".OL") else " DKK")
        bits = []
        if not f0.get("_ny") and b["status"] != f0.get("status"): bits.append(("status:" + b["status"], f"status {f0.get('status')} → {b['status']}"))
        if b["K"] < 25 and not f0.get("oversald"): bits.append(("oversald", f"stochastic översåld ({b['K']:.0f})"))
        if b["ny_botten"] and not f0.get("ny_botten"): bits.append(("ny_botten", f"ny lägsta stängning på 20 dagar (under {f(b['lagsta20'])})"))
        if a.lage == "bekraftat" and b.get("imorgon") and not f0.get("imorgon"):
            bits.append(("imorgon", f"kan bli köpläge i morgon vid stängning över {f(b['imorgon']['botten'])}"))
        sk = dag["skickat"].setdefault(t, [])
        bits = [(k, txt) for k, txt in bits if k not in sk]
        if bits:
            sk += [k for k, _ in bits]
            rader_b.append(f"{b['namn']} ({kort(t)}) {f(b['kurs'])}{val}: " + "; ".join(txt for _, txt in bits) + f". RSI {b['rsi']:.0f}.")
        if a.lage == "bekraftat":
            st.setdefault("bevakning", {})[t] = dict(status=b["status"], oversald=b["K"] < 25, ny_botten=b["ny_botten"], imorgon=bool(b.get("imorgon")))
    st["bevakning_dag"] = dag
    if rader_b:
        titel4 = ("Bevakning (prel): " if a.lage == "prel" else "Bevakning: ") + ", ".join(x.split(" (")[0] for x in rader_b)
        text4 = ("Under dagen, kurser med ca 15 min fördröjning. Bekräftas efter stängning.\n" if a.lage == "prel" else "") + "\n".join(rader_b)
        print(titel4); print(text4)
        if not a.prov:
            r = subprocess.run([sys.executable, "notis.py", "--sida", text4, "--titel", titel4, "--lank", LANK, "--prio", "3"])
            if r.returncode: sys.exit(r.returncode)

# ---------- säljnotiser för mina positioner ----------
PF = Path("divergens_positioner.json")
if POS and PF.exists():
    pf = json.load(open(PF, encoding="utf-8")); byt = {(x["ticker"], x["datum"]): x for x in pf.get("positioner", [])}
    salj = []
    for p_ in POS:
        x = byt.get((p_["ticker"], p_["datum"]))
        if not x: continue
        nt = x.setdefault("notis", {}); val = "" if p_["valuta"] == "SEK" else f" {p_['valuta']}"
        namn_ = f"{p_['namn']} ({kort(p_['ticker'])})"
        tag = " (preliminärt)" if a.lage == "prel" else ""
        if p_["nu"] <= p_["stopp"] and not nt.get("stopp"):
            nt["stopp"] = idag; x["stangd"] = idag
            salj.append(f"{namn_}: kursen {f(p_['nu'])}{val} har nått stoppen {f(p_['stopp'])}{tag}. Kontrollera att stop loss-ordern har gått, annars sälj {p_['antal_kvar']} st.")
        elif not nt.get("m2") and p_["nu"] >= p_["m2"]:
            nt["m2"] = idag
            salj.append(f"{namn_}: målet 2R {f(p_['m2'])}{val} är nått{tag}. Sälj halva ({p_['antal'] // 2} st) och flytta stop loss till köpkursen {f(p_['kurs'])}.")
        elif nt.get("m2") and not nt.get("m3") and p_["nu"] >= p_["m3"]:
            nt["m3"] = idag; x["stangd"] = idag
            salj.append(f"{namn_}: målet 3R {f(p_['m3'])}{val} är nått{tag}. Sälj resten ({p_['antal_kvar']} st).")
        elif a.lage == "bekraftat" and p_["dagar"] >= 10 and not nt.get("tid"):
            nt["tid"] = idag; x["stangd"] = idag
            salj.append(f"{namn_}: 10 handelsdagar har gått sedan köpet. Sälj resten ({p_['antal_kvar']} st), kurs nu {f(p_['nu'])}{val}.")
    if salj:
        titel3 = "Sälj: " + ", ".join(s_.split(" (")[0] for s_ in salj)
        text3 = "\n".join(salj)
        print(titel3); print(text3)
        if not a.prov:
            r = subprocess.run([sys.executable, "notis.py", "--sida", text3, "--titel", titel3, "--lank", LANK, "--prio", "5"])
            if r.returncode: sys.exit(r.returncode)
            json.dump(pf, open(PF, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

if not a.prov:
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
