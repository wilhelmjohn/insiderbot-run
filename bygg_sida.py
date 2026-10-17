#!/usr/bin/env python3
"""
Bygger en självständig interaktiv HTML-sida (sida.html) med bevakningslistan: flikar (Knivar, Bevaka med poäng,
Regelträffar, Alla), sök, sortering, poängfilter och detaljrad per bolag. Data är inbäddad (ögonblicksbild).

Publiceras som Artifact. Uppdatera: kör `python bevakning.py && python bygg_sida.py` och publicera sida.html igen.
"""
import base64
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

import backtest as bt
import bevakningslista as bl
import vandning

MAPP = Path(__file__).resolve().parent


def _datatid() -> str:
    """När datan senast uppdaterades: tidpunkten för senaste commit som rörde insynsdata, kurser eller bevakningslistan
    (körningen i GitHub committar dem). Faller tillbaka på nu om git saknas."""
    import subprocess
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        t = subprocess.run(["git", "log", "-1", "--format=%cI", "--", "insiderhandel.csv", "kurser.csv", "bevakning.csv"],
                           cwd=MAPP, capture_output=True, text=True, timeout=30).stdout.strip()
        d = datetime.fromisoformat(t)
    except Exception:
        d = datetime.now(ZoneInfo("Europe/Stockholm"))
    return d.astimezone(ZoneInfo("Europe/Stockholm")).strftime("%Y-%m-%d %H:%M")


def _varfor_ingen_graf(r, px):
    t = r["Ticker"]
    if not t.endswith(".ST"):
        return f"Ingen graf: kursdatan gäller en utländsk notering ({t}), inte Stockholmsbörsen."
    n = int(px[t].dropna().shape[0]) if t in px else 0
    if n < 60:
        return f"Ingen graf än: bara {n} handelsdagar kursdata (nyligen noterat)."
    return ""


def _namn_norm(s):
    s = bl.re.sub(r"\(\s*publ\.?\s*\)|\bab\b|\baktiebolaget\b|\bgroup\b|\bholding\b|[^a-zåäö0-9 ]", "", str(s).lower())
    return " ".join(s.split())


def _blankning():
    """Nivå (summerad nettoblankning, %), 20-dagarsförändring och datum per normaliserat bolagsnamn."""
    f = MAPP / "blankning.csv"
    if not f.exists():
        return {}
    d = pd.read_csv(f)
    return {_namn_norm(r["Emittent"]): (float(r["Niva"]), None if pd.isna(r["Forandring_20d"]) else float(r["Forandring_20d"]), str(r["Datum"])[:10])
            for _, r in d.iterrows()}


def _kurs_till(px, tk, senast):
    """Datum för bolagets senaste kurs om den ligger efter marknadens senaste handelsdag (annars tom sträng)."""
    if tk not in px or not px[tk].notna().any():
        return ""
    d = px[tk].dropna().index.max().date().isoformat()
    return d if d < senast else ""


def data() -> dict:
    b = pd.read_csv(MAPP / "bevakning.csv")
    px = bt.las_kurser(MAPP / "kurser.csv"); px.index = pd.to_datetime(px.index)
    idag = pd.Timestamp.today().normalize()
    d = bl.berakna(b, px, idag)
    kr = pd.read_csv(MAPP / "kurser.csv", usecols=["Datum", "Ticker", "Volym"], parse_dates=["Datum"])
    vol = kr.pivot_table(index="Datum", columns="Ticker", values="Volym", aggfunc="last")
    senast = px.index.max().date().isoformat()
    blk = _blankning()
    rader = []
    for _, r in d.iterrows():
        kniv = r["Tillstand"] in ("fall", "snabbt_fall") and r["Kategori"] != "Regelträff"
        avd = "regel" if r["Kategori"] == "Regelträff" else ("kniv" if kniv else "poang")
        sig = None
        if kniv and r["Ticker"] in px and r["Ticker"] in vol:
            s = vandning.signaler(px[r["Ticker"]], vol[r["Ticker"]], r["Forsta_publ"], tillbaka=10 ** 6)
            if s:
                ser = px[r["Ticker"]].dropna()
                sedan = ser.iloc[-1] / ser.loc[s[0][0]] - 1 if s[0][0] in ser.index and s[0][0] != ser.index[-1] else None
                sig = {"datum": s[0][0].date().isoformat(), "vol": round(s[0][1], 1), "sedan": None if sedan is None else round(float(sedan) * 100, 1)}
        def f(x, n=1):
            return None if pd.isna(x) else round(float(x), n)
        rader.append({
            "bolag": bl.re.sub(r"\s*\(\s*publ\s*\)|\s+(aktiebolag|ab|inc\.?|se|ag)\b", "", str(r["Bolag"]), flags=bl.re.I).strip(),
            "ticker": r["Ticker"].replace(".ST", ""), "avd": avd, "poang": f(r.get("Poang"), 0),
            "pers": int(r["Antal_personer"]), "belopp": f(r["Belopp"], 0), "fall15": f(r.get("Fall_15d_fore_%")),
            "topp": f(r.get("Under_52v_hogsta_%")), "oms": f(r.get("Oms_MSEK_dag"), 2), "bv": f(r.get("Borsvarde_MSEK"), 0),
            "d1": f(r["1D"] * 100), "d7": f(r["7D"] * 100), "d14": f(r["14D"] * 100), "tot": f(r["tot"] * 100),
            "d1x": f(r["1Dx"] * 100), "d7x": f(r["7Dx"] * 100), "d14x": f(r["14Dx"] * 100), "totx": f(r["totx"] * 100),
            "dagar": f(r["Dagar"], 0), "forsta": r["Forsta_publ"], "senaste": r["Senaste_publ"],
            "personer": "" if pd.isna(r.get("Personer")) else str(r["Personer"]),
            "teknik": "" if pd.isna(r.get("Teknik")) else str(r["Teknik"]),
            "extra": "" if pd.isna(r.get("Lagts_till")) else str(r["Lagts_till"]),
            "kommentar": "" if pd.isna(r.get("Kommentar")) else str(r["Kommentar"]),
            "ma200": str(r.get("Over_MA200", "")), "vandning": sig,
            "rapport": "" if pd.isna(r.get("Rapport_nasta_uppsk")) or r.get("Rapport_nasta_uppsk") == "" else str(r["Rapport_nasta_uppsk"]),
            "rapport_kalla": "" if pd.isna(r.get("Rapport_kalla")) else str(r["Rapport_kalla"]),
            "rapport_senast": "" if pd.isna(r.get("Rapport_senast")) else str(r["Rapport_senast"]),
            "ingen_graf": _varfor_ingen_graf(r, px),
            "kurs_till": _kurs_till(px, r["Ticker"], senast),
            "sen": f(r.get("Sen_anm_dagar"), 0), "affar": "" if pd.isna(r.get("Affar_datum")) else str(r["Affar_datum"]),
            "blank": (blk.get(_namn_norm(r["Bolag"])) or (None, None, None))[0],
            "blank_f": (blk.get(_namn_norm(r["Bolag"])) or (None, None, None))[1],
            "blank_d": (blk.get(_namn_norm(r["Bolag"])) or (None, None, None))[2],
            "rod": "" if pd.isna(r.get("Rod_flagga")) else str(r["Rod_flagga"]),
            "nyhet": "" if pd.isna(r.get("Nyhet_fore")) else str(r["Nyhet_fore"]),
            "nyhet_rubrik": "" if pd.isna(r.get("Nyhet_rubrik")) else str(r["Nyhet_rubrik"]),
            "nyhet_kalla": "" if pd.isna(r.get("Nyhet_kalla")) else str(r["Nyhet_kalla"]),
            "aterkop": "" if pd.isna(r.get("Aterkop")) else str(r["Aterkop"]),
        })
    sf = {}
    sfp = MAPP / "bev_sammanfattning.json"
    if sfp.exists():
        sf = json.loads(sfp.read_text(encoding="utf-8"))
    import bev_sf
    si = pd.read_csv(MAPP / "sektor_info.csv").set_index("Ticker")
    ohl = pd.read_csv(MAPP / "ohlc.csv", parse_dates=["Datum"]) if (MAPP / "ohlc.csv").exists() else None
    djp = json.loads((MAPP / "bev_djup.json").read_text(encoding="utf-8")) if (MAPP / "bev_djup.json").exists() else {}
    try:
        dd = bev_sf.las()
    except Exception:
        dd = None
    for x in rader:
        try:
            tk0 = x["ticker"] if "." in x["ticker"] else x["ticker"] + ".ST"
            x["block"] = bev_sf.blockflagga(dd, tk0) if dd is not None else None
        except Exception:
            x["block"] = None
        x["djup"] = djp.get(x["ticker"])
        x["sf"] = sf.get(x["ticker"])
        if x["djup"] and not x["sf"]:
            tk = x["ticker"] if "." in x["ticker"] else x["ticker"] + ".ST"
            x["gx"] = dict(handelser=x["djup"].get("handelser", []), graf=bev_sf.graf(px, si, tk) if tk in px.columns else None)
        if x["sf"]:
            tk = x["ticker"] if "." in x["ticker"] else x["ticker"] + ".ST"
            x["sf"]["graf"] = bev_sf.graf(px, si, tk) if tk in px.columns else None
            try:
                oh = ohl[ohl.Ticker == tk].set_index("Datum") if ohl is not None else None
                x["sf"]["fm"] = bev_sf.teknik(px, oh, tk) if tk in px.columns else []
            except Exception:
                x["sf"]["fm"] = []
    bilder = {}
    for _, r in d.iterrows():
        f = MAPP / "diagram" / str(r.get("Diagram", ""))
        if str(r.get("Diagram", "")) not in ("", "nan") and f.exists():
            im = Image.open(f).convert("RGB")
            im.thumbnail((990, 720))
            buf = io.BytesIO(); im.save(buf, "JPEG", quality=68, optimize=True)
            bilder[r["Ticker"].replace(".ST", "")] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    return {"marknad": bl.marknadslage(), "bilder": bilder, "rader": rader, "uppdaterad": _datatid(), "kursdatum": senast}


MALL = Path(__file__).with_name("sida_mall.html")


def main():
    html = MALL.read_text().replace("__DATA__", json.dumps(data(), ensure_ascii=False))
    (MAPP / "sida.html").write_text(html)
    print("skrev sida.html", len(html) // 1024, "kB")


if __name__ == "__main__":
    main()
