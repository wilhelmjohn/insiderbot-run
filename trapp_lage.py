"""Jämför varje akties läge (Köpläge/Neutral/Avvakta) mot gårdagens och skickar en samlad push vid ändring.
Körs i Actions efter kursuppdateringen. Läget sparas i trapp_lage.json. Första körningen skickar inget.
Användning: python trapp_lage.py [--prov]   (kräver NTFY_TOPIC för att skicka)"""
import json, os, sys
from pathlib import Path
import pandas as pd
import trapp_bygg as tb
import notis

HER = Path(__file__).resolve().parent
FIL = HER / "trapp_lage.json"
NAMN = {"a": "Avvakta", "k": "Köpläge", "n": "Neutral"}
URL = "https://claude.ai/artifact/Mis8w1jwr9hQm8CyFGGUHt"

def lage(z):
    if any(w["sev"] == "hard" for w in z["ned"]): return "a"
    return "k" if any(u["l"].startswith("Dopp") for u in z["upp"]) else "n"

def orsak(z, nytt):
    if nytt == "a": return ", ".join(w["l"] for w in z["ned"] if w["sev"] == "hard")
    if nytt == "k": return next(u["l"] for u in z["upp"] if u["l"].startswith("Dopp"))
    return ""

def main():
    prov = "--prov" in sys.argv
    o = pd.read_csv(HER / "volscreen_ohlc.csv", parse_dates=["Datum"]).sort_values(["Ticker", "Datum"])
    inf = pd.read_csv(HER / "volscreen_info.csv", parse_dates=["Nasta_rapport"]).set_index("Ticker")
    tm, bl, h, ik = tb.las_stod()
    out = tb.analysera(o, inf, tm, bl, h, ik)
    nu = {z["t"]: lage(z) for z in out}
    datum = str(o.Datum.max().date())
    fore = json.loads(FIL.read_text()) if FIL.exists() else None
    if fore is not None and fore.get("datum") == datum:
        fore_l = fore["lage"]  # samma handelsdag: jämför mot förra körningens läge
        if fore.get("jamf"): fore_l = fore["jamf"]
    else:
        fore_l = fore["lage"] if fore else None
    egna = set((HER / "trapp_fasta.txt").read_text().split()) if (HER / "trapp_fasta.txt").exists() else set()
    rader = []
    if fore_l is not None:
        for z in out:
            t = z["t"]; ny = nu[t]; gammal = fore_l.get(t)
            if gammal is None or gammal == ny: continue
            nm = z["namn"].replace(" AB", "").replace(" (publ)", "")
            o_ = orsak(z, ny)
            rader.append((t in egna, ny, f"{nm}: {NAMN[gammal]} → {NAMN[ny]}" + (f" ({o_})" if o_ else "")))
    rader.sort(key=lambda r: (not r[0], r[1] != "a", r[2]))
    # spara läget; vid samma datum behålls jämförelsebasen så att flera körningar samma kväll inte ger dubbla notiser
    jamf = fore_l if (fore and fore.get("datum") == datum) else fore["lage"] if fore else None
    if not prov:
        FIL.write_text(json.dumps(dict(datum=datum, lage=nu, jamf=jamf), ensure_ascii=False, indent=0))
    if not rader:
        print(f"{datum}: inga lägesändringar ({len(nu)} aktier)"); return
    mina = [r for r in rader if r[0]]
    text = "\n".join(("● " if r[0] else "") + r[2] for r in rader[:14]) + (f"\n+{len(rader) - 14} till" if len(rader) > 14 else "")
    titel = f"Trappscreen: {len(rader)} lägesändring" + ("ar" if len(rader) > 1 else "") + (f", {len(mina)} i dina aktier" if mina else "")
    print(titel); print(text)
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if prov or not topic: return
    notis.KLICK_URL = URL
    notis.skicka([{"title": titel, "message": text, "priority": 4 if any(r[0] and r[1] == "a" for r in rader) else 3, "tags": ["chart_with_upwards_trend"]}], topic, os.environ.get("NTFY_SERVER", "https://ntfy.sh"))

if __name__ == "__main__":
    main()
