"""Börsläget: samlar kvällens siffror ur datarepot till JSON (för den schemalagda Claude-uppgiften som bygger sidan).

  python borslaget_data.py <datarepo> > borslaget.json

Innehåll: index (dag, vecka, i år, från årshögsta) för Stockholm, Oslo, Köpenhamn och USA (USA är gårdagens stängning
i datan: dagens tal hämtas från webben), valutor, marknadsmåttet (-10..+10) med 30 dagars historik, orolig regim,
avstånd till MA50/MA200/20-dagarshögsta, Divergensscreen (köpläge, närmar sig, i morgon, positioner) och Rasbevakning
(facit, nya ras, väntar). Skriver inget till repot.
"""
import sys, json, re
from pathlib import Path
import pandas as pd

D = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
r2 = lambda x: None if x is None or pd.isna(x) else round(float(x), 2)

px = pd.concat([pd.read_csv(D / f, usecols=["Datum", "Ticker", "Stang"], parse_dates=["Datum"])
                for f in ("kurser.csv", "divergens_nordic.csv", "divergens_usa.csv")])
INDEX = [("SE", "OMXS30", "^OMX"), ("SE", "Stockholm hela (OMXSPI)", "^OMXSPI"), ("SE", "Small cap", "^OMXSSCPI"),
         ("NO", "Oslo (OSEBX)", "OSEBX.OL"), ("DK", "OMXC25", "^OMXC25"),
         ("US", "S&P 500", "^GSPC"), ("US", "Nasdaq-100", "^NDX"), ("US", "Dow Jones", "^DJI"),
         ("FX", "NOK/SEK", "NOKSEK=X"), ("FX", "USD/SEK", "USDSEK=X"), ("FX", "DKK/SEK", "DKKSEK=X")]
index = []
for land, namn, t in INDEX:
    x = px[px.Ticker == t].drop_duplicates("Datum").set_index("Datum").Stang.sort_index().dropna()
    if len(x) < 6: index.append(dict(land=land, namn=namn, ticker=t, saknas=True)); continue
    fjol = x[:f"{x.index[-1].year - 1}-12-31"]
    index.append(dict(land=land, namn=namn, ticker=t, datum=x.index[-1].strftime("%Y-%m-%d"), kurs=r2(x.iloc[-1]),
                      dag=r2(100 * (x.iloc[-1] / x.iloc[-2] - 1)), vecka=r2(100 * (x.iloc[-1] / x.iloc[-6] - 1)),
                      i_ar=r2(100 * (x.iloc[-1] / fjol.iloc[-1] - 1)) if len(fjol) and x.index[0] < fjol.index[-1] - pd.Timedelta(days=200) else None,
                      fran_topp=r2(100 * (x.iloc[-1] / x.iloc[-250:].max() - 1)) if len(x) >= 250 else None))

m = pd.read_csv(D / "marknad.csv", parse_dates=["Datum"]).drop_duplicates("Datum").set_index("Datum")
I = m.Index
matt = dict(datum=m.index[-1].strftime("%Y-%m-%d"), trend=r2(m.Trend.iloc[-1]), trend_igar=r2(m.Trend.iloc[-2]),
            trend_vecka=r2(m.Trend.iloc[-6]), p_orolig=r2(m.P_orolig.iloc[-1]), p_orolig_igar=r2(m.P_orolig.iloc[-2]),
            vandning=str(m.Vandning.iloc[-1]) if pd.notna(m.Vandning.iloc[-1]) else "", bekraftad=bool(m.Bekraftad.iloc[-1]),
            mot_ma50=r2(100 * (I.iloc[-1] / I.rolling(50).mean().iloc[-1] - 1)), mot_ma200=r2(100 * (I.iloc[-1] / I.rolling(200).mean().iloc[-1] - 1)),
            fran_20d_hogsta=r2(100 * (I.iloc[-1] / I.tail(20).max() - 1)), fran_arshogsta=r2(100 * (I.iloc[-1] / I.tail(250).max() - 1)),
            trend_ar_median=r2(m.Trend.tail(250).median()), trend_ar_p25=r2(m.Trend.tail(250).quantile(.25)), trend_ar_p75=r2(m.Trend.tail(250).quantile(.75)),
            historik=[[d.strftime("%Y-%m-%d"), r2(v)] for d, v in m.Trend.tail(30).items()])

s = json.loads((D / "divergens_status.json").read_text())
aktier = {k: v for k, v in s.items() if not k.startswith("_")}
bev = s.get("_bevakning", {})
divergens = dict(
    koplage=[dict(t=k, namn=v["namn"], land=v["land"], valuta=v["valuta"], kurs=r2(v["kurs"]), **{x: (v["niv"][x] if x == "signaldag" else round(float(v["niv"][x]), 4) if x == "risk" else r2(v["niv"][x]))
             for x in ("signaldag", "kop", "stopp", "m2", "m3", "risk", "antal")}) for k, v in aktier.items() if v["status"] == "Köpläge" and v.get("niv")],
    i_morgon=[dict(t=k, namn=v["namn"], valuta=v["valuta"], kurs=r2(v["kurs"]), **{x: r2(y) if isinstance(y, float) else y for x, y in v["imorgon"].items()})
              for k, v in aktier.items() if v.get("imorgon")],
    narmar_sig=[dict(t=k, namn=v["namn"], land=v["land"], valuta=v["valuta"], kurs=r2(v["kurs"]), vantar_pa=bev.get(k, {}).get("not_", ""))
                for k, v in aktier.items() if v["status"] == "Närmar sig"],
    positioner=s.get("_positioner", []))

h = (D / "ras.html").read_text(encoding="utf-8")
R = json.loads(re.search(r"const DATA=(\{.*?\});\n", h).group(1))
ras = dict(meta={k: R["meta"].get(k) for k in ("uppdaterad", "kursdag", "vanta", "start", "risk_kr")},
           facit=[{k: f.get(k) for k in ("t", "namn", "rasdag", "signaldag", "ingang", "stopp0", "m2", "m3", "klar", "ut", "slutdag", "slutkurs",
                                         "kurs_nu", "res", "kr", "antal", "sista")} for f in R["facit"]],
           vantar=[{k: v.get(k) for k in ("t", "namn", "rasdag", "fall", "kvar", "dag5", "kurs", "rasstang", "sedan", "rel", "insyn", "just_nu", "orsak")}
                   for v in R["vantar"]])
ras["nya_ras_idag"] = [v for v in ras["vantar"] if v["rasdag"] == ras["meta"]["kursdag"]]
ras["nya_signaler_idag"] = [f for f in ras["facit"] if f["signaldag"] == ras["meta"]["kursdag"]]
ras["avslutade_idag"] = [f for f in ras["facit"] if f.get("slutdag") == ras["meta"]["kursdag"]]

json.dump(dict(index=index, marknadsmatt=matt, divergens=divergens, ras=ras), sys.stdout, ensure_ascii=False, indent=1)
