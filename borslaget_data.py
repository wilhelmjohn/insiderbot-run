"""Börsläget: samlar kvällens siffror ur datarepot till JSON (för den schemalagda Claude-uppgiften som bygger sidan).

  python borslaget_data.py <datarepo> > borslaget.json

Innehåll: index (dag, vecka, i år, från årshögsta) för Stockholm, Oslo, Köpenhamn och USA (USA är gårdagens stängning
i datan: dagens tal hämtas från webben), valutor, marknadsmåttet (-10..+10) med 30 dagars historik, orolig regim,
avstånd till MA50/MA200/20-dagarshögsta, Divergensscreen (köpläge, närmar sig, i morgon, positioner) och Rasbevakning
(facit, nya ras, väntar), statsräntor för USA och Sverige (nivå, dagens förändring, 10-2-årsskillnad, kurvan i dag och
för en månad sedan; rantor.csv från rantor.py). Skriver inget till repot.
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

# ---------- räntor (rantor.csv: Datum,Land,Lopetid,Manader,Ranta) ----------
rantor = {}
try:
    rr = pd.read_csv(D / "rantor.csv", parse_dates=["Datum"])
    for land, g in rr.groupby("Land"):
        p_ = g.pivot_table(index="Datum", columns="Manader", values="Ranta").sort_index()
        p_ = p_[p_.notna().sum(axis=1) >= max(3, p_.shape[1] // 2)]       # bara dagar med i stort sett hela kurvan
        d0, d1 = p_.index[-1], p_.index[-2]
        dv = p_.index[p_.index <= d0 - pd.Timedelta(days=7)][-1]; dm = p_.index[p_.index <= d0 - pd.Timedelta(days=30)][-1]
        namn_ = g.drop_duplicates("Manader").set_index("Manader").Lopetid.to_dict()
        def niv(m):
            if m not in p_.columns or pd.isna(p_.at[d0, m]): return None
            return dict(lopetid=namn_[m], ranta=r2(p_.at[d0, m]), dag_pe=r2(p_.at[d0, m] - p_.at[d1, m]), vecka_pe=r2(p_.at[d0, m] - p_.at[dv, m]),
                        manad_pe=r2(p_.at[d0, m] - p_.at[dm, m]), ar_hogsta=r2(p_[m].tail(250).max()), ar_lagsta=r2(p_[m].tail(250).min()))
        spr = lambda d: r2(p_.at[d, 120] - p_.at[d, 24]) if 120 in p_.columns and 24 in p_.columns else None
        kurva = lambda d: [[int(m), namn_[m], r2(v)] for m, v in p_.loc[d].items() if pd.notna(v)]
        rantor[land] = dict(datum=d0.strftime("%Y-%m-%d"), foregaende=d1.strftime("%Y-%m-%d"), manad_datum=dm.strftime("%Y-%m-%d"),
                            nivaer={namn_[m]: niv(m) for m in (3, 24, 60, 120, 360) if m in p_.columns},
                            spread_10_2=dict(nu=spr(d0), igar=spr(d1), vecka=spr(dv), manad=spr(dm),
                                             ar_min=r2((p_[120] - p_[24]).tail(250).min()) if 24 in p_.columns else None,
                                             ar_max=r2((p_[120] - p_[24]).tail(250).max()) if 24 in p_.columns else None),
                            kurva_nu=kurva(d0), kurva_manad=kurva(dm))
    rantor["kalla"] = "USA: U.S. Treasury, daily par yield curve. Sverige: Riksbanken (SWEA), statsskuldväxlar och statsobligationer."
except Exception as e:
    rantor = dict(fel=f"{e.__class__.__name__}: {e}")

json.dump(dict(index=index, marknadsmatt=matt, rantor=rantor, divergens=divergens, ras=ras), sys.stdout, ensure_ascii=False, indent=1)
