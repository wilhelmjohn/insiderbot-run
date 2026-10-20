"""Rasbevakning: papperstest av "ras som fortsätter" (blankning), Stockholmsbörsen, alla aktier med omsättning >= 2 MSEK/dag.

  python ras_bygg.py <ut.html>        (körs i datarepot; läser kurser.csv m.fl., skriver ras_logg.csv och <ut.html>)

Regler (låsta 2026-10-20, samma som backtestet SB 2016-2026):
  1. Rasdag: aktien faller minst 8 % på en dag och minst 5 procentenheter mer än OMXSPI.
  2. Inga insynsköp (förvärv av aktier) i bolaget från rasdagen till och med dag 5 efter.
  3. Dag 5 efter raset: stängningen ligger under rasdagens stängning och aktien har gått sämre än index sedan rasdagen.
     Då "blankas" aktien på papper till dag 5:s stängningskurs.
  4. Stopp 1 % över högsta stängning sedan raset (risk 1,5-8 %, annars ingen affär). Köp tillbaka halva vid 2R och flytta
     stoppen till ingången, resten vid 3R, vid stoppen eller efter 10 handelsdagar. Kostnad 0,35 % per affär.
Facit räknas bara på signaler från testets start. Resultatet mot index (blankad aktie mot köpt OMXSPI, 10 dagar) visas också.
Ingen handel: sidan är ett test framåt i tiden.
"""
import sys, re, json, datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd

UT = sys.argv[1] if len(sys.argv) > 1 else "ras.html"
START = pd.Timestamp("2026-10-20")
OMS_MIN, FALL, REL, VANTA, HALL, KOST = 2e6, -0.08, -0.05, 5, 10, 0.0035
LOGG = Path("ras_logg.csv")

k = pd.read_csv("kurser.csv", usecols=["Datum", "Ticker", "Stang", "Volym"], parse_dates=["Datum"])
k = k[k.Ticker.str.endswith(".ST") & (k.Datum >= "2025-06-01")]
px = k.pivot_table(index="Datum", columns="Ticker", values="Stang").sort_index().ffill(limit=3)
vo = k.pivot_table(index="Datum", columns="Ticker", values="Volym").sort_index().reindex(px.index)
dates, cols = px.index, list(px.columns)
C = px.values; T = len(dates)
OMS = (px * vo).rolling(60, min_periods=40).median().values
TOP80 = pd.DataFrame(OMS).rank(axis=1, ascending=False).values <= 80
mk = pd.read_csv("marknad.csv", parse_dates=["Datum"]).drop_duplicates("Datum").set_index("Datum").Index.reindex(dates).ffill()
I = mk.values
MKDD = (mk / pd.read_csv("marknad.csv", parse_dates=["Datum"]).drop_duplicates("Datum").set_index("Datum").Index.rolling(250, min_periods=200).max().reindex(dates).ffill() - 1).values
sek = pd.read_csv("sektor_info.csv").set_index("Ticker")
namn = lambda t: re.sub(r"\s*\(publ\.?\)|\s+AB\b|\s+SE\b", "", str(sek.longName.get(t, t))).strip() if t in sek.index else t
tm = pd.read_csv("ticker_map.csv"); bid2t = tm.set_index("Bolag_id").Ticker
kop = pd.read_csv("insiderkop.csv", parse_dates=["Publiceringsdatum"])
kop = kop[(kop["Karaktär"] == "Förvärv") & (kop["Instrumenttyp"] == "Aktie")].copy()
kop["Ticker"] = kop.Bolag_id.map(bid2t)
KOP = {t: set(g.Publiceringsdatum.dt.normalize()) for t, g in kop.dropna(subset=["Ticker"]).groupby("Ticker")}
try:
    nyh = pd.read_csv("nyheter.csv", usecols=["Publicerat", "Bolag_id", "Typ", "Rubrik"], parse_dates=["Publicerat"])
    nyh = nyh[nyh.Publicerat >= dates[-60]]; nyh["Ticker"] = nyh.Bolag_id.map(bid2t)
except Exception:
    nyh = pd.DataFrame(columns=["Publicerat", "Ticker", "Typ", "Rubrik"])
f2 = lambda x: round(float(x), 2)


def utfall(i, j, e, stop0):
    """Pappersblankning från dag i. Returnerar dict med status och resultat (om avslutad eller pågående)."""
    risk = (stop0 - e) / e
    risk = max(risk, 0.015); stop, t2, t3 = e * (1 + risk), e * (1 - 2 * risk), e * (1 - 3 * risk)
    pos, pnl, half, ut, slut = 1.0, 0.0, False, None, None
    for d in range(1, HALL + 1):
        if i + d >= T: break
        x = C[i + d, j]
        if not np.isfinite(x): continue
        if x >= stop: pnl += pos * (1 - x / e); pos = 0; ut = "Stopp" if not half else "2R, sedan stopp på ingång"; slut = i + d; break
        if not half and x <= t2: pnl += 0.5 * (1 - x / e); pos = 0.5; half = True; stop = e
        elif half and x <= t3: pnl += pos * (1 - x / e); pos = 0; ut = "3R"; slut = i + d; break
    dagar = min(HALL, T - 1 - i)
    if slut is None and i + HALL <= T - 1:
        pnl += pos * (1 - C[i + HALL, j] / e); ut = "Tiden ute" + (" (halva vid 2R)" if half else ""); slut = i + HALL; pos = 0
    nu = C[min(T - 1, i + HALL), j] if slut is None else C[slut, j]
    if slut is None:  # pågående: orealiserat
        pnl_nu = pnl + pos * (1 - C[T - 1, j] / e)
        return dict(klar=False, dagar=dagar, halv=half, stopp_nu=f2(stop), kurs_nu=f2(C[T - 1, j]), res=pnl_nu - KOST, R=(pnl_nu - KOST) / risk,
                    risk=risk, m2=f2(t2), m3=f2(t3))
    rel = None
    if i + HALL <= T - 1:
        rel = -(C[i + HALL, j] / e - 1) + (I[i + HALL] / I[i] - 1) - KOST
    return dict(klar=True, slut_i=slut, dagar=slut - i, halv=half, ut=ut, slutdag=dates[slut].strftime("%Y-%m-%d"), slutkurs=f2(C[slut, j]),
                res=pnl - KOST, R=(pnl - KOST) / risk, risk=risk, m2=f2(t2), m3=f2(t3), rel=rel)


def graf(j, q, i=None, slut=None):
    """Kurs och OMXSPI (omräknat till aktiens kurs dagen före raset) från 20 dagar före raset till idag eller ut ur affären."""
    a = max(0, q - 20); b = min(T - 1, (slut if slut is not None else T - 1) + 3)
    c = C[a:b + 1, j]; ix = I[a:b + 1] / I[q - 1] * C[q - 1, j]
    return dict(d=[x.strftime("%Y-%m-%d") for x in dates[a:b + 1]], c=[f2(x) if np.isfinite(x) else None for x in c],
                ix=[f2(x) if np.isfinite(x) else None for x in ix], ras=q - a, in_=(i - a) if i is not None and i <= b else None,
                ut=(slut - a) if slut is not None and slut <= b else None)


ras, signaler = [], []
for j, t in enumerate(cols):
    c = C[:, j]
    for q in range(max(1, T - 70), T):
        if not (np.isfinite(c[q]) and np.isfinite(c[q - 1]) and np.isfinite(OMS[q, j]) and OMS[q, j] >= OMS_MIN): continue
        dr = c[q] / c[q - 1] - 1; ir = I[q] / I[q - 1] - 1
        if not (dr <= FALL and dr - ir <= REL): continue
        nn = nyh[(nyh.Ticker == t) & (nyh.Publicerat >= dates[q - 1]) & (nyh.Publicerat <= dates[q] + pd.Timedelta(hours=23))]
        orsak = "; ".join(f"{x.Typ.replace('_', ' ')}: {x.Rubrik}" for x in nn.head(2).itertuples())
        base = dict(t=t, namn=namn(t), rasdag=dates[q].strftime("%Y-%m-%d"), fall=f2(100 * dr), mot_index=f2(100 * (dr - ir)),
                    rasstang=f2(c[q]), orsak=orsak[:220], top80=bool(TOP80[q, j]))
        i = q + VANTA
        kopdagar = set(dates[q:min(i, T - 1) + 1].normalize()) & KOP.get(t, set())
        if i > T - 1:  # väntar
            sedan = c[T - 1] / c[q] - 1; isedan = I[T - 1] / I[q] - 1
            ras.append(dict(base, ch=graf(j, q), lage="Väntar", dag=T - 1 - q, kvar=i - (T - 1), kurs=f2(c[T - 1]), sedan=f2(100 * sedan),
                            rel=f2(100 * (sedan - isedan)), insyn=bool(kopdagar),
                            just_nu=("Insynsköp: utgår" if kopdagar else "Följer villkoren" if (c[T - 1] < c[q] and sedan < isedan) else "Uppfyller inte villkoren just nu")))
            continue
        sedan = c[i] / c[q] - 1; isedan = I[i] / I[q] - 1
        if kopdagar: lage = "Släppt: insynsköp"
        elif not (c[i] < c[q]): lage = "Släppt: över rasdagens stängning dag 5"
        elif not (sedan < isedan): lage = "Släppt: starkare än index sedan raset"
        else:
            stop0 = np.nanmax(c[q:i + 1]) * 1.01; risk = (stop0 - c[i]) / c[i]
            if risk > 0.08: lage = "Släppt: risken över 8 %"
            else:
                lage = "Signal"
                signaler.append(dict(base, signaldag=dates[i].strftime("%Y-%m-%d"), ingang=f2(c[i]), stopp=f2(stop0), mk_dd=f2(100 * MKDD[i]),
                                     i=i, j=j, q=q))
        ras.append(dict(base, ch=graf(j, q, i), lage=lage, dag=T - 1 - q, kurs=f2(c[T - 1]), sedan=f2(100 * sedan), rel=f2(100 * (sedan - isedan))))

# logg: signaler från testets start läggs till en gång (ingångsfälten ändras aldrig), utfallet räknas om varje körning
nu_str = dt.datetime.now(ZoneInfo("Europe/Stockholm")).strftime("%Y-%m-%d %H:%M")
logg = pd.read_csv(LOGG, dtype=str) if LOGG.exists() else pd.DataFrame(columns=["Ticker", "Signaldag", "Rasdag", "Ingang", "Stopp", "Marknad_dd", "Top80", "Loggad"])
finns = set(zip(logg.Ticker, logg.Signaldag))
nya = [s for s in signaler if pd.Timestamp(s["signaldag"]) >= START and (s["t"], s["signaldag"]) not in finns]
if nya:
    logg = pd.concat([logg, pd.DataFrame([dict(Ticker=s["t"], Signaldag=s["signaldag"], Rasdag=s["rasdag"], Ingang=s["ingang"], Stopp=s["stopp"],
                                               Marknad_dd=s["mk_dd"], Top80=s["top80"], Loggad=nu_str) for s in nya])], ignore_index=True)
    logg.to_csv(LOGG, index=False)
facit = []
for r in logg.itertuples():
    if r.Ticker not in cols or pd.Timestamp(r.Signaldag) not in dates: continue
    i, j = dates.get_loc(pd.Timestamp(r.Signaldag)), cols.index(r.Ticker)
    u = utfall(i, j, float(r.Ingang), float(r.Stopp))
    u["ch"] = graf(j, dates.get_loc(pd.Timestamp(r.Rasdag)) if pd.Timestamp(r.Rasdag) in dates else i - VANTA, i, u.get("slut_i"))
    facit.append(dict(t=r.Ticker, namn=namn(r.Ticker), signaldag=r.Signaldag, rasdag=r.Rasdag, ingang=float(r.Ingang), stopp0=float(r.Stopp),
                      mk_dd=float(r.Marknad_dd), top80=str(r.Top80) == "True", loggad=r.Loggad, **u))
# för sammanhang: senaste signalerna före start (ingår inte i facit)
fore = []
for s in signaler:
    if pd.Timestamp(s["signaldag"]) < START:
        u = utfall(s["i"], s["j"], s["ingang"], s["stopp"])
        u["ch"] = graf(s["j"], s["q"], s["i"], u.get("slut_i"))
        fore.append(dict(t=s["t"], namn=s["namn"], signaldag=s["signaldag"], rasdag=s["rasdag"], ingang=s["ingang"], stopp0=s["stopp"],
                         mk_dd=s["mk_dd"], top80=s["top80"], **u))
fore.sort(key=lambda x: x["signaldag"], reverse=True)


def sammanf(rader):
    kl = [r for r in rader if r["klar"]]
    if not kl: return dict(n=len(rader), klara=0)
    res = np.array([r["res"] for r in kl]); rel = [r["rel"] for r in kl if r.get("rel") is not None]
    return dict(n=len(rader), klara=len(kl), snitt=float(res.mean()), traff=float((res > 0).mean()), summa_R=float(sum(r["R"] for r in kl)),
                rel=float(np.mean(rel)) if rel else None, stopp=float(np.mean([r["ut"] == "Stopp" for r in kl])))


meta = dict(uppdaterad=nu_str, kursdag=dates[-1].strftime("%Y-%m-%d"), start=START.strftime("%Y-%m-%d"), mk_dd=float(MKDD[-1]),
            n_univ=int((OMS[-1] >= OMS_MIN).sum()), alla=sammanf(facit), topp=sammanf([r for r in facit if r["mk_dd"] > -10]),
            ned=sammanf([r for r in facit if r["mk_dd"] <= -10]), stora=sammanf([r for r in facit if r["top80"]]),
            sma=sammanf([r for r in facit if not r["top80"]]))
ras.sort(key=lambda x: x["rasdag"], reverse=True)
data = dict(meta=meta, facit=sorted(facit, key=lambda x: x["signaldag"], reverse=True), fore=fore[:15],
            vantar=[r for r in ras if r["lage"] == "Väntar"], slappta=[r for r in ras if r["lage"].startswith("Släppt")][:25])
mall = open(Path(__file__).resolve().parent / "ras_mall.html", encoding="utf-8").read()
Path(UT).write_text(mall.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, default=float)), encoding="utf-8")
print(f"Rasbevakning: {len(facit)} signaler i facit ({len(nya)} nya), {len(data['vantar'])} väntar på dag 5, "
      f"{len(fore)} signaler före start, kursdag {meta['kursdag']}, universum {meta['n_univ']} aktier")
