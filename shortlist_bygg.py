"""Bygger Shortlist: en kort sida med de bolag i Biotechscreen som är värda att följa just nu.
Urvalet och texterna ligger i shortlist.csv (handskrivna, med källa i Biotechscreens sammanfattningar); siffrorna (kurs, poäng, kassa,
insynsköp, dagar kvar) hämtas ur den färdigbyggda Biotechscreen. Kör efter bio_bygg.py:
    python shortlist_bygg.py [biotech.html] [utfil]      (standard /tmp/biotech/biotech.html -> /tmp/shortlist/shortlist.html)
Skriver rader som börjar med KONTROLL: när något i urvalet behöver ses över (datum passerat, hård varning, bolag saknas). Ändrar aldrig csv-filen."""
import json
import sys
from pathlib import Path

import pandas as pd

HER = Path(__file__).resolve().parent
IN = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/biotech/biotech.html")
UT = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/shortlist/shortlist.html")
IDAG = pd.Timestamp.now(tz="Europe/Stockholm").tz_localize(None).normalize()


def sv(x, n=1):
    return f"{x:,.{n}f}".replace(",", " ").replace(".", ",")


def las_data():
    h = IN.read_text(encoding="utf-8")
    i = h.index("const DATA = ") + len("const DATA = ")
    d, _ = json.JSONDecoder().raw_decode(h[i:])
    return d


def kassa_text(z, ovr):
    if ovr:
        return ovr
    if z.get("runway") is not None:
        return f"ca {z['runway']:.0f} mån (Yahoo)"
    if z.get("sjalvfin") and z.get("cash"):
        return f"Lönsamt, kassa {sv(z['cash'] / 1000)} mdr SEK"
    return "Okänd"


def main():
    d = las_data()
    A = {a["t"]: a for a in d["aktier"]}
    sl = pd.read_csv(HER / "shortlist.csv", keep_default_na=False, dtype=str)
    lista, nastan, kontroll = [], [], []
    for r in sl.itertuples():
        z = A.get(r.Ticker)
        if z is None:
            kontroll.append(f"KONTROLL: {r.Ticker} finns inte i Biotechscreen")
            continue
        if r.Roll == "nastan":
            nastan.append(dict(t=z["t"], namn=z["namn"], vad=z["vad"], nar=z["nar"], varfor=r.Varfor))
            continue
        dagar = None
        if r.Datum:
            dagar = (pd.Timestamp(r.Datum) - IDAG).days
            if dagar < 0:
                kontroll.append(f"KONTROLL: {z['namn']}: datumet {r.Datum} har passerat")
        if any(w["sev"] == "hard" for w in z["ned"]):
            kontroll.append(f"KONTROLL: {z['namn']} har fått hård varning ({', '.join(w['l'] for w in z['ned'] if w['sev'] == 'hard')})")
        folj = r.Folj.replace("{sma50}", sv(z["tek"]["sma50"]) if z["tek"].get("sma50") is not None else "–")
        lista.append(dict(
            t=z["t"], namn=z["namn"], roll=r.Roll, kurs=z["kurs"], d1=z["d1"], ret20=z["ret20"], vad=z["vad"], nar=z["nar"], samlad=z["samlad"],
            kassa=kassa_text(z, r.Kassa), kop=round(z["kop180"] / 1e6, 2), kop_n=z["kop180_n"], mc=z["mc"],
            handelse=r.Handelse, datum=r.Datum, period=r.Period, dagar=dagar, sakerhet=r.Sakerhet,
            varfor=r.Varfor, fel=r.Fel, folj=folj))
    data = dict(lista=lista, nastan=nastan, genererad=str(IDAG.date()), kursdatum=d["datum"][-1])
    html = (HER / "shortlist_mall.html").read_text(encoding="utf-8").replace("/*DATA*/", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    UT.parent.mkdir(parents=True, exist_ok=True)
    UT.write_text(html, encoding="utf-8")
    print(f"{len(lista)} bolag och {len(nastan)} nästan med, senaste kurs {data['kursdatum']} -> {UT} ({UT.stat().st_size / 1e3:.0f} kB)")
    for k in kontroll:
        print(k)


if __name__ == "__main__":
    main()
