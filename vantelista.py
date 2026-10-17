#!/usr/bin/env python3
"""
Väntelista: Bevaka-fall efter kursfall som ännu inte visat bottenkänning.

Ett insynsköp efter kursfall är ingen regelträff (ingen testad edge direkt). Fallet läggs här i 90 handelsdagar och
bevakas dagligen. När aktien visar bottenkänning (trigger F2) kommer en notis, även om inga nya insynsköp gjorts:

  F2: stängning över högsta stängningen de senaste 10 dagarna OCH ingen ny 20-dagarsbotten de senaste 10 dagarna.

STATUS: INTE AKTIVERAD (ej kopplad till intradag.py). Ett första test gav +2,7 %, men det byggde på kursserier med
luckor. Rättat (bolagets egna handelsdagar) triggar F2 i 97 % av fallen efter ca 19 dagar och ger ingen skillnad mot att köpa
direkt (median 0,0 % mot +0,6 %; 63 dagar mot medianaktien, 1 501 fall). Ska testas om med längre historik och en
strängare trigger innan den slås på.

Fallet lämnar listan vid trigger (notis skickad), när stängningen understiger riskgränsen (stoppad) eller efter
90 handelsdagar (utgången). Tillstånd sparas i vantelista.csv.
"""
import os
from pathlib import Path

import numpy as np
import pandas as pd

MAPP = Path(__file__).resolve().parent
LISTA = MAPP / "vantelista.csv"
KOLUMNER = ["Ticker", "Bolag", "Forsta_publ", "Tillagd", "Stop", "Kopkurs", "Belopp", "Antal_personer", "Personer",
            "Under_52v_hogsta_%", "Status", "Trigger_datum"]
MAX_DAGAR = 90          # handelsdagar efter första publiceringen
MIN_OMS = 2.0           # MSEK/dag, som i testet
EFFEKT = "ingen påvisad effekt i backtestet (0,0 % mot +0,6 % om man köpt direkt)"


def _f2(s: pd.Series, i: int) -> bool:
    """Bottenkänning vid position i: över högsta stängningen de 10 senaste dagarna och inte lägsta kurs senaste 10 dagarna
    inom de 20 senaste (alltså ingen ny 20-dagarsbotten på 10 dagar)."""
    if i < 20:
        return False
    c = s.iloc[i]
    if pd.isna(c) or s.iloc[i - 10:i].isna().all():
        return False
    fonster = s.iloc[i - 19:i + 1].to_numpy(float)
    if np.isnan(fonster).all():
        return False
    return bool(c > np.nanmax(s.iloc[i - 10:i].to_numpy(float)) and int(np.nanargmin(fonster)) < 10)


def las() -> pd.DataFrame:
    if LISTA.exists():
        d = pd.read_csv(LISTA, keep_default_na=False, dtype=str)
        for c in KOLUMNER:
            if c not in d.columns:
                d[c] = ""
        return d[KOLUMNER]
    return pd.DataFrame(columns=KOLUMNER)


def lagg_till(lista: pd.DataFrame, bev: pd.DataFrame, idag: pd.Timestamp) -> pd.DataFrame:
    """Nya Bevaka-fall efter kursfall (likvida, .ST) hamnar på listan."""
    if bev is None or bev.empty:
        return lista
    kand = bev[(bev["Kategori"] == "Bevaka") & bev["Tillstand"].isin(["fall", "snabbt_fall"])
               & (pd.to_numeric(bev["Oms_MSEK_dag"], errors="coerce") >= MIN_OMS) & bev["Ticker"].astype(str).str.endswith(".ST")]
    kand = kand[pd.notna(kand["Stop"])]
    have = set(zip(lista["Ticker"], lista["Forsta_publ"]))
    rader = []
    for _, r in kand.iterrows():
        if (r["Ticker"], r["Forsta_publ"]) in have:
            continue
        rader.append({"Ticker": r["Ticker"], "Bolag": r["Bolag"], "Forsta_publ": r["Forsta_publ"],
                      "Tillagd": idag.date().isoformat(), "Stop": r["Stop"], "Kopkurs": r["Kopkurs"], "Belopp": r["Belopp"],
                      "Antal_personer": r["Antal_personer"], "Personer": r["Personer"],
                      "Under_52v_hogsta_%": r["Under_52v_hogsta_%"], "Status": "bevakar", "Trigger_datum": ""})
    if rader:
        lista = pd.concat([lista, pd.DataFrame(rader)], ignore_index=True)
    return lista


def uppdatera(lista: pd.DataFrame, px: pd.DataFrame, idag: pd.Timestamp):
    """Går igenom bevakade fall med senaste kurser. Returnerar (ny lista, fall som triggats idag)."""
    lista = lista.copy()
    triggade = []
    dates = px.index
    for ix, r in lista[lista["Status"] == "bevakar"].iterrows():
        t = r["Ticker"]
        if t not in px.columns:
            continue
        s = px[t].dropna()
        if len(s) < 30:
            continue
        i0 = s.index.searchsorted(pd.Timestamp(r["Forsta_publ"]))
        senaste = len(s) - 1
        if senaste - i0 > MAX_DAGAR:
            lista.loc[ix, "Status"] = "utgången"
            continue
        stop = float(r["Stop"]) if r["Stop"] not in ("", "nan") else np.nan
        if pd.notna(stop) and s.iloc[-1] < stop and senaste - i0 >= 1:
            lista.loc[ix, "Status"] = "stoppad"
            continue
        # första dag efter publiceringen (+2 handelsdagar, som i testet) som uppfyller villkoret
        trig = None
        for i in range(max(i0 + 2, 20), senaste + 1):
            if _f2(s, i):
                trig = i
                break
        if trig is None:
            continue
        if senaste - trig <= 2:              # färsk trigger (tillåter att en körning missats)
            lista.loc[ix, "Status"] = "triggad"
            lista.loc[ix, "Trigger_datum"] = s.index[trig].date().isoformat()
            triggade.append(ix)
        else:                                  # hände redan tidigare, innan fallet lades på listan: ingen notis
            lista.loc[ix, "Status"] = "triggad_tidigare"
            lista.loc[ix, "Trigger_datum"] = s.index[trig].date().isoformat()
    return lista, triggade


def meddelande(r, s: pd.Series, tek: dict, bild=None) -> dict:
    uppg = s.iloc[-1] / s.iloc[-30:].min() - 1
    rader = [f"Insynsköp {r['Forsta_publ']}: {r['Antal_personer']} pers, {float(r['Belopp'])/1e6:.1f} Mkr "
             f"({str(r['Personer'])[:90]}). Aktien låg då {r['Under_52v_hogsta_%']} % från 52v-högsta.",
             f"Nu: bottenkänning, ny 10-dagarstopp utan ny 20-dagarsbotten. Kurs {s.iloc[-1]:.2f}, "
             f"+{uppg*100:.0f} % från 30-dagarsbotten."]
    if tek:
        rader.append("Teknik: " + tek["Text"].replace("\n", "\n"))
    rader.append(f"Testad effekt: {EFFEKT}. Liten edge, ingen köpsignal. Egen bedömning krävs.")
    return {"bild": bild, "title": f"Stabiliserad efter insynsköp: {r['Bolag']} ({str(r['Ticker']).replace('.ST', '')})",
            "message": "\n".join(rader), "priority": 3, "tags": ["chart_with_upwards_trend", "insiderbot"]}


def kor(topic=None, prov=False, bevakning_csv=MAPP / "bevakning.csv", px=None, idag=None, server="https://ntfy.sh",
        skicka_fn=None, lista_fil=LISTA, hamta_handel=True):
    import analys
    import notis
    if px is None:
        import backtest as bt
        px = bt.las_kurser(MAPP / "kurser.csv")
        px.index = pd.to_datetime(px.index)
    idag = pd.Timestamp(idag) if idag is not None else pd.Timestamp.today().normalize()
    bev = pd.read_csv(bevakning_csv) if Path(bevakning_csv).exists() else pd.DataFrame()
    lista = las() if lista_fil == LISTA else (pd.read_csv(lista_fil, keep_default_na=False, dtype=str) if Path(lista_fil).exists() else pd.DataFrame(columns=KOLUMNER))
    lista = lagg_till(lista, bev, idag)
    lista, triggade = uppdatera(lista, px, idag)
    msgs = []
    if triggade:
        try:
            import bevakning
            handel = bevakning._handel_per_ticker(MAPP / "ticker_map.csv") if hamta_handel else {}
        except Exception as e:
            print(f"Insynshandel för graf misslyckades: {e}")
            handel = {}
        (MAPP / "diagram").mkdir(exist_ok=True)
        for ix in triggade:
            r = lista.loc[ix]
            s = px[r["Ticker"]].dropna()
            tek = analys.teknisk(s)
            bild = None
            try:
                sokv = MAPP / "diagram" / f"{r['Ticker']}_stabil.png"
                analys.rita(r["Bolag"], r["Ticker"], s, tek, sokv, publ_datum=r["Forsta_publ"],
                            kopkurs=float(r["Kopkurs"]) if r["Kopkurs"] not in ("", "nan") else None, handel=handel.get(r["Ticker"]))
                bild = str(sokv)
            except Exception as e:
                print(f"Graf för {r['Ticker']} misslyckades: {e}")
            msgs.append(meddelande(r, s, tek, bild))
    for m in msgs:
        print(f"- {m['title']}\n  " + m["message"].replace("\n", "\n  "))
    print(f"Väntelista: {int((lista['Status'] == 'bevakar').sum())} bevakas, {len(triggade)} triggade nu")
    if not prov:
        if msgs and topic:
            (skicka_fn or notis.skicka)(msgs, topic, server)
        elif msgs and not topic:
            print("NTFY_TOPIC saknas: inget skickades.")
            return {"triggade": len(msgs), "skickade": 0}
        lista.to_csv(lista_fil, index=False)
    return {"triggade": len(msgs), "skickade": len(msgs) if topic else 0}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--prov", action="store_true")
    a = ap.parse_args()
    kor(topic=os.environ.get("NTFY_TOPIC", "").strip() or None, prov=a.prov)
