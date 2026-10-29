"""Bygger Trappscreen (poäng, varningar och grafer för handelstrappan) som en HTML-sida.

Läser volscreen_ohlc.csv och volscreen_info.csv (hämtas av trapp.yml / volhamta.py från Yahoo),
dessutom marknad.csv, insiderkop.csv, insiderhandel.csv, blankning.csv och ticker_map.csv.
Kör: python trapp_bygg.py [utfil]   (standard: /tmp/trappscreen/trappscreen.html)
Mallen är trappscreen_mall.html; platshållaren /*DATA*/ ersätts med datat som JSON.
"""
import json, re, sys
from pathlib import Path
import numpy as np, pandas as pd

UT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/trappscreen/trappscreen.html")
HER = Path(__file__).resolve().parent
try: NORDNET = pd.read_csv(HER / "nordnet_lankar.csv").set_index("Ticker").Url.to_dict()
except Exception: NORDNET = {}
FEE, MARG, N = 0.001, 0.002, 260
IDAG = pd.Timestamp.now(tz="Europe/Stockholm").tz_localize(None).normalize()

def clip(x, a, b): return max(a, min(b, x))

def serie(O_, H_, L_, C_, s=0.03, K=3):
    """Flytande trappa med K poster och steg s. Returnerar marknadsvärderat resultat (enheter av en post)."""
    lots, real, hi = [], 0.0, O_[0]
    for O, H, L, C in zip(O_, H_, L_, C_):
        bl = lots[-1] * (1 - s) if lots else hi * (1 - s)
        sl = lots[-1] * (1 + s) if lots else None
        canb, cans, sold = len(lots) < K, bool(lots), None
        if canb and O <= bl: lots.append(O)
        elif cans and O >= sl: p = lots.pop(); real += O / p - 1 - 2 * FEE; sold = O
        else:
            b = canb and L <= bl * (1 - MARG); x = cans and H >= sl * (1 + MARG)
            bf = (C >= O) if (b and x) else b
            if b and (not x or bf): lots.append(bl)
            elif x: p = lots.pop(); real += sl / p - 1 - 2 * FEE; sold = sl
        if sold is not None: hi = sold
        elif not lots and H >= hi: hi = H
    return real + sum(C_[-1] / p - 1 for p in lots)

def rsi(c, n=14):
    d = c.diff(); up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(); dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)

def norm(x):
    x = str(x).lower(); x = re.sub(r"\(.*?\)", "", x)
    x = re.sub(r"\b(aktiebolag|aktiebolaget|ab|publ|group|holding|ltd)\b", "", x)
    return re.sub(r"[^a-zåäö0-9]", "", x)

def nyckeltal(g, i):
    """Rörelse, studs, omsättning, trend och kvalitet för en aktie ur dess OHLC och Yahoo-info."""
    c = g.Close.values; pc = np.r_[np.nan, c[:-1]]
    rng = ((g.High - g.Low) / pc * 100).values[1:]
    n, ev, last = len(c), [], -99
    for k in range(130, n - 1):
        hi = c[k - 120:k + 1].max()
        if 0.8 * hi <= c[k] <= 0.9 * hi and k - last > 20 and c[k - 1] > 0.9 * hi:
            last = k; fw = c[k + 1:k + 91]
            if len(fw) < 30: continue
            ev.append((fw.max() >= hi, fw.min() < 0.75 * hi))
    sp = (i.ask - i.bid) / ((i.ask + i.bid) / 2) * 100 if i.bid and i.ask and i.ask > 0 else np.nan
    nd = (i.totalDebt - i.totalCash) / i.ebitda if i.ebitda and i.ebitda > 0 else np.nan
    return pd.Series(dict(
        Namn=i.longName, Sektor=i.sector, MC_Mdr=i.marketCap / 1e9 if pd.notna(i.marketCap) else np.nan,
        Rng250=round(rng[-250:].mean(), 2), Dagar25=round((rng[-250:] >= 2.5).mean() * 100),
        Oms=round(np.median(c[-60:] * g.Volume.values[-60:]) / 1e6, 0),
        Dips=len(ev), Aterh=sum(e[0] for e in ev), Krasch=sum(e[1] for e in ev),
        Marg=round(i.profitMargins * 100, 1), DE=i.debtToEquity, NettoSkuld_EBITDA=nd,
        Spread=round(sp, 2) if sp == sp else np.nan))

def las_stod():
    """Läser stöddata (ticker_map, blankning, insider) som poängsättningen behöver."""
    tm = pd.read_csv(HER / "ticker_map.csv").drop_duplicates("Ticker").set_index("Ticker")
    bl = pd.read_csv(HER / "blankning.csv"); bl["k"] = bl.Emittent.map(norm)
    h = pd.read_csv(HER / "insiderhandel.csv", parse_dates=["Publiceringsdatum", "Transaktionsdatum"])
    h = h[(h.Instrumenttyp == "Aktie") & (h.Valuta == "SEK") & h.Karaktär.isin(["Förvärv", "Avyttring"])].copy()
    h["belopp"] = h.Volym * h.Pris
    ik = pd.read_csv(HER / "insiderkop.csv", parse_dates=["Publiceringsdatum", "Transaktionsdatum"])
    ik = ik[(ik.Intern_overforing != True) & (ik.Indirekt != True) & (ik.Valuta == "SEK")].copy()  # Typ_antagen är bara en flagga för gamla rader utan instrumenttyp
    return tm, bl, h, ik

def las_utdelning():
    """Avskiljandedagar (historik och kommande) från volscreen_utdelning.csv, skapad av volhamta.py. Saknas filen görs ingen justering."""
    f = HER / "volscreen_utdelning.csv"
    try:
        return pd.read_csv(f, parse_dates=["ExDatum"]) if f.exists() else pd.DataFrame(columns=["Ticker", "ExDatum", "Belopp", "Typ"])
    except Exception:
        return pd.DataFrame(columns=["Ticker", "ExDatum", "Belopp", "Typ"])

def justera_utdelning(g, dv):
    """Justerar Open/High/Low/Close bakåt för utdelningar (faktor 1 - utdelning/kursen före avskiljandedagen), så att utdelningsfallet inte räknas som ett kursfall.
    g har radindex 0..n-1. Returnerar (justerad kopia, lista med (datum, belopp, procent av kursen) för varje justerad avskiljandedag)."""
    g = g.copy(); f = np.ones(len(g)); lista = []
    for r in dv[dv.Typ == "historik"].sort_values("ExDatum").itertuples():
        pos = np.flatnonzero(g.Datum.values >= np.datetime64(r.ExDatum))
        if len(pos) == 0 or pos[0] == 0: continue
        k = int(pos[0]); prev = float(g.Close.iloc[k - 1])
        if not (prev > 0 and r.Belopp and r.Belopp > 0) or r.Belopp / prev > 0.25: continue  # orimliga belopp (t.ex. fel enhet) justeras inte
        f[:k] *= 1 - r.Belopp / prev; lista.append((str(r.ExDatum.date()), float(r.Belopp), round(float(r.Belopp / prev * 100), 2)))
    for col in ("Open", "High", "Low", "Close"): g[col] = g[col].values * f
    return g, lista

def analysera(o, inf, tm, bl, h, ik, div=None):
    """Poäng, varningar och trappdata per aktie. o = OHLC (Datum, Ticker, ...), inf = info indexerad på Ticker.
    Kurserna justeras för utdelningar (div, standard volscreen_utdelning.csv) så att en avskiljandedag inte ser ut som ett fall."""
    out = []
    div = las_utdelning() if div is None else div
    for t, g in o.groupby("Ticker"):
        g = g.dropna(subset=["Close", "High", "Low", "Open"]).drop_duplicates("Datum").reset_index(drop=True)
        if len(g) < 500 or t not in inf.index: continue
        dvt = div[div.Ticker == t]; g, utj = justera_utdelning(g, dvt)
        i = inf.loc[t]; x = nyckeltal(g, i); c = g.Close
        ma50, ma200, r = c.rolling(50).mean(), c.rolling(200).mean(), rsi(c)
        last = c.iloc[-1]; hi20 = g.High.tail(20).max(); hi120 = g.High.tail(120).max(); lo252 = g.Low.tail(252).min()
        ret1 = (last / c.iloc[-2] - 1) * 100; ret5 = (last / c.iloc[-6] - 1) * 100
        ned = 0
        for k in range(1, 10):
            if c.iloc[-k] < c.iloc[-k - 1]: ned += 1
            else: break
        v21 = g.Volume.tail(21).iloc[:-1].mean(); vr = g.Volume.iloc[-1] / v21 if v21 > 0 else np.nan
        isin = tm.ISIN.get(t)
        r90 = h[(h.ISIN == isin) & (h.Publiceringsdatum >= IDAG - pd.Timedelta(days=90))] if isin == isin else h.iloc[0:0]
        sal = r90[r90.Karaktär == "Avyttring"]
        kop = ik[(ik.ISIN == isin) & (ik.Publiceringsdatum >= IDAG - pd.Timedelta(days=90))].rename(columns={"Belopp": "belopp"})
        namn = tm.Bolag.get(t, x.Namn); b = bl[(bl.k == norm(namn)) | (bl.k == norm(i.longName))]
        blank = float(b.Niva.iloc[0]) if len(b) else None; blank_f = float(b.Forandring_20d.iloc[0]) if len(b) else None
        rng = x.Rng250; s = 2.5 if rng < 2.6 else 3.0 if rng < 3.3 else 3.5 if rng < 4.0 else 4.0
        gg = g.tail(750)
        m = serie(gg.Open.values, gg.High.values, gg.Low.values, gg.Close.values, s / 100, 3)
        trappa_ar = m / 3 / (len(gg) / 250) * 100; bh3 = (c.iloc[-1] / gg.Open.iloc[0] - 1) * 100
        # --- poäng (passform 0-100)
        P = {}
        P["rorelse"] = 20 if 2.0 <= rng <= 4.0 else (8 + 12 * (rng - 1.5) / 0.5 if 1.5 <= rng < 2.0 else (20 - 12 * (rng - 4.0) if 4.0 < rng <= 5.0 else 4))
        dips = x.Dips; andel = x.Aterh / dips if dips else 0; kr = x.Krasch / dips if dips else 1
        P["studs"] = 20 * (0.65 * clip(andel / 0.8, 0, 1) + 0.35 * (1 - clip(kr / 0.5, 0, 1))) * min(1, dips / 6)
        ma2 = (last / ma200.iloc[-1] - 1) * 100
        gro = i.earningsGrowth if pd.notna(i.earningsGrowth) else (1 if i.trailingEps > 0 else -1)
        P["trend"] = (0 if ma2 < 0 else 5 + 5 * clip(ma2 / 10, 0, 1)) + (5 if gro > 0 else 0)
        fin = x.Sektor == "Financial Services"
        mc = x.MC_Mdr if pd.notna(x.MC_Mdr) else 100
        de_ok = fin or pd.isna(x.DE) or x.DE <= 110; nd_ok = fin or pd.isna(x.NettoSkuld_EBITDA) or x.NettoSkuld_EBITDA <= 3
        P["kvalitet"] = (4 * clip((mc - 5) / 45, 0, 1) + 4 * clip(x.Marg / 5, 0, 1) + (4 if de_ok else 0) + (4 if nd_ok else 0)
                         + (4 if (pd.notna(i.returnOnEquity) and i.returnOnEquity >= 0.10) else 0))
        sp = x.Spread
        P["likviditet"] = (5 if x.Oms >= 100 else 2.5 if x.Oms >= 15 else 0) + (5 if (pd.notna(sp) and sp <= 0.15) else 2.5 if (pd.notna(sp) and sp <= 0.30) else 0)
        dr = (i.Nasta_rapport - IDAG).days if pd.notna(i.Nasta_rapport) else None
        nyss = dr is not None and dr <= 0
        if nyss: dr = None
        P["rapport"] = 12 if nyss else 8 if dr is None else 15 if dr >= 21 else 11 if dr >= 14 else 6 if dr >= 8 else 0
        fit = round(sum(P.values()))
        if not (de_ok and nd_ok): fit = min(fit, 70)
        if mc < 10: fit = min(fit, 80)
        # --- varningar
        W, U = [], []
        if not (de_ok and nd_ok):
            W.append(("Hög skuld", f"Skuld/eget kapital {x.DE:.0f} %" + (f", nettoskuld {x.NettoSkuld_EBITDA:.1f}× EBITDA" if pd.notna(x.NettoSkuld_EBITDA) else "") + ". Kravet på låg skuld klaras inte.", "hard"))
        if mc < 10: W.append((f"Börsvärde {mc:.0f} Mdr", "Under 10 miljarder, mindre och känsligare bolag.", "mjuk"))
        if last < ma200.iloc[-1]: W.append(("Under MA200", "Kursen ligger under 200-dagarssnittet, kravet på uppåttrend håller inte.", "hard"))
        if dr is not None and dr <= 7: W.append((f"Rapport om {dr} d", "Kvartalsrapporten kommer inom en vecka. Pausa nya poster.", "hard"))
        elif dr is not None and dr <= 14: W.append((f"Rapport om {dr} d", "Rapporten är nära. Planera att trappan är tom eller säkrad innan.", "mjuk"))
        if ret5 <= -8: W.append((f"{ret5:.0f} % på 5 d", "Snabbt fall, trappan fylls fort.", "hard"))
        if ned >= 4 and ret5 < -4: W.append((f"{ned} nedåtdagar i rad", "Fallet fortsätter, vänta på en vändning innan nya poster.", "mjuk"))
        if last <= lo252 * 1.03: W.append(("Nära 52-veckorslägsta", "Kursen ligger högst 3 % över årslägsta.", "hard"))
        elif last < hi120 * 0.80: W.append((f"{(last / hi120 - 1) * 100:.0f} % från 6-mån-topp", "Fallet är djupare än 20 %, utanför studsprofilen.", "mjuk"))
        if blank is not None and blank >= 5: W.append((f"Blankat {blank:.1f} %", "Stor blankning" + (", ökar" if blank_f and blank_f >= 0.5 else "") + ".", "mjuk"))
        if sal.belopp.sum() >= 1e6: W.append((f"Insiderförsäljning {sal.belopp.sum() / 1e6:.1f} MSEK", "Ledande befattningshavare sålde senaste 90 dagarna.", "mjuk"))
        if vr and vr >= 2 and ret1 <= -3: W.append((f"Stor volym nedåt ({vr:.1f}×)", "Fall på hög omsättning.", "hard"))
        # utdelning: kommande avskiljandedag och nyligen passerad (kursfallet den dagen är utdelningen, signalerna ovan är utdelningsjusterade)
        utd = None
        if len(dvt):
            kom = dvt[(dvt.Typ == "kommande") & (dvt.ExDatum >= IDAG)].sort_values("ExDatum")
            sen = utj[-1] if utj else None
            nd = int((kom.ExDatum.iloc[0] - IDAG).days) if len(kom) else None
            hist_ = dvt[dvt.Typ == "historik"].sort_values("ExDatum")  # beloppet uppskattas från senaste utdelningen i handelsvalutan (Yahoos lastDividendValue kan vara i annan valuta)
            nb = float(hist_.Belopp.iloc[-1]) if len(kom) and len(hist_) else None
            npct = round(nb / float(last) * 100, 1) if nb else None
            utd = dict(senast=sen[0] if sen else None, s_belopp=sen[1] if sen else None, s_pct=sen[2] if sen else None, s_dagar=int((IDAG - pd.Timestamp(sen[0])).days) if sen else None,
                       nasta=str(kom.ExDatum.iloc[0].date()) if len(kom) else None, n_dagar=nd, n_belopp=nb, n_pct=npct, just=len(utj))
            if nd is not None and nd <= 10 and (npct is None or npct >= 0.8):
                W.append((f"Avskiljandedag om {nd} d" + (f" (ca {npct:.1f} %)" if npct else ""), "Utdelningen tas bort ur kursen den dagen" + (f" (ca {nb:.2f} kr om den blir som senast)" if nb else "") + ". Det är inget fall att köpa i trappan.", "mjuk"))
            if sen and utd["s_dagar"] <= 7 and sen[2] >= 1.0:
                W.append((f"Avskiljandedag {sen[0]}: {sen[2]:.1f} %", "Kursfallet den dagen var utdelningen. Fall, dopp, RSI och nedåtdagar ovan är räknade på utdelningsjusterade kurser.", "mjuk"))
        if kop.belopp.sum() >= 250000: U.append((f"Insiderköp {kop.belopp.sum() / 1e6:.1f} MSEK", f"Ledande befattningshavare köpte senaste 90 dagarna ({len(kop)} köp)."))
        dopp = (last / hi20 - 1) * 100
        if ma2 > 0 and -12 <= dopp <= -5: U.append((f"Dopp {dopp:.0f} % i uppåttrend", "Kursen är nedtryckt från 20-dagarstoppen men över MA200: typiskt trappaläge."))
        rr = r.iloc[-6:]
        if rr.min() < 35 and r.iloc[-1] > 35 and ret1 > 0: U.append(("RSI vänder upp", "RSI har gått upp över 35 efter en svag period."))
        if last > ma50.iloc[-1] and (c.iloc[-11:-1] < ma50.iloc[-11:-1]).any() and ma2 > 0: U.append(("Tillbaka över MA50", "Kursen har tagit sig över 50-dagarssnittet igen."))
        if blank_f is not None and blank_f <= -0.5: U.append((f"Blankning minskar {blank_f:+.1f} pp", "Blankarna täcker."))
        if last >= hi20 * 0.998 and vr and vr >= 1.5 and ma2 > 0: U.append(("Utbrott på volym", "Ny 20-dagarstopp på högre omsättning."))
        out.append(dict(
            t=t, nordnet=NORDNET.get(t), namn=re.sub(r"\s*\(publ\.?\)", "", str(i.longName)).strip(), sektor=x.Sektor, mc=round(float(mc), 1), kurs=round(float(last), 2),
            d1=round(float(ret1), 1), d5=round(float(ret5), 1), fit=fit, P={k: round(v, 1) for k, v in P.items()}, rng=round(float(rng), 2),
            dagar25=int(x.Dagar25), dips=int(dips), aterh=int(x.Aterh), krasch=int(x.Krasch), ma200=round(float(ma2), 1), dopp=round(float(dopp), 1),
            rsi=round(float(r.iloc[-1])), oms=float(x.Oms), marg=float(x.Marg), spread=None if pd.isna(sp) else float(sp),
            de=None if pd.isna(x.DE) else round(float(x.DE)), nd=None if pd.isna(x.NettoSkuld_EBITDA) else float(x.NettoSkuld_EBITDA),
            rapport=dr, rapport_nyss=nyss, blank=blank, blank_f=blank_f, kop=round(float(kop.belopp.sum())), sal=round(float(sal.belopp.sum())),
            utd=utd, steg=s, niv=[round(float(last * (1 - s / 100 * k)), 2 if last < 1000 else 0) for k in (1, 2, 3)],
            trappa_ar=round(float(trappa_ar), 1), bh3=round(float(bh3), 0), fin=bool(fin),
            ned=[dict(l=a, t=b_, sev=c_) for a, b_, c_ in W], upp=[dict(l=a, t=b_) for a, b_ in U]))
    out.sort(key=lambda z: -z["fit"])
    return out

def main():
    o = pd.read_csv(HER / "volscreen_ohlc.csv", parse_dates=["Datum"]).sort_values(["Ticker", "Datum"])
    inf = pd.read_csv(HER / "volscreen_info.csv", parse_dates=["Nasta_rapport"]).set_index("Ticker")
    tm, bl, h, ik = las_stod()
    mk = pd.read_csv(HER / "marknad.csv", parse_dates=["Datum"])
    div = las_utdelning()
    out = analysera(o, inf, tm, bl, h, ik, div)

    # --- grafdata: samma kalender för alla
    cal = sorted(set().union(*[set(g.Datum.tail(N)) for _, g in o.groupby("Ticker")]))[-N:]; ci = pd.DatetimeIndex(cal)
    def ev(df, isin, typ):
        xx = df[df.ISIN == isin].copy()
        if xx.empty: return []
        td = xx.Transaktionsdatum.where((xx.Transaktionsdatum <= xx.Publiceringsdatum) & (xx.Transaktionsdatum >= xx.Publiceringsdatum - pd.Timedelta(days=400)), xx.Publiceringsdatum)
        xx["D"] = td; xx = xx[xx.D >= ci[0]]; res = []
        for dt, gx in xx.groupby("D"):
            k = min(int(ci.searchsorted(dt)), len(ci) - 1)
            res.append([k, typ, round(float(gx.Belopp.sum() if "Belopp" in gx else gx.belopp.sum())), len(gx), int((gx.Publiceringsdatum.max() - dt).days)])
        return res
    sal_all = h[h.Karaktär == "Avyttring"].rename(columns={"belopp": "Belopp"})
    for z in out:
        t = z["t"]; g = o[o.Ticker == t].drop_duplicates("Datum").set_index("Datum").sort_index()
        m50 = g.Close.rolling(50).mean().reindex(ci).ffill(); m200 = g.Close.rolling(200).mean().reindex(ci).ffill(); rg = g.reindex(ci).ffill()
        rd = lambda s_, k=2: [None if pd.isna(v) else round(float(v), k) for v in s_]
        isin = tm.ISIN.get(t)
        ud = []
        for r in div[(div.Ticker == t) & (div.Typ == "historik") & (div.ExDatum >= ci[0])].itertuples():
            fore = g.Close[g.index < r.ExDatum]
            if r.ExDatum in g.index and len(fore) and fore.iloc[-1] > 0:
                ud.append([min(int(ci.searchsorted(r.ExDatum)), len(ci) - 1), "U", round(float(r.Belopp), 2), round(float(r.Belopp / fore.iloc[-1] * 100), 1), 0])
        z["ch"] = dict(o=rd(rg.Open), h=rd(rg.High), l=rd(rg.Low), c=rd(rg.Close), v=[int(v) if v == v else 0 for v in g.Volume.reindex(ci).fillna(0)],
                       m50=rd(m50), m200=rd(m200), ev=((ev(ik.rename(columns={"Belopp": "Belopp"}), isin, "K") + ev(sal_all, isin, "S")) if isin == isin else []) + ud)

    mm = mk.tail(N)
    marknad = dict(datum=str(mk.Datum.iloc[-1].date()), trend=float(mk.Trend.iloc[-1]), trend_ig=float(mk.Trend.iloc[-2]), orolig=float(mk.P_orolig.iloc[-1]),
                   vandning=str(mk.Vandning.iloc[-1]) if isinstance(mk.Vandning.iloc[-1], str) else "", index=float(mk.Index.iloc[-1]),
                   serie=[[str(d.date()), round(float(v), 2), round(float(ix), 1)] for d, v, ix in zip(mm.Datum, mm.Trend, mm.Index)])
    try:  # makrodagar (räntebesked, inflation) med källa i makrodagar.csv: visas i marknadsrutan som förvarning
        mk_ = pd.read_csv(HER / "makrodagar.csv", parse_dates=["Datum"]) if (HER / "makrodagar.csv").exists() else pd.DataFrame()
        marknad["makro"] = [dict(d=str(r.Datum.date()), dg=int((r.Datum - IDAG).days), h=r.Handelse, typ=r.Typ, k=r.Kalla) for r in mk_[mk_.Datum >= IDAG].sort_values("Datum").head(6).itertuples()] if len(mk_) else []
    except Exception:
        marknad["makro"] = []
    data = dict(marknad=marknad, aktier=out, genererad=str(IDAG.date()), datum=[str(d.date()) for d in ci])
    html = (HER / "trappscreen_mall.html").read_text(encoding="utf-8").replace("/*DATA*/", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    UT.parent.mkdir(parents=True, exist_ok=True); UT.write_text(html, encoding="utf-8")
    nf = sum(1 for z in out if any(w["sev"] == "hard" for w in z["ned"]))
    print(f"{len(out)} aktier, senaste kurs {data['datum'][-1]}, marknadsmått {marknad['trend']:+.1f} ({marknad['datum']}), {nf} med hård varning -> {UT} ({UT.stat().st_size / 1e3:.0f} kB)")

if __name__ == "__main__":
    main()
