"""Bygger Sektortracker: vilka segment på Stockholmsbörsen (och sektor-ETF:er i USA) har medvind just nu, och hur väl en enkel regel
("håll de starkaste segmenten") har fungerat historiskt på våra data.

Underlag: kurser.csv (dagskurser), sektor_info.csv (Yahoo: sektor och bransch per bolag), sektor_etf.csv (USA-fonder), alla ur repot.
Segment = lika viktat index av likvida bolag (median omsättning minst 0,5 MSEK/dag, vald på senaste 90 dagarna, alltså med survivorship-bias). Universum = bolagen i kurser.csv, som kurser.py bygger på insiderkop.csv: bara bolag med insiderköp sedan 2016 och Yahoo-ticker på .ST (inte hela börsen).
Kör:  python sektor_bygg.py [utfil]     (standard /tmp/sektor/sektor.html)
Skriver rader som börjar med KONTROLL: när något behöver ses över (för få bolag, saknad sektor, gammal kursdag). Ändrar aldrig indata."""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HER = Path(__file__).resolve().parent
UT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/sektor/sektor.html")
MIN_OMS = 0.5e6
MIN_BOLAG = 4            # minsta antal bolag med kurs för att ett segment ska räknas den dagen
DAG = dict(m1=21, m3=63, m6=126, m12=252)
KOST = 0.003             # 0,3 % per omsatt andel (enkelriktat), grov uppskattning för korgar av små och medelstora bolag
ETFNAMN = {"XLK": "Teknik", "XLV": "Hälsovård", "XLI": "Industri", "XLF": "Finans", "XLE": "Energi", "XLY": "Sällanköpsvaror",
           "XLP": "Dagligvaror", "XLU": "Kraft och vatten", "XLB": "Material", "XLRE": "Fastigheter", "XLC": "Media och telekom",
           "XBI": "Biotech", "SMH": "Halvledare"}
ETF_SEKTOR = [t for t in ETFNAMN if t not in ("XBI", "SMH")]      # de elva sektorerna; XBI och SMH visas som extra rader
SEKTORER = {"Industrials": "Industri", "Technology": "Teknik", "Consumer Cyclical": "Sällanköpsvaror", "Consumer Defensive": "Dagligvaror",
            "Basic Materials": "Material", "Real Estate": "Fastigheter", "Communication Services": "Media och telekom",
            "Energy": "Energi", "Utilities": "Kraft och vatten"}


def segment(sector, industry):
    s, i = str(sector or ""), str(industry or "")
    if s == "Healthcare":
        return "Biotech och läkemedel" if any(w in i for w in ("Biotech", "Drug", "Pharma")) else "Medtech och vård"
    if s == "Financial Services":
        return "Banker" if i.startswith("Banks") else "Finans övrigt"
    return SEKTORER.get(s)


def sv_pct(x):
    return None if x is None or pd.isna(x) else round(float(x) * 100, 1)


# ---------------------------------------------------------------- data
def las_kurser():
    k = pd.read_csv(HER / "kurser.csv", usecols=["Datum", "Ticker", "Stang", "Volym"], parse_dates=["Datum"])
    omx = k[k.Ticker == "^OMXSPI"].set_index("Datum").Stang.sort_index()
    k = k[k.Ticker.str.endswith(".ST")].copy()
    k["Oms"] = k.Stang * k.Volym
    r = k[k.Datum > k.Datum.max() - pd.Timedelta(days=90)]
    med, n = r.groupby("Ticker").Oms.median(), r.groupby("Ticker").Datum.count()
    liq = set(med[(med >= MIN_OMS) & (n.reindex(med.index) >= 40)].index)
    px = k[k.Ticker.isin(liq)].pivot_table(index="Datum", columns="Ticker", values="Stang").sort_index()
    return px, omx


def rensa_pris(px):
    """Tar bort dagsrörelser som ser ut som en ojusterad split (över 40 % och kvoten ligger nära ett heltal eller 1/heltal) och bygger om
    kurserna ur de rensade avkastningarna. Andra stora rörelser, som verkliga raser eller uppgångar på nyheter, behålls."""
    r = px.ffill().pct_change(fill_method=None)
    q = (1 + r).to_numpy(dtype=float)
    k = np.arange(2, 21)
    q0 = np.nan_to_num(q, nan=1.0)[..., None]
    nara = (np.abs(q0 / k - 1).min(axis=-1) < 0.03) | (np.abs(q0 * k - 1).min(axis=-1) < 0.03)
    flagga = (np.abs(np.nan_to_num(r.to_numpy(dtype=float), nan=0.0)) > 0.4) & nara
    r = r.mask(flagga, 0.0)
    return (1 + r.fillna(0)).cumprod().where(px.notna()), int(flagga.sum())


def manadsindex(px, min_bolag=MIN_BOLAG, trim=(-0.5, 1.0)):
    """Lika viktat index med månadsvis ombalansering: inom en månad håller man bolagen, vid månadsskiftet vägs de lika igen.
    Daglig ombalansering skulle överdriva avkastningen i små, svårhandlade bolag. Enskilda bolags månadsrörelse trimmas till -50 % och +100 %.
    Returnerar (dagligt index, månadsavkastning per månadsslut)."""
    per = px.index.to_period("M")
    me = px.groupby(per).last()
    prev = me.shift(1)
    mret = (me / prev - 1).clip(*trim)
    mr = mret.mean(axis=1).where(mret.notna().sum(axis=1) >= min_bolag)
    kedja = (1 + mr.fillna(0)).cumprod().shift(1).fillna(1.0)
    rel = (px / prev.reindex(per).set_axis(px.index)).clip(1 + trim[0], 1 + trim[1])
    lvl = rel.mean(axis=1).where(rel.notna().sum(axis=1) >= min_bolag)
    idx = kedja.reindex(per).set_axis(px.index) * lvl
    mr.index = mr.index.to_timestamp(how="end").normalize()
    return idx.where(idx.notna().cummax()), mr


# ---------------------------------------------------------------- mått
def mått(idx, uni):
    """Mått för ett index (Series) mot ett jämförelseindex. Alla avkastningar i bråkform."""
    idx, uni = idx.dropna(), uni.reindex(idx.dropna().index)
    n = len(idx)
    if n < DAG["m12"] + 30:
        return None
    s = idx.iloc[-1]

    def ret(d):
        return s / idx.iloc[-1 - d] - 1

    def rel(d):
        return (s / idx.iloc[-1 - d]) / (uni.iloc[-1] / uni.iloc[-1 - d]) - 1

    m121 = idx.iloc[-1 - DAG["m1"]] / idx.iloc[-1 - DAG["m12"]] - 1
    u121 = uni.iloc[-1 - DAG["m1"]] / uni.iloc[-1 - DAG["m12"]] - 1
    sma50, sma200 = idx.rolling(50).mean().iloc[-1], idx.rolling(200).mean().iloc[-1]
    return dict(r1=ret(DAG["m1"]), r3=ret(DAG["m3"]), r6=ret(DAG["m6"]), r12=ret(DAG["m12"]),
                rel1=rel(DAG["m1"]), rel3=rel(DAG["m3"]), rel6=rel(DAG["m6"]), rel121=(1 + m121) / (1 + u121) - 1,
                over200=bool(s > sma200), over50_200=bool(sma50 > sma200), dist200=s / sma200 - 1)


def lage(comp, over200, bredd200, rel6):
    if comp >= 0.6 and over200 and (rel6 or 0) > 0 and (bredd200 is None or bredd200 >= 0.5):
        return "Medvind"
    if comp <= 0.4 and not over200:
        return "Motvind"
    return "Blandat"


def rs_datum(idx, veckor=52):
    """Gemensamma veckodatum (var femte handelsdag, senaste dagen med) för relativa styrkelinjer."""
    return list(idx.index[::-1][: 5 * veckor + 1 : 5][::-1])


def rs_linje(idx, uni, datum):
    rs = (idx / uni).reindex(datum)
    if rs.isna().all():
        return []
    f = rs.dropna().iloc[0]
    return [None if pd.isna(v) else round(float(v / f * 100), 1) for v in rs.tolist()]


# ---------------------------------------------------------------- test (månadsbaserat, som i litteraturen)
def manadsavk(px):
    m = px.resample("ME").last()
    m = m.where(px.resample("ME").count() > 0)
    if m.index[-1] > px.index.max() + pd.Timedelta(days=3):          # senaste månaden är inte slut
        m = m.iloc[:-1]
    return m.pct_change(fill_method=None)


def statistik(r, namn):
    r = r.dropna()
    n = len(r)
    if n < 24:
        return None
    ar = (1 + r).prod() ** (12 / n) - 1
    vol = r.std() * np.sqrt(12)
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    return dict(namn=namn, manader=int(n), cagr=sv_pct(ar), vol=sv_pct(vol), sharpe=round(float(ar / vol), 2) if vol > 0 else None, maxdd=sv_pct(dd))


def kor_test(M, uni, L=12, N=3, trend=False, kost=KOST):
    """M: månadsavkastning per segment. Signal vid månadsskiftet m (avkastning månaderna m-L+1..m-1, alltså senaste månaden hoppas över),
    innehav månad m+1: de N starkaste, lika vikt. trend=True: en plats ersätts av kassa om segmentets index är under sitt 10-månaderssnitt."""
    C = (1 + M.fillna(0)).cumprod().where(M.notna().cummax())
    sma = C.rolling(10, min_periods=10).mean()
    mom = (1 + M).rolling(L - 1, min_periods=L - 1).apply(np.prod, raw=True).shift(1) - 1
    ut, vikt_f = {}, pd.Series(dtype=float)
    for i in range(len(M) - 1):
        d, nasta = M.index[i], M.index[i + 1]
        sig = mom.loc[d].dropna()
        sig = sig[M.loc[nasta].reindex(sig.index).notna()]
        if len(sig) < N + 2:
            continue
        val = sig.sort_values(ascending=False).index[:N]
        if trend:
            val = [v for v in val if C.loc[d, v] > sma.loc[d, v]]
        w = pd.Series(1.0 / N, index=val)
        omsatt = w.sub(vikt_f, fill_value=0).abs().sum()
        ut[nasta] = float((w * M.loc[nasta].reindex(w.index)).sum()) - kost * omsatt
        vikt_f = w
    return pd.Series(ut)


def testblock(M, uni, omx_m, benamn, kost=KOST):
    res = {}
    res["a"] = kor_test(M, uni, 12, 3, False, kost)
    res["b"] = kor_test(M, uni, 12, 3, True, kost)
    res["k6"] = kor_test(M, uni, 6, 3, False, kost)
    res["lag"] = None
    start = res["a"].index.min()
    snitt = M.mean(axis=1).loc[start:]
    ref = uni.loc[start:]
    kurvor = pd.DataFrame({"a": res["a"], "b": res["b"], "alla": snitt, "ref": ref}).dropna(subset=["a"])
    if omx_m is not None:
        kurvor["omx"] = omx_m.reindex(kurvor.index)
    tab = [statistik(res["a"], "De 3 starkaste (12−1 mån)"), statistik(res["b"], "…och bara över 10-månaderssnittet"),
           statistik(res["k6"].reindex(res["a"].index), "De 3 starkaste (6−1 mån)"), statistik(snitt.reindex(res["a"].index), "Alla segment, lika vikt"),
           statistik(ref.reindex(res["a"].index), benamn)]
    if omx_m is not None:
        tab.append(statistik(omx_m.reindex(res["a"].index), "OMXSPI (kursindex)"))
    tab = [t for t in tab if t]
    # rörlighet: slår regeln snittet i fler månader än hälften, och hur stor är skillnaden
    diff = (res["a"] - ref.reindex(res["a"].index)).dropna()
    t = float(diff.mean() / (diff.std() / np.sqrt(len(diff)))) if len(diff) > 2 and diff.std() > 0 else None
    # robusthet: samma test för flera lookbacks och antal segment
    rob = []
    for L in (3, 6, 12):
        for N in (2, 3, 4):
            rr = kor_test(M, uni, L, N, False, kost)
            if len(rr) > 24:
                k = (1 + rr).prod() ** (12 / len(rr)) - 1
                b = (1 + ref.reindex(rr.index)).prod() ** (12 / len(rr)) - 1
                rob.append(dict(L=L, N=N, extra=sv_pct(k - b)))
    seg = pd.DataFrame({"a": res["a"], "alla": snitt.reindex(res["a"].index)}).dropna()
    return dict(
        ref_namn=benamn, start=str(start.date()), slut=str(res["a"].index.max().date()), manader=int(len(res["a"])),
        tabell=tab, tstat=round(t, 2) if t is not None else None, andel_over=sv_pct((diff > 0).mean()), robust=rob,
        kurva=dict(datum=[str((kurvor.index[0] - pd.offsets.MonthEnd(1)).date())] + [str(d.date()) for d in kurvor.index],
                   **{k: [100.0] + [round(float(v), 1) for v in (100 * (1 + kurvor[k].fillna(0)).cumprod()).tolist()] for k in kurvor.columns}))


def viktat_test(px, mapp, info, segs):
    """Känslighetskontroll: samma test (12-1, de tre starkaste, månadsvis) men med börsvärdesvikter i stället för lika vikter.
    Vikterna är dagens börsvärde (Yahoo har inga historiska), alltså med framåtblick. Saknas börsvärde används segmentets median."""
    try:
        mc = info.set_index("Ticker").marketCap.astype(float)
        w = pd.Series({t: mc.get(t, np.nan) for t in px.columns})
        for g in segs:
            k = [t for t in px.columns if mapp[t] == g]
            w[k] = w[k].fillna(w[k].median())
        w = w.fillna(w.median())
        me = px.groupby(px.index.to_period("M")).last()
        mret = (me / me.shift(1) - 1).clip(-0.5, 1.0)
        mret.index = mret.index.to_timestamp(how="end").normalize()

        def agg(cols):
            r = mret[cols]
            ww = pd.DataFrame(np.tile(w[cols].values, (len(r), 1)), index=r.index, columns=cols).where(r.notna())
            return ((r * ww).sum(axis=1) / ww.sum(axis=1)).where(r.notna().sum(axis=1) >= MIN_BOLAG)

        M = pd.DataFrame({g: agg([t for t in px.columns if mapp[t] == g]) for g in segs})
        uni = agg(list(px.columns))
        M = M.loc[M.notna().sum(axis=1) >= 8]
        uni = uni.reindex(M.index)
        if M.index[-1] > px.index.max() + pd.Timedelta(days=3):
            M, uni = M.iloc[:-1], uni.iloc[:-1]
        r = kor_test(M, uni, 12, 3, False, KOST)
        a, b = statistik(r, ""), statistik(uni.reindex(r.index), "")
        return dict(a=a["cagr"], alla=b["cagr"]) if a and b else None
    except Exception as ex:          # kontrollen är extra, bygget ska aldrig falla på den
        print("viktat_test misslyckades:", ex)
        return None


# ---------------------------------------------------------------- main
def main():
    px, omx = las_kurser()
    info = pd.read_csv(os.environ.get("SEKTOR_INFO") or HER / "sektor_info.csv")
    info["seg"] = [segment(s, i) for s, i in zip(info.sector, info.industry)]
    kontroll = []
    saknas = sorted(set(px.columns) - set(info[info.seg.notna()].Ticker))
    if saknas:
        kontroll.append(f"KONTROLL: {len(saknas)} likvida bolag saknar sektor i sektor_info.csv ({', '.join(saknas[:8])}{'…' if len(saknas) > 8 else ''})")
    namn = info.set_index("Ticker").longName.to_dict()
    mapp = info[info.seg.notna()].set_index("Ticker").seg.to_dict()
    px = px[[t for t in px.columns if t in mapp]]
    kursdag = px.index.max()
    if (pd.Timestamp.now().normalize() - kursdag).days > 4:
        kontroll.append(f"KONTROLL: senaste svenska kursdag är {kursdag.date()}")

    px, n_split = rensa_pris(px)
    uni_idx, uni_mr = manadsindex(px)
    segs = sorted(set(mapp.values()))
    utelamnade = [f"{g} ({sum(1 for t in px.columns if mapp[t] == g)} bolag)" for g in segs if sum(1 for t in px.columns if mapp[t] == g) < MIN_BOLAG]
    segs = [g for g in segs if sum(1 for t in px.columns if mapp[t] == g) >= MIN_BOLAG]
    se_datum = rs_datum(uni_idx)
    seg_idx, seg_mr, seg_n, rad = {}, {}, {}, []
    for sgm in segs:
        kol = [t for t in px.columns if mapp[t] == sgm]
        seg_idx[sgm], seg_mr[sgm] = manadsindex(px[kol])
        seg_n[sgm] = len(kol)
    # bredd och ledare
    sma50, sma200 = px.rolling(50, min_periods=40).mean(), px.rolling(200, min_periods=150).mean()
    for sgm in segs:
        m = mått(seg_idx[sgm], uni_idx)
        if m is None:
            kontroll.append(f"KONTROLL: {sgm} har för kort historik")
            continue
        kol = [t for t in px.columns if mapp[t] == sgm]
        sist = px.iloc[-1][kol]
        b50 = (sist > sma50.iloc[-1][kol]).where(sma50.iloc[-1][kol].notna()).dropna()
        b200 = (sist > sma200.iloc[-1][kol]).where(sma200.iloc[-1][kol].notna()).dropna()
        r6 = (px.iloc[-1][kol] / px.iloc[-1 - DAG["m6"]][kol] - 1).dropna().sort_values(ascending=False)
        ledare = [dict(namn=(namn.get(t) or t).replace(" AB (publ)", "").replace(" (publ)", "").replace(" AB", "").replace(" Aktiebolag", ""), t=t.replace(".ST", ""), r6=sv_pct(v)) for t, v in r6.head(3).items()]
        rad.append(dict(namn=sgm, n=seg_n[sgm], **{k: (sv_pct(v) if not isinstance(v, bool) else v) for k, v in m.items()},
                        bredd50=sv_pct(b50.mean()) if len(b50) else None, bredd200=sv_pct(b200.mean()) if len(b200) else None,
                        rs=rs_linje(seg_idx[sgm], uni_idx, se_datum), ledare=ledare, smalt=seg_n[sgm] < 10))
    df = pd.DataFrame(rad)
    for c in ("rel3", "rel6", "rel121"):
        df["p_" + c] = df[c].rank(pct=True)
    df["comp"] = df[["p_rel3", "p_rel6", "p_rel121"]].mean(axis=1)
    df["lage"] = [lage(c, o, (b / 100 if b is not None else None), r6) for c, o, b, r6 in zip(df.comp, df.over200, df.bredd200, df.rel6)]
    df = df.sort_values("comp", ascending=False).drop(columns=[c for c in df.columns if c.startswith("p_")])
    se = json.loads(df.to_json(orient="records"))
    for s in se:
        s["comp"] = round(s["comp"], 2)

    # marknadsläge
    o = omx.dropna()
    o24 = o.iloc[-1] / o.iloc[-505] - 1 if len(o) > 505 else None
    o1 = o.iloc[-1] / o.iloc[-22] - 1
    dd = o.iloc[-1] / o.iloc[-252:].max() - 1
    vol = o.pct_change().rolling(21).std().iloc[-1] * np.sqrt(252)
    vol_med = (o.pct_change().rolling(21).std() * np.sqrt(252)).dropna().median()
    bear = o24 is not None and o24 < 0
    risk = bool(bear and (vol > 1.25 * vol_med or o1 > 0.05))
    marknad = dict(omx=round(float(o.iloc[-1]), 1), over200=bool(o.iloc[-1] > o.rolling(200).mean().iloc[-1]), r1=sv_pct(o1), r12=sv_pct(o.iloc[-1] / o.iloc[-253] - 1),
                   r24=sv_pct(o24), dd=sv_pct(dd), vol=sv_pct(vol), vol_med=sv_pct(vol_med), risk=risk, bear=bool(bear))

    # USA
    us, test_us, us_datum = [], None, []
    ep = HER / "sektor_etf.csv"
    if ep.exists():
        e = pd.read_csv(ep, parse_dates=["Datum"]).pivot_table(index="Datum", columns="Ticker", values="Close").sort_index()
        spy = e["SPY"]
        us_datum = rs_datum(spy.dropna().to_frame())
        for t, nm in ETFNAMN.items():
            if t in e and e[t].dropna().shape[0] > 300:
                m = mått(e[t], spy)
                if m:
                    us.append(dict(t=t, namn=nm, extra=t in ("XBI", "SMH"), **{k: (sv_pct(v) if not isinstance(v, bool) else v) for k, v in m.items()}, rs=rs_linje(e[t], spy, us_datum)))
        du = pd.DataFrame([u for u in us if not u["extra"]])
        if len(du):
            for c in ("rel3", "rel6", "rel121"):
                du["p_" + c] = du[c].rank(pct=True)
            du["comp"] = du[["p_rel3", "p_rel6", "p_rel121"]].mean(axis=1)
            comp = dict(zip(du.t, du.comp))
            for u in us:
                if u["extra"]:      # XBI och SMH rangordnas mot de elva sektorerna
                    u["comp"] = round(float(np.mean([(du[c] < (u[c] if u[c] is not None else -9e9)).mean() for c in ("rel3", "rel6", "rel121")])), 2)
                else:
                    u["comp"] = round(float(comp[u["t"]]), 2)
                u["lage"] = lage(u["comp"], u["over200"], None, u["rel6"])
        us.sort(key=lambda u: (u["extra"], -u["comp"]))
        em = manadsavk(e[ETF_SEKTOR])
        em = em.loc[em.notna().sum(axis=1) >= 8]
        spy_m = manadsavk(e[["SPY"]])["SPY"].reindex(em.index)
        test_us = testblock(em, spy_m, None, "S&P 500 (SPY)", kost=0.001)
        test_us["kommentar"] = "Elva sektor-ETF:er (Teknik, Hälsovård m.fl.) med utdelning inräknad, från 2003. ETF:erna finns kvar, så survivorship-felet är litet. Kostnad 0,1 % per omsatt andel är inräknad. Fastigheter finns från 2015 och Media och telekom från 2018."

    # Sverige: test på månadsbasis
    M = pd.DataFrame(seg_mr)
    uni_m = uni_mr
    omx_m = manadsavk(omx.to_frame("Stang"))["Stang"]
    if M.index[-1] > px.index.max() + pd.Timedelta(days=3):          # senaste månaden är inte slut
        M, uni_m = M.iloc[:-1], uni_m.iloc[:-1]
    test_se = testblock(M, uni_m, omx_m, "Alla likvida bolag, lika vikt")
    test_se["kommentar"] = ("Urvalet är inte hela börsen: bara bolag där insiders köpt aktier sedan 2016 ingår, och valet bygger alltså på köp som kom efteråt. Dessutom är segment och likviditet valda utifrån dagens läge, och bara bolag som finns kvar ingår. Bolag som föll mycket och försvann saknas. Alla siffror ser därför för bra ut. "
                            "Jämförelsen mellan raderna är rättvisare än nivåerna. Avkastningarna är kursavkastning utan utdelning. Kostnad 0,3 % per omsatt andel är inräknad.")
    vt = viktat_test(px, mapp, info, segs)
    if vt:
        f = lambda v: f"{v:.1f}".replace(".", ",")
        test_se["kommentar"] += (f" Kontroll: med börsvärdesvikter (dagens börsvärde) i stället för lika vikter gav de tre starkaste {f(vt['a'])} % per år mot {f(vt['alla'])} % för alla bolag. "
                                 "Fördelen i testet försvinner alltså om storbolagen får väga mer.")

    data = dict(genererad=str(pd.Timestamp.now(tz="Europe/Stockholm").date()), kursdag=str(kursdag.date()),
                us_dag=str(pd.read_csv(ep, parse_dates=["Datum"]).Datum.max().date()) if ep.exists() else None,
                antal_bolag=int(px.shape[1]), utelamnade=utelamnade, se_datum=[str(d.date()) for d in se_datum], us_datum=[str(d.date()) for d in us_datum], se=se, us=us, marknad=marknad, test_se=test_se, test_us=test_us)
    UT.parent.mkdir(parents=True, exist_ok=True)
    mall = HER / "sektor_mall.html"
    if mall.exists():
        UT.write_text(mall.read_text(encoding="utf-8").replace("/*DATA*/", json.dumps(data, ensure_ascii=False, separators=(",", ":"))), encoding="utf-8")
        print(f"{len(se)} segment, {len(us)} USA-rader, {px.shape[1]} bolag, senaste kurs {kursdag.date()} -> {UT} ({UT.stat().st_size / 1e3:.0f} kB)")
    else:
        (UT.parent / "sektor.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(f"{len(se)} segment, {px.shape[1]} bolag -> {UT.parent / 'sektor.json'} (mall saknas)")
    g = lambda rader, l: ", ".join(r["namn"] for r in rader if r["lage"] == l) or "inga"
    print(f"LÄGE Sverige: medvind i {g(se, 'Medvind')}; motvind i {g(se, 'Motvind')}. USA ({data['us_dag']}): medvind i {g([u for u in us if not u['extra']], 'Medvind')}; motvind i {g([u for u in us if not u['extra']], 'Motvind')}.")
    for k in kontroll:
        print(k)


if __name__ == "__main__":
    main()
