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
                            vecka_datum=dv.strftime("%Y-%m-%d"),
                            kurva_nu=kurva(d0), kurva_igar=kurva(d1), kurva_vecka=kurva(dv), kurva_manad=kurva(dm))
    rantor["kalla"] = "USA: U.S. Treasury, daily par yield curve. Sverige: Riksbanken (SWEA), statsskuldväxlar och statsobligationer."
except Exception as e:
    rantor = dict(fel=f"{e.__class__.__name__}: {e}")

# ---------- oromått (oro.csv från oro_data.py: HY, IG, NDX, SOX, SPX, VIX) ----------
oro = {}
try:
    o = pd.read_csv(D / "oro.csv", parse_dates=["Datum"]).pivot_table(index="Datum", columns="Serie", values="Varde").sort_index()
    def spread(k):
        x = o[k].dropna(); t = x.index[-1]
        fore = lambda dagar: x[x.index <= t - pd.Timedelta(days=dagar)].iloc[-1]
        return dict(datum=t.strftime("%Y-%m-%d"), nu=r2(x.iloc[-1]), dag=r2(x.iloc[-1] - x.iloc[-2]), vecka=r2(x.iloc[-1] - fore(7)),
                    manad=r2(x.iloc[-1] - fore(30)), fran_3m_lagsta=r2(x.iloc[-1] - x[x.index > t - pd.Timedelta(days=91)].min()),
                    ar_min=r2(x[x.index > t - pd.Timedelta(days=365)].min()), ar_max=r2(x[x.index > t - pd.Timedelta(days=365)].max()),
                    percentil_3ar=r2(100 * (x < x.iloc[-1]).mean()),
                    historik=[[d.strftime("%Y-%m-%d"), r2(v)] for d, v in x[x.index > t - pd.Timedelta(days=365)].items()])
    def rel(a, b="SPX"):
        x = (o[a] / o[b]).dropna(); t = x.index[-1]
        f = lambda n: r2(100 * (x.iloc[-1] / x.iloc[-1 - n] - 1))
        h = x[x.index > t - pd.Timedelta(days=183)]
        return dict(datum=t.strftime("%Y-%m-%d"), dag=f(1), v5=f(5), v20=f(20), v60=f(60),
                    fran_topp_6m=r2(100 * (x.iloc[-1] / h.max() - 1)), historik=[[d.strftime("%Y-%m-%d"), r2(100 * v / h.iloc[0])] for d, v in h.items()])
    def idx(k):
        x = o[k].dropna()
        return dict(datum=x.index[-1].strftime("%Y-%m-%d"), nu=r2(x.iloc[-1]), dag=r2(100 * (x.iloc[-1] / x.iloc[-2] - 1)), v20=r2(100 * (x.iloc[-1] / x.iloc[-21] - 1)),
                    fran_arshogsta=r2(100 * (x.iloc[-1] / x.tail(252).max() - 1)))
    oro = dict(hy=spread("HY"), ig=spread("IG"), ndx_mot_spx=rel("NDX"), sox_mot_spx=rel("SOX"), ndx=idx("NDX"), sox=idx("SOX"), spx=idx("SPX"),
               kalla="Kreditspreadar: ICE BofA via FRED (St. Louis Fed), ett par dagars eftersläpning. Index: Yahoo Finance, stängning.")
    hy, n20, s20 = oro["hy"], oro["ndx_mot_spx"]["v20"], oro["sox_mot_spx"]["v20"]
    skal = []
    if hy["nu"] >= 5 or hy["manad"] >= 1.0: niva = "Stress"; skal.append("kreditspreaden för högräntebolag är hög eller har stigit kraftigt")
    else:
        if hy["fran_3m_lagsta"] >= 0.4: skal.append(f"högräntespreaden har stigit {hy['fran_3m_lagsta']:.2f} p.e. från tremånaderslägsta".replace(".", ",", 1))
        if n20 <= -4: skal.append(f"Nasdaq-100 har gått {abs(n20):.1f} % sämre än S&P 500 på 20 dagar".replace(".", ",", 1))
        if s20 <= -8: skal.append(f"halvledarna har gått {abs(s20):.1f} % sämre än S&P 500 på 20 dagar".replace(".", ",", 1))
        niva = "Ökande oro" if skal else "Lugnt"
    oro["lage"] = dict(niva=niva, skal=skal, regler="Tumregler, inte backtestade: Stress om HY ≥ 5 % eller +1,0 p.e. på en månad. Ökande oro om HY +0,4 p.e. "
                       "från tremånaderslägsta, Nasdaq-100 −4 % eller halvledare −8 % mot S&P 500 på 20 handelsdagar.")
except Exception as e:
    oro = dict(fel=f"{e.__class__.__name__}: {e}")

json.dump(dict(index=index, marknadsmatt=matt, rantor=rantor, oro=oro, divergens=divergens, ras=ras), sys.stdout, ensure_ascii=False, indent=1)
