#!/usr/bin/env python3
"""
Intradagsbevakning: hämtar dagens publicerade insynshandel från FI, och om något nytt
kommit in analyseras det direkt och nya fall skickas som notis (med teknisk analys och diagram).

Körs av .github/workflows/intradag.yml under börsens öppettider. Dagens rader läggs till i
insiderhandel.csv; den dagliga körningen hämtar om hela dagen när den är klar (och tar då bort
dagens delrader först), så det blir aldrig dubbletter.

  python intradag.py            # hoppar över om börsen är stängd
  python intradag.py --tvinga   # kör oavsett klockslag (test)
  python intradag.py --prov     # skicka inga notiser
"""
import argparse
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

import bevakning
import checkpoint
import insider_bot as ib
import notis

MAPP = Path(__file__).resolve().parent
OPEN = (8, 55)      # lokal tid
STANGT = (17, 45)


def borsen_oppen(nu: datetime) -> bool:
    if nu.weekday() >= 5:
        return False
    return OPEN <= (nu.hour, nu.minute) <= STANGT


def nya_rader(idag: str, hamtat: pd.DataFrame, befintligt: pd.DataFrame) -> pd.DataFrame:
    """Rader i `hamtat` som inte redan finns bland dagens rader i `befintligt`."""
    if hamtat.empty:
        return hamtat
    nyckel = [k for k in ib.KOLUMNER if k != "Publiceringsdatum"]
    idag_rader = befintligt[befintligt["Publiceringsdatum"] == idag]

    def n(df):
        d = df[nyckel].copy()
        for c in ("Volym", "Pris"):
            d[c] = pd.to_numeric(d[c], errors="coerce").round(4)
        d = d.astype(object).where(d.notna(), "")
        return pd.Series(["|".join(map(str, t)) for t in d.itertuples(index=False)], index=d.index)
    kant = n(idag_rader).value_counts().to_dict() if not idag_rader.empty else {}
    ut = []
    for i, k in n(hamtat).items():
        if kant.get(k, 0) > 0:
            kant[k] -= 1                    # samma rad kan förekomma flera gånger legitimt
        else:
            ut.append(i)
    return hamtat.loc[ut]


def _notifiera(prov, topic):
    """Skicka notiser för fall i bevakning.csv. Körs även när inga nya FI-rader kommit, så att
    aktiveringen av ntfy (första notisen) kan ske vid vilken körning som helst."""
    topic = topic if topic is not None else (os.environ.get("NTFY_TOPIC", "").strip() or None)
    r = notis.kor(topic=topic, server=os.environ.get("NTFY_SERVER", "https://ntfy.sh"), prov=prov)
    checkpoint.spara_i_git([notis.SKICKADE], f"Notiser {datetime.now(ZoneInfo('Europe/Stockholm')):%F %H:%M}")
    return r


def kor(prov=False, tvinga=False, hamta=None, nu=None, topic=None):
    nu = nu or datetime.now(ZoneInfo("Europe/Stockholm"))
    if not tvinga and not borsen_oppen(nu):
        print(f"Börsen stängd ({nu:%a %H:%M}), inget att göra.")
        return {"nya": 0}
    idag = nu.date().isoformat()
    if hamta is None:
        s = requests.Session()
        s.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml", "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8"})
        df, komplett = ib.hamta_dag(s, nu.date())
    else:
        df, komplett = hamta(nu.date())
    if df.empty:
        print(f"{idag}: inga publicerade poster än.")
        return {"nya_rader": 0, **_notifiera(prov, topic)}
    lagra = ib.till_lagring(df)
    nya = nya_rader(idag, lagra, ib.las_historik())
    print(f"{idag}: {len(lagra)} poster hos FI, {len(nya)} nya" + ("" if komplett else " (ofullständig hämtning)"))
    if nya.empty:
        return {"nya_rader": 0, **_notifiera(prov, topic)}
    nya.to_csv(ib.HISTORIK, mode="a", header=not ib.HISTORIK.exists(), index=False)
    # Logg över när nya poster först sågs (FI anger bara datum, inte klockslag): bygger statistik över när på dagen de publiceras.
    try:
        sedd = MAPP / "fi_sedd.csv"
        rad = pd.DataFrame([{"Sedd": nu.strftime("%Y-%m-%d %H:%M"), "Nya_rader": len(nya),
                             "Bolag": "; ".join(sorted(set(nya["Emittent"].astype(str))))[:200]}])
        rad.to_csv(sedd, mode="a", header=not sedd.exists(), index=False)
    except Exception as e:
        print("Kunde inte logga fi_sedd.csv:", e)

    kop = ib.bygg_koplista(ib.las_historik())
    kop.to_csv(ib.KOPLISTA, index=False)
    res = bevakning.bevaka(ib.KOPLISTA, MAPP / "ticker_map.csv", MAPP / "kurser.csv",
                           nyheter_fil=MAPP / "nyheter.csv", diagram_mapp=MAPP / "diagram")
    if res.empty:
        print("Inga bolag uppfyller bevakningens villkor.")
        return {"nya_rader": len(nya), "flaggade": 0}
    res.to_csv(MAPP / "bevakning.csv", index=False)
    r = _notifiera(prov, topic)
    checkpoint.spara_i_git([ib.HISTORIK, ib.KOPLISTA, MAPP / "bevakning.csv", notis.SKICKADE, MAPP / "fi_sedd.csv"],
                           f"Intradag {nu:%F %H:%M}")
    return {"nya_rader": len(nya), **r}


FELFIL = MAPP / "intradag_fel.txt"


if __name__ == "__main__":
    import traceback
    ap = argparse.ArgumentParser()
    ap.add_argument("--tvinga", action="store_true")
    ap.add_argument("--prov", action="store_true")
    a = ap.parse_args()
    try:
        kor(prov=a.prov, tvinga=a.tvinga)
    except Exception:
        # Loggarna kan inte läsas utifrån, så felet sparas i repot. Ett fel ska inte heller
        # göra körningen röd varje 10:e minut.
        fel = f"{datetime.now(ZoneInfo('Europe/Stockholm')):%F %H:%M}\n{traceback.format_exc()}"
        print(fel)
        FELFIL.write_text(fel)
        checkpoint.spara_i_git([FELFIL], "Intradagsfel")
