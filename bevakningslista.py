#!/usr/bin/env python3
"""
Daglig bevakningslista till telefonen (ntfy) med kursutveckling efter att fallet kom på listan.

För varje fall i bevakning.csv: ingångskurs = första stängningskurs på eller efter första publiceringsdagen
(Forsta_publ). Utvecklingen räknas från den kursen:
  1D = stängning 1 handelsdag senare, 7D = 7 kalenderdagar senare, 14D = 14 kalenderdagar senare
  (visas som "-" tills tiden gått), och "tot" = senaste kurs mot ingångskursen.
Listan är sorterad: regelträffar först, sedan Bevaka efter poäng. Sparas också i bevakningslista.csv.

Kör:  python bevakningslista.py --prov          # visa, skicka inte
      NTFY_TOPIC=... python bevakningslista.py  # skicka
"""
import argparse
import os
import re
import sys
from pathlib import Path

import pandas as pd
import requests

MAPP = Path(__file__).resolve().parent
sys.path.insert(0, str(MAPP))
import backtest as bt  # noqa: E402
import notis  # noqa: E402

MAX_BYTES = 3600
RADER_PER_BILD = 24
BILDER = MAPP / "diagram" / "lista"


def _pct(x):
    return "  -  " if pd.isna(x) else f"{x * 100:+.1f}%"


def marknadslage() -> dict:
    """Marknadsläge för OMXSPI ur marknad.csv: trendmått -10..+10 (gränser +-3), orolig marknad om sannolikheten > 50 %."""
    try:
        m = pd.read_csv(MAPP / "marknad.csv", parse_dates=["Datum"]).dropna(subset=["Trend"]).iloc[-1]
    except Exception:
        return {}
    t = float(m["Trend"]); po = float(m["P_orolig"])
    lage = "uppgång" if t > 3 else "nedgång" if t < -3 else "neutral"
    v = str(m.get("Vandning", "")) if pd.notna(m.get("Vandning", "")) else ""
    return {"datum": m["Datum"].date().isoformat(), "trend": round(t, 1), "orolig": round(po * 100), "lage": lage,
            "vandning": v, "bekraftad": bool(m.get("Bekraftad", False))}


def marknadstext(mk: dict) -> str:
    if not mk:
        return ""
    t = f"Marknad (OMXSPI): {mk['lage']}, trend {mk['trend']:+.1f} (skala -10..+10), orosrisk {mk['orolig']} %"
    if mk["vandning"]:
        t += f", {mk['vandning']}{' (bekräftad)' if mk['bekraftad'] else ' (obekräftad)'}"
    return t


def berakna(b: pd.DataFrame, px: pd.DataFrame, idag: pd.Timestamp) -> pd.DataFrame:
    """Utveckling efter publicering, och 7Dx/14Dx/totx = överavkastning mot medianaktien i kursdatan
    (samma start- och slutdag; samma jämförelse som backtestet med --benchmark MEDIAN)."""
    rader = []
    pxf = px.ffill(limit=5)

    def bench(a, z):
        m = (pxf.loc[z] / pxf.loc[a] - 1).replace([float("inf"), float("-inf")], float("nan")).dropna()
        return float(m.median()) if len(m) >= 30 else float("nan")
    for _, r in b.iterrows():
        s = px[r["Ticker"]].dropna() if r["Ticker"] in px else pd.Series(dtype=float)
        ut = {"1D": float("nan"), "7D": float("nan"), "14D": float("nan"), "tot": float("nan"), "Dagar": float("nan"),
              "1Dx": float("nan"), "7Dx": float("nan"), "14Dx": float("nan"), "totx": float("nan")}
        if not s.empty:
            d0 = pd.Timestamp(r["Forsta_publ"])
            i0 = s.index.searchsorted(d0)                  # första kurs på/efter publiceringsdagen
            if i0 < len(s):
                p0 = s.iloc[i0]
                ut["Dagar"] = (idag - d0).days
                ut["tot"] = s.iloc[-1] / p0 - 1 if len(s) - 1 > i0 else float("nan")
                if len(s) - 1 > i0:
                    ut["totx"] = ut["tot"] - bench(s.index[i0], s.index[-1])
                if i0 + 1 < len(s):
                    ut["1D"] = s.iloc[i0 + 1] / p0 - 1
                    ut["1Dx"] = ut["1D"] - bench(s.index[i0], s.index[i0 + 1])
                for namn, dagar in (("7D", 7), ("14D", 14)):
                    slut = s.index[i0] + pd.Timedelta(days=dagar)
                    if s.index[-1] >= slut:
                        j = s.index.searchsorted(slut, side="right") - 1
                        ut[namn] = s.iloc[j] / p0 - 1
                        ut[namn + "x"] = ut[namn] - bench(s.index[i0], s.index[j])
        rader.append(ut)
    return pd.concat([b.reset_index(drop=True), pd.DataFrame(rader)], axis=1)


def rader_text(d: pd.DataFrame) -> list:
    ut = []
    for rubrik, x in avdelningar(d):
        if x.empty:
            continue
        ut.append(f"== {rubrik} ({len(x)}) ==")
        for _, r in x.iterrows():
            namn = re.sub(r"\s*\(\s*publ\s*\)|\s+(aktiebolag|ab|inc\.?|se|ag)\b", "", str(r["Bolag"]), flags=re.I).replace("  ", " ").strip()[:20]
            p = "" if pd.isna(r.get("Poang")) else f"{r['Poang']:.0f}p "
            dagar = "" if pd.isna(r["Dagar"]) else f" ({int(r['Dagar'])}d)"
            ut.append(f"{namn} {p}| 1D {_pct(r['1D'])} 7D {_pct(r['7D'])} 14D {_pct(r['14D'])} tot {_pct(r['tot'])}{dagar}")
    return ut


def avdelningar(d: pd.DataFrame) -> list:
    """Tre avdelningar: regelträffar, trendfall utan kursfall (efter poäng) och knivar (kursfall, efter antal köpare och belopp).
    Knivar är fall som rasat (mer än 20 % under 52v-högsta eller -15 % på 15 dagar): ingen testad edge, men de bevakas
    för att fånga en vändning."""
    kniv = d["Tillstand"].isin(["fall", "snabbt_fall"]) & (d["Kategori"] != "Regelträff")
    regel = d[d["Kategori"] == "Regelträff"].sort_values(["Poang", "Belopp"], ascending=False)
    poang = d[~kniv & (d["Kategori"] != "Regelträff")].sort_values(["Poang", "Belopp"], ascending=False)
    knivar = d[kniv].sort_values(["Antal_personer", "Belopp"], ascending=False)
    return [("REGELTRÄFFAR", regel), ("BEVAKA MED POÄNG (ej efter kursfall)", poang),
            ("KNIVAR: efter kursfall, försöker fånga vändning", knivar)]


def _farg(x):
    """Cellfärg för en förändring (grön upp, röd ned, intensitet efter storlek)."""
    if pd.isna(x):
        return "#f1f1f1"
    a = min(abs(x) / 0.10, 1.0) * 0.75 + 0.1
    return (0.17, 0.62, 0.35, a) if x >= 0 else (0.82, 0.22, 0.2, a)


def rita_bilder(d: pd.DataFrame, idag) -> list:
    """Tabellbilder (PNG), RADER_PER_BILD rader per bild. Returnerar filsökvägar."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    BILDER.mkdir(parents=True, exist_ok=True)
    for gammal in BILDER.glob("*.png"):
        gammal.unlink()
    rader = []                     # (typ, data)
    for rubrik, x in avdelningar(d):
        if x.empty:
            continue
        rader.append(("rubrik", f"{rubrik} ({len(x)})"))
        rader += [("rad", r) for _, r in x.iterrows()]
    sidor, cur = [], []
    for typ, v in rader:
        if len(cur) >= RADER_PER_BILD:
            sidor.append(cur); cur = []
            if typ == "rad":                     # ny sida börjar med rubriken igen
                cur.append(("rubrik", "forts."))
        cur.append((typ, v))
    if cur:
        sidor.append(cur)
    kol = [("Bolag", 0.0, "left"), ("P", 0.27, "center"), ("15d före", 0.34, "center"), ("Från topp", 0.45, "center"),
           ("1D", 0.56, "center"), ("7D", 0.65, "center"), ("14D", 0.74, "center"), ("Totalt", 0.84, "center"), ("Tillagd", 0.95, "center")]
    filer = []
    for n, sida in enumerate(sidor, 1):
        h = 0.34 * (len(sida) + 2)
        fig, ax = plt.subplots(figsize=(8.2, h), dpi=130)
        ax.set_xlim(0, 1); ax.set_ylim(len(sida) + 2, 0); ax.axis("off")
        ax.text(0, 0.5, f"Bevakningslista {idag.date()}  ({n}/{len(sidor)})", fontsize=11, fontweight="bold", va="center")
        ax.text(1.0, 0.5, marknadstext(marknadslage()).replace("Marknad (OMXSPI): ", "Marknad: "), fontsize=7, color="#555", ha="right", va="center")
        for namn, xp, ha in kol:
            ax.text(xp + (0.045 if ha == "center" else 0), 1.5, namn, fontsize=7.5, color="#555", ha=ha, va="center", fontweight="bold")
        ax.plot([0, 1], [1.9, 1.9], color="#999", lw=0.8)
        for i, (typ, v) in enumerate(sida):
            y = i + 2.5
            if typ == "rubrik":
                ax.add_patch(Rectangle((0, y - 0.45), 1, 0.9, color="#e4e8ef", lw=0))
                ax.text(0.005, y, v, fontsize=8.5, fontweight="bold", va="center", color="#223")
                continue
            namn = re.sub(r"\s*\(\s*publ\s*\)|\s+(aktiebolag|ab|inc\.?|se|ag)\b", "", str(v["Bolag"]), flags=re.I).strip()[:26]
            ax.text(0.005, y, namn, fontsize=8, va="center")
            ax.text(0.315, y, "" if pd.isna(v.get("Poang")) else f"{v['Poang']:.0f}", fontsize=8, ha="center", va="center", fontweight="bold")
            fall15 = v.get("Fall_15d_fore_%"); topp = v.get("Under_52v_hogsta_%")
            for xp, val, scal in ((0.34, fall15, 1.0), (0.45, topp, 1.0)):
                ax.add_patch(Rectangle((xp + 0.005, y - 0.4), 0.09, 0.8, color=_farg(None if pd.isna(val) else val / 100), lw=0))
                ax.text(xp + 0.05, y, "-" if pd.isna(val) else f"{val:+.0f}%", fontsize=7.5, ha="center", va="center")
            for xp, k in ((0.56, "1D"), (0.65, "7D"), (0.74, "14D"), (0.84, "tot")):
                val = v[k]
                ax.add_patch(Rectangle((xp + 0.005, y - 0.4), 0.08, 0.8, color=_farg(v[k + "x"]), lw=0))   # färg = mot index
                ax.text(xp + 0.045, y, "-" if pd.isna(val) else f"{val * 100:+.1f}%", fontsize=7.5, ha="center", va="center")
            ax.text(0.995, y, "" if pd.isna(v["Dagar"]) else ("idag" if int(v["Dagar"]) == 0 else "igår" if int(v["Dagar"]) == 1 else f"-{int(v['Dagar'])}"), fontsize=7.5, ha="right", va="center", color="#555")
        fig.text(0.01, 0.003, "15d före = rörelse 15 handelsdagar före publicering; Från topp = avstånd till 52v-högsta. 1D–Totalt från kursen när fallet kom på listan. "
                 "Grönt/rött = bättre/sämre än medianaktien.", fontsize=5.5, color="#777", va="bottom")
        fig.subplots_adjust(left=0.02, right=0.98, top=0.99, bottom=0.025)
        f = BILDER / f"lista_{n}.png"
        fig.savefig(f); plt.close(fig)
        filer.append(f)
    return filer


def dela(rader: list) -> list:
    delar, cur = [], []
    for r in rader:
        if cur and len("\n".join(cur + [r]).encode()) > MAX_BYTES:
            delar.append("\n".join(cur)); cur = []
        cur.append(r)
    if cur:
        delar.append("\n".join(cur))
    return delar


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prov", action="store_true")
    a = ap.parse_args()
    b = pd.read_csv(MAPP / "bevakning.csv")
    if b.empty:
        print("Tom bevakningslista."); return
    b = b.sort_values(["Kategori", "Poang", "Belopp"], ascending=[False, False, False])  # Regelträff före Bevaka
    px = bt.las_kurser(MAPP / "kurser.csv"); px.index = pd.to_datetime(px.index)
    idag = pd.Timestamp.today().normalize()
    d = berakna(b, px, idag)
    d[["Bolag", "Ticker", "Kategori", "Poang", "Forsta_publ", "Dagar", "1D", "7D", "14D", "tot", "1Dx", "7Dx", "14Dx", "totx"]].to_csv(
        MAPP / "bevakningslista.csv", index=False, float_format="%.4f")
    bilder = rita_bilder(d, idag)
    print(f"{len(d)} fall, {len(bilder)} bilder i {BILDER}")
    if a.prov or not os.environ.get("NTFY_TOPIC", "").strip():
        return
    topic = os.environ["NTFY_TOPIC"].strip(); server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    for i, f in enumerate(bilder, 1):
        requests.put(f"{server.rstrip('/')}/{topic}", data=f.read_bytes(), timeout=60,
                     headers={"Filename": f"bevakningslista_{idag.date()}_{i}.png", "Priority": "2",
                              "Title": f"Bevakningslista {idag.date()} ({i}/{len(bilder)})", "Tags": "clipboard"}).raise_for_status()


if __name__ == "__main__":
    main()
