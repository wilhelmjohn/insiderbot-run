#!/usr/bin/env python3
"""
Teknisk analys och grafer för flaggade fall.

teknisk(px)  -> nyckeltal, föreslagen stop loss och brytkurs
rita(...)    -> PNG: kurs, MA50/MA200, stop loss, brytkurs och en prickad linje 3 månader framåt
rita_marknad -> PNG: index och marknadsmåttet -10..+10

Definitioner (all analys bygger på stängningskurser, kurser.csv saknar högsta/lägsta):
  Stop loss   = den lägsta av (lägsta stängning senaste 20 dagarna - 1 %) och
                (kurs - 1,5 x daglig volatilitet x kvadratroten ur 20), men aldrig tätare än -8 %
                och aldrig bredare än -20 %. Backtestet visade att täta stopp (-10 %) stoppar ut
                två av tre affärer och att nästan hälften sedan vänder upp, så stoppet är ett
                skydd mot katastrofer och inte ett sätt att öka avkastningen.
  Brytkurs    = högsta stängning senaste 60 handelsdagarna (utom de 2 senaste). Stänger kursen över
                den är en utbrytning bekräftad; ligger kursen redan över visas det som "bruten".
  Förväntan   = den prickade linjen är medianen (och kvartilerna som band) av utfallet 1 och 3
                månader efter tidigare regelträffar i backtestet (forvantan.csv). Det är historisk
                spridning för liknande fall, inte en prognos för just det här bolaget.
"""
from pathlib import Path

import numpy as np
import pandas as pd

MAPP = Path(__file__).resolve().parent
FORVANTAN = MAPP / "forvantan.csv"
HORISONT = 63


def _rsi(px: pd.Series, n: int = 14) -> float:
    d = px.diff()
    upp = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    ner = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = upp / ner.replace(0, np.nan)
    v = 100 - 100 / (1 + rs)
    return float(v.iloc[-1]) if pd.notna(v.iloc[-1]) else np.nan


def teknisk(px: pd.Series) -> dict:
    """px: stängningskurser (index = datum), minst ~200 dagar för full analys."""
    px = px.dropna()
    if len(px) < 60:
        return {}
    kurs = float(px.iloc[-1])
    ma50 = float(px.rolling(50).mean().iloc[-1]) if len(px) >= 50 else np.nan
    ma200 = float(px.rolling(200).mean().iloc[-1]) if len(px) >= 150 else np.nan
    hog52 = float(px.iloc[-252:].max())
    sigma = float(px.pct_change().iloc[-20:].std())
    lag20 = float(px.iloc[-20:].min())
    stop = min(lag20 * 0.99, kurs * (1 - 1.5 * sigma * np.sqrt(20)))
    stop = float(min(max(stop, kurs * 0.80), kurs * 0.92))
    brytkurs = float(px.iloc[-62:-2].max()) if len(px) >= 62 else float(px.iloc[:-2].max())
    bruten = kurs > brytkurs
    ut = {
        "Kurs": kurs, "MA50": ma50, "MA200": ma200, "Hog52v": hog52,
        "Under_52v_%": (kurs / hog52 - 1) * 100,
        "Over_MA200": bool(kurs > ma200) if pd.notna(ma200) else None,
        "Over_MA50": bool(kurs > ma50) if pd.notna(ma50) else None,
        "RSI14": _rsi(px),
        "Volatilitet_dag_%": sigma * 100,
        "Stop": stop, "Stop_%": (stop / kurs - 1) * 100,
        "Brytkurs": brytkurs, "Bruten": bruten, "Brytkurs_%": (brytkurs / kurs - 1) * 100,
    }
    ut["Text"] = beskriv(ut)
    return ut


def beskriv(t: dict) -> str:
    rader = []
    if t.get("Over_MA200") is not None:
        rader.append(f"Trend: {'över' if t['Over_MA200'] else 'under'} MA200 ({t['MA200']:.2f}), "
                     f"{'över' if t.get('Over_MA50') else 'under'} MA50 ({t['MA50']:.2f}), {t['Under_52v_%']:.0f} % från 52v-högsta")
    rsi = t.get("RSI14")
    if pd.notna(rsi):
        rader.append(f"RSI {rsi:.0f}" + (" (överköpt)" if rsi >= 70 else " (översåld)" if rsi < 30 else ""))
    rader.append(f"Riskgräns (stop) {t['Stop']:.2f} ({t['Stop_%']:.0f} %): skydd mot stora förluster, höjde inte avkastningen i testet")
    if t["Bruten"]:
        rader.append(f"Brytkurs {t['Brytkurs']:.2f} är bruten (kurs {-t['Brytkurs_%']:.1f} % över)")
    else:
        rader.append(f"Brytkurs {t['Brytkurs']:.2f} (+{t['Brytkurs_%']:.1f} %): stängning över bekräftar utbrytning")
    return "\n".join(rader)


def _forvantan() -> pd.DataFrame | None:
    return pd.read_csv(FORVANTAN) if FORVANTAN.exists() else None


def _mkr(b: float) -> str:
    return f"{b/1e6:.1f} Mkr" if abs(b) >= 1e6 else f"{b/1e3:.0f} tkr"


def sammanfatta_handel(handel: pd.DataFrame, idag, dagar: int = 90) -> str:
    """En rad om köp/sälj senaste `dagar` dagarna plus senaste köp och sälj."""
    if handel is None or handel.empty:
        return "Ingen insynshandel i aktien senaste året"
    idag = pd.Timestamp(idag)
    ut = []
    for typ, ord_ in (("Köp", "Köp"), ("Sälj", "Sälj")):
        h = handel[handel["Typ"] == typ]
        r = h[h["Datum"] > idag - pd.Timedelta(days=dagar)]
        s = f"{ord_} {dagar} d: {r['Person'].nunique()} pers, {_mkr(r['Belopp'].sum())}" if not r.empty else f"{ord_} {dagar} d: inga"
        if not h.empty:
            l = h.sort_values("Datum").iloc[-1]
            s += f" (senast {l['Datum']:%y-%m-%d} {str(l['Person']).split()[-1]} {_mkr(l['Belopp'])})"
        ut.append(s)
    return "  |  ".join(ut)


def _ljus(ohlc, sen):
    """OHLC för grafens period, eller None om data saknas eller inte stämmer med slutkurserna (justeringar)."""
    if ohlc is None or len(ohlc) < 2:
        return None
    x = ohlc.reindex(sen.index).dropna()
    if len(x) < (0.5 if len(sen) < 30 else 0.8) * len(sen):
        return None
    s = sen.reindex(x.index)
    if ((x["Close"] / s - 1).abs() > 0.02).mean() > 0.05:      # >2 % avvikelse på fler än 5 % av dagarna
        return None
    return x


def _rita_ljus(ax, x):
    from matplotlib.patches import Rectangle
    import matplotlib.dates as mdates
    bredd = 0.85
    for d, r in x.iterrows():
        up = r["Close"] >= r["Open"]
        farg = "#16a34a" if up else "#dc2626"
        xd = mdates.date2num(d)
        ax.vlines(xd, r["Low"], r["High"], color=farg, lw=0.7, zorder=3)
        lo, hi = min(r["Open"], r["Close"]), max(r["Open"], r["Close"])
        ax.add_patch(Rectangle((xd - bredd / 2, lo), bredd, max(hi - lo, (r["High"] - r["Low"]) * 0.02), facecolor=farg,
                               edgecolor=farg, lw=0.4, zorder=4))
    ax.plot([], [], color="#16a34a", lw=4, label="Ljus (upp)")
    ax.plot([], [], color="#dc2626", lw=4, label="Ljus (ned)")
    ax.set_xlim(mdates.date2num(x.index[0]) - 3, mdates.date2num(x.index[-1]) + 3)
    ax.autoscale(axis="y")
    ax.set_ylim(x["Low"].min() * 0.97, x["High"].max() * 1.03)


def _teknikritning(ax, px, sen):
    """Teknisk analys i grafen (som Investtech): toppar och bottnar markerade med cirklar och kurs, stöd- och
    motståndsnivåer (gröna linjer med avstånd till nuvarande kurs vid högerkanten) och trendkanal (grå parallella
    linjer). Returnerar antal nivåer som ritades (de behöver marginal till höger)."""
    import matplotlib.dates as mdates
    from scipy.signal import find_peaks
    if len(sen) < 60:
        return 0
    y = sen.values.astype(float)
    sist = y[-1]
    prom = 0.06 * float(np.median(y))
    topp, _ = find_peaks(y, prominence=prom, distance=8)
    botten, _ = find_peaks(-y, prominence=prom, distance=8)
    # toppar och bottnar: de fem mest framträdande av varje sort
    def bast(idx, tecken):
        if len(idx) <= 5:
            return list(idx)
        from scipy.signal import peak_prominences
        p = peak_prominences(tecken * y, idx)[0]
        return [idx[i] for i in sorted(np.argsort(p)[-5:])]
    topp, botten = bast(topp, 1), bast(botten, -1)
    for i in topp:
        ax.scatter([sen.index[i]], [y[i]], s=75, facecolors="none", edgecolors="#dc2626", lw=1.5, zorder=8)
        ax.annotate(f"{y[i]:.2f}", (sen.index[i], y[i]), xytext=(0, 9), textcoords="offset points", ha="center", fontsize=7, color="#374151", zorder=9)
    for i in botten:
        ax.scatter([sen.index[i]], [y[i]], s=75, facecolors="none", edgecolors="#16a34a", lw=1.5, zorder=8)
        ax.annotate(f"{y[i]:.2f}", (sen.index[i], y[i]), xytext=(0, -14), textcoords="offset points", ha="center", fontsize=7, color="#374151", zorder=9)
    # stöd och motstånd: närliggande topp-/bottennivåer (inom 2,5 %) slås ihop, minst två träffar
    niv = sorted(float(y[i]) for i in list(topp) + list(botten))
    kluster = []
    for v in niv:
        if kluster and v / np.mean(kluster[-1]) - 1 <= 0.025:
            kluster[-1].append(v)
        else:
            kluster.append([v])
    nivaer = [float(np.mean(k)) for k in kluster if len(k) >= 2]
    nivaer = sorted(sorted(nivaer, key=lambda v: abs(v / sist - 1))[:4])
    for v in nivaer:
        ax.axhline(v, color="#16a34a", lw=1.0, alpha=0.85, zorder=2)
        ax.annotate(f"{v:.2f}\n{(v / sist - 1) * 100:+.1f} %", xy=(1.0, v), xycoords=ax.get_yaxis_transform(), xytext=(4, 0),
                    textcoords="offset points", ha="left", va="center", fontsize=7, color="white", annotation_clip=False,
                    bbox=dict(boxstyle="square,pad=0.25", fc="#16a34a", ec="none"))
    # trendkanal: senaste ~120 dagarna. Övre linjen genom de två mest framträdande topparna, nedre linjen parallell
    # genom lägsta kursen under övre linjen.
    N = min(120, len(y))
    off = len(y) - N
    tt = [i - off for i in topp if i >= off]
    farg = "#6b7280"
    ritad = False
    if len(tt) >= 2:
        tt = sorted(sorted(tt, key=lambda i: -y[off + i])[:2])
        i1, i2 = tt
        if i2 - i1 >= 10:
            dn = mdates.date2num(sen.index[off:].to_pydatetime())  # kalenderdagar => helt raka linjer
            k = (y[off + i2] - y[off + i1]) / (dn[i2] - dn[i1])
            ovre = y[off + i1] + k * (dn - dn[i1])
            bb = [i - off for i in botten if i >= off]
            if bb:
                nedre_off = float(min(y[off + i] - ovre[i] for i in bb))
            else:
                nedre_off = float((y[off:] - ovre).min())
            idx = sen.index[off:]
            ax.plot(idx, ovre, color=farg, lw=1.4, zorder=3, label=f"Trendkanal {N} d ({k * 30 / y[-1] * 100:+.0f} %/mån)")
            ax.plot(idx, ovre + nedre_off, color=farg, lw=1.4, zorder=3)
            ritad = True
    if not ritad and N >= 60:
        # för få toppar för en lutande kanal: vågrät kanal mellan högsta topp och lägsta botten i fönstret
        fon = y[off:]
        hog = max([y[i] for i in topp if i >= off] or [fon.max()])
        lag = min([y[i] for i in botten if i >= off] or [fon.min()])
        if hog / lag - 1 >= 0.03:
            idx = sen.index[off:]
            ax.plot(idx, [hog] * len(idx), color=farg, lw=1.4, zorder=3, label=f"Sidledes kanal {N} d ({lag:.0f}–{hog:.0f})")
            ax.plot(idx, [lag] * len(idx), color=farg, lw=1.4, zorder=3)
    lo_, hi_ = ax.get_ylim()
    ax.set_ylim(lo_, hi_)
    return len(nivaer)


def rita(namn: str, ticker: str, px: pd.Series, tek: dict, sokvag, publ_datum=None, kopkurs=None,
         forvantan: bool = True, handel: pd.DataFrame = None, borsvarde: float = None, rapporter=None, facit: pd.Series = None, ohlc: pd.DataFrame = None, klipp_x: bool = False, vol: pd.Series = None) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    px = px.dropna()
    sen = px.iloc[-252:]
    harvol = vol is not None and int(vol.reindex(sen.index).notna().sum()) >= 20
    if harvol:
        fig, (ax, axv) = plt.subplots(2, 1, figsize=(9, 7.0), dpi=110, sharex=True, gridspec_kw={"height_ratios": [4.3, 1], "hspace": 0.05})
    else:
        fig, ax = plt.subplots(figsize=(9, 5.2), dpi=110)
    ljus = _ljus(ohlc, sen)
    if ljus is None:
        ax.plot(sen.index, sen.values, color="#1f2937", lw=1.6, label="Kurs")
    else:
        _rita_ljus(ax, ljus)
    if len(px) >= 50:
        ax.plot(sen.index, px.rolling(50).mean().reindex(sen.index), color="#2563eb", lw=1, label="MA50")
    if len(px) >= 150:
        ax.plot(sen.index, px.rolling(200, min_periods=150).mean().reindex(sen.index), color="#d97706", lw=1, label="MA200")

    try:
        niv = _teknikritning(ax, px, sen)
    except Exception as e:
        print('  Teknikritning misslyckades:', e)
        niv = 0
    if harvol:
        v = vol.reindex(sen.index).fillna(0).rolling(5, min_periods=1).mean()
        axv.fill_between(sen.index, 0, v.values, color="#9ca3af", alpha=0.8, lw=0)
        axv.set_yticks([]); axv.set_ylabel("Volym", fontsize=7); axv.grid(alpha=0.2); axv.set_ylim(bottom=0)

    if tek:
        ax.axhline(tek["Stop"], color="#dc2626", ls="--", lw=1, label=f"Riskgräns (stop) {tek['Stop']:.2f}")
        ax.axhline(tek["Brytkurs"], color="#16a34a", ls="--", lw=1, label=f"Brytkurs (bekräftelse) {tek['Brytkurs']:.2f}")
    if publ_datum is not None:
        pd_ = pd.Timestamp(publ_datum)
        if sen.index[0] <= pd_ <= sen.index[-1] + pd.Timedelta(days=3):
            ax.axvline(pd_, color="#7c3aed", ls=":", lw=1)
            ax.annotate("insynsköp\npublicerat", (pd_, ax.get_ylim()[0]), fontsize=7, color="#7c3aed",
                        xytext=(-3 if klipp_x else 3, 6), textcoords="offset points", va="bottom", ha="right" if klipp_x else "left")
    if kopkurs and not np.isnan(kopkurs):
        ax.scatter([sen.index[-1]], [kopkurs], marker="_", s=300, color="#7c3aed", zorder=5)

    if handel is not None and not handel.empty:
        h = handel[(handel["Datum"] >= sen.index[0]) & (handel["Datum"] <= sen.index[-1] + pd.Timedelta(days=3))]
        if not h.empty:
            if "Publ" not in h.columns:
                h = h.assign(Publ=h["Datum"])
            g = (h.groupby(["Datum", "Typ"]).agg(Belopp=("Belopp", "sum"), Person=("Person", "first"), N=("Person", "nunique"),
                                                  Publ=("Publ", "max")).reset_index())
            g["Kurs"] = [float(px.loc[:d].iloc[-1]) if len(px.loc[:d]) else np.nan for d in g["Datum"]]
            bmax = max(g["Belopp"].max(), 1e6)
            g["S"] = 18 + 320 * np.sqrt(g["Belopp"] / bmax)
            for typ, farg, mark, lab in (("Köp", "#16a34a", "^", "Insynsköp"), ("Sälj", "#dc2626", "v", "Insynssälj")):
                x = g[g["Typ"] == typ]
                if not x.empty:
                    ax.scatter(x["Datum"], x["Kurs"], s=x["S"], marker=mark, color=farg, alpha=0.75, edgecolors="white",
                               linewidths=0.6, zorder=6, label=f"{lab} (storlek = belopp)")
            for _, r in g.sort_values("Belopp", ascending=False).head(3).iterrows():
                forsen = (pd.Timestamp(r["Publ"]) - pd.Timestamp(r["Datum"])).days
                txt = f"{str(r['Person']).split()[-1]} {_mkr(r['Belopp'])}" + (f"\nköpt {r['Datum']:%d/%m}, anmäldes {forsen} d senare" if forsen > 14 else "")
                ax.annotate(txt, (r["Datum"], r["Kurs"]), fontsize=6.5,
                            xytext=(0, 9 if r["Typ"] == "Köp" else -13), textcoords="offset points", ha="center",
                            color="#166534" if r["Typ"] == "Köp" else "#991b1b")

    f = _forvantan() if forvantan else None
    if f is not None and len(f) >= 2:
        sist = sen.index[-1]; kurs = tek["Kurs"] if tek else float(sen.iloc[-1])
        fut = pd.bdate_range(sist, periods=HORISONT + 1)
        dagar = f["Handelsdagar"].to_numpy(float)
        steg = np.arange(HORISONT + 1)
        med = kurs * (1 + np.interp(steg, dagar, f["q50"]))
        lag = kurs * (1 + np.interp(steg, dagar, f["q25"]))
        hog = kurs * (1 + np.interp(steg, dagar, f["q75"]))
        ax.plot(fut, med, color="#111827", ls=":", lw=2, label=f"Förväntad (median, N={int(f['N'].iloc[-1])})")
        ax.fill_between(fut, lag, hog, color="#9ca3af", alpha=0.25, label="Kvartilspann")
        ax.annotate(f"{med[-1]:.1f}", (fut[-1], med[-1]), fontsize=8, xytext=(4, 0), textcoords="offset points")

    if facit is not None and len(facit):
        fc = pd.concat([sen.iloc[-1:], facit.dropna()])
        ax.plot(fc.index, fc.values, color="#ea580c", lw=2, label=f"Faktiskt utfall (facit): {facit.dropna().iloc[-1] / sen.iloc[-1] - 1:+.1%}")
    if rapporter:
        # rapportdagar (grå linjer) och uppskattad nästa rapport (streckad); köp precis efter en rapport är historiskt svagast
        rdat, nasta = rapporter
        xmin, xmax = ax.get_xlim()
        ymin = ax.get_ylim()[0]
        forst_r = True
        for d in rdat:
            d = pd.Timestamp(d)
            if sen.index[0] <= d <= (facit.index[-1] if facit is not None and len(facit) else sen.index[-1]):
                ax.axvline(d, color="#6b7280", lw=0.8, alpha=0.6, zorder=1, label="Rapport" if forst_r else None)
                ax.annotate("R", (d, ymin), fontsize=7, color="#4b5563", xytext=(2, 3), textcoords="offset points")
                forst_r = False
        if nasta is not None:
            n = pd.Timestamp(nasta)
            ax.axvline(n, color="#6b7280", lw=1, ls="--", alpha=0.8, zorder=1, label=f"Nästa rapport ca {n:%d %b} (uppskattad)")
            ax.annotate("R?", (n, ymin), fontsize=7, color="#4b5563", xytext=(2, 3), textcoords="offset points")
            ax.set_xlim(xmin, max(xmax, mdates.date2num(n + pd.Timedelta(days=6))))

    rubrik = (f"{namn} ({ticker})" if ticker else namn) + (f"  |  börsvärde {borsvarde/1e9:.1f} mdr kr" if borsvarde and borsvarde >= 1e8 else "")
    ax.set_title(rubrik, fontsize=11, loc="left", pad=16)
    if handel is not None:
        ax.text(0, 1.01, sammanfatta_handel(handel, sen.index[-1]), transform=ax.transAxes, fontsize=7, color="#374151", va="bottom")
    ax.grid(alpha=0.2)
    h_, l_ = ax.get_legend_handles_labels()
    ncol_ = 4
    leg_rader = -(-len(l_) // ncol_)
    leg_fig = fig.legend(h_, l_, fontsize=7, loc="lower center", ncol=ncol_, framealpha=0.9, bbox_to_anchor=(0.5, 0.025))
    if f is not None and len(f) >= 2:
        fig.text(0.01, 0.005, "Prickad linje: historiskt utfall för liknande regelträffar (median/kvartiler), inte en prognos.",
                 fontsize=6.5, color="#6b7280")
    if len(sen) < 40:                      # nyligen noterat: dagsaxel i stället för timmar
        ax.xaxis.set_major_locator(mdates.DayLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    fig.autofmt_xdate()
    fig.tight_layout(rect=(0, 0.04 + 0.028 * leg_rader, 0.93 if niv else 1, 1))
    sokvag = Path(sokvag)
    if klipp_x:
        ax.set_xlim(mdates.date2num(sen.index[0]) - 3, mdates.date2num(sen.index[-1]) + 1)
    fig.savefig(sokvag)
    plt.close(fig)
    return sokvag


def rita_marknad(marknad_csv, sokvag, dagar: int = 400) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    m = pd.read_csv(marknad_csv, index_col=0, parse_dates=True).iloc[-dagar:]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(9, 5.6), dpi=110, sharex=True,
                                 gridspec_kw={"height_ratios": [2, 1.4]})
    a1.plot(m.index, m["Index"], color="#1f2937", lw=1.4)
    a1.set_title("OMXSPI", fontsize=10, loc="left"); a1.grid(alpha=0.2)
    t = m["Trend"]
    a2.fill_between(m.index, 0, t, where=t >= 0, color="#16a34a", alpha=0.6)
    a2.fill_between(m.index, 0, t, where=t < 0, color="#dc2626", alpha=0.6)
    a2.axhline(0, color="#6b7280", lw=0.8); a2.set_ylim(-10, 10); a2.grid(alpha=0.2)
    a2.set_title(f"Marknadsmått {t.iloc[-1]:+.1f} (−10 superbear, +10 superbull)", fontsize=10, loc="left")
    fig.autofmt_xdate(); fig.tight_layout()
    sokvag = Path(sokvag)
    fig.savefig(sokvag); plt.close(fig)
    return sokvag


if __name__ == "__main__":
    import sys
    from backtest import las_kurser
    tk = sys.argv[1] if len(sys.argv) > 1 else "MYCR.ST"
    px = las_kurser(MAPP / "kurser.csv")[tk].dropna()
    t = teknisk(px)
    print(t.pop("Text")); print(t)
    print(rita(tk, tk, px, teknisk(px), MAPP / "prov_analys.png"))


_MAN = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober", "november", "december"]


def _kr(v):
    return f"{v:.2f}".rstrip("0").rstrip(".") if v < 100 else f"{v:.0f}"


def kommentar(namn: str, px: pd.Series, vol: pd.Series = None, ohlc: pd.DataFrame = None) -> str:
    """Kort marknadskommentar i Investtech-stil, räknad enbart ur kurs- och volymdata fram till sista dagen."""
    from scipy.signal import find_peaks
    s = px.dropna()
    if len(s) < 60:
        return ""
    y = s.values.astype(float)
    sist, fore = y[-1], y[-2]
    r = (sist / fore - 1) * 100
    kurs = _kr(sist)
    ut = []
    if abs(r) < 0.5:
        ut.append(f"{namn} rörde sig närmast sidledes och slutade på {kurs} kronor ({r:+.1f} %).".replace(".", ",", 0))
    else:
        if r > 0:
            verb = "steg kraftigt med" if r >= 2.5 else "gick upp"
            ut.append(f"{namn} {verb} {r:+.1f} % till en slutnotering på {kurs} kronor.")
        else:
            verb = "föll kraftigt med" if r <= -2.5 else "sjönk"
            ut.append(f"{namn} {verb} {r:+.1f} % till en slutnotering på {kurs} kronor.")
    # rekord / senast högre eller lägre
    fonster = y[-252:]
    if sist >= fonster.max() and len(y) > 100:
        ut.append("Aktien stängde på den högsta nivån på över ett år." if len(y) >= 252 else "Aktien stängde på högsta nivån i vårt dataunderlag.")
    elif sist <= fonster.min() and len(y) > 100:
        ut.append("Aktien stängde på den lägsta nivån på över ett år." if len(y) >= 252 else "Aktien stängde på lägsta nivån i vårt dataunderlag.")
    else:
        mask = (y[:-1] > sist) if r >= 0 else (y[:-1] < sist)
        if mask.any():
            j = np.where(mask)[0][-1]
            dagar = (s.index[-1] - s.index[j]).days
            if dagar >= 60:
                d = s.index[j]
                ut.append(f"Aktien har inte varit {'högre' if r >= 0 else 'lägre'} sedan {_MAN[d.month - 1]} {d.year}.")
    # årsbästa / årslägsta (kalenderår, stängningskurser)
    ar = s[s.index.year == s.index[-1].year]
    if len(ar) > 20:
        for namn_, kond, ext in (("årsbästa", ar.values[1:] > np.maximum.accumulate(ar.values)[:-1], "högsta"), ("årslägsta", ar.values[1:] < np.minimum.accumulate(ar.values)[:-1], "lägsta")):
            if kond[-1]:
                n_ = int(kond.sum())
                ordn = ["", "första", "andra", "tredje", "fjärde", "femte", "sjätte", "sjunde", "åttonde", "nionde", "tionde", "elfte", "tolfte"]
                ut.append(f"Aktien gjorde med det nytt {namn_} för {ordn[n_] if n_ < len(ordn) else str(n_) + ':e'} gången i år.")
                break
    # serie
    steg = np.sign(np.diff(y))
    n = 0
    for v in steg[::-1]:
        if v == steg[-1] and v != 0:
            n += 1
        else:
            break
    if n >= 3:
        ut.append(f"Det var {'den ' + ['', '', '', 'tredje', 'fjärde', 'femte', 'sjätte', 'sjunde', 'åttonde', 'nionde', 'tionde'][n] if n <= 10 else 'den ' + str(n) + ':e'} dagen i rad som aktien {'steg' if steg[-1] > 0 else 'sjönk'}.")
    m = (sist / y[-22] - 1) * 100
    if abs(m) >= 10:
        ut.append(f"Den senaste månaden har aktien {'stigit' if m > 0 else 'sjunkit'} hela {m:+.1f} %.")
    # volym
    if vol is not None:
        v = vol.reindex(s.index)
        if v.iloc[-1] and v.iloc[-21:-1].mean() > 0:
            kvot = v.iloc[-1] / v.iloc[-21:-1].mean()
            if kvot >= 2:
                ut.append(f"Omsättningen var dessutom hög, ungefär {kvot:.0f} gånger genomsnittlig dagsomsättning.")
    # formation
    try:
        import formationer
        fm = formationer.hitta(s, max_dagar=40, ohlc=ohlc)
    except Exception:
        fm = []
    if fm:
        h = fm[0]
        nm_ = {"dubbelbotten": "dubbelbotten-formation", "dubbeltopp": "dubbeltopp-formation", "rektangel": "rektangelformation",
               "omvänd huvud-skuldra": "omvänd huvud-skuldra-formation", "huvud-skuldra": "huvud-skuldra-formation"}[h["namn"]]
        d = h["dagar_sedan"]
        tal = ["noll", "en", "två", "tre", "fyra", "fem", "sex", "sju", "åtta", "nio", "tio", "elva", "tolv"]
        ord_ = {0: "idag", 1: "igår"}
        nar = ord_[d] if d in ord_ else (f"{tal[d // 7]} veckor sedan" if d >= 14 and d // 7 < len(tal) else f"{tal[d] if d < len(tal) else d} dagar sedan")
        rorelse = (sist / y[-1 - d] - 1) * 100
        upp = h["typ"] == "köp"
        okvol = ""
        if vol is not None:
            vv = vol.reindex(s.index)
            bi = len(y) - 1 - d
            if vv.iloc[bi] and vv.iloc[max(0, bi - 20):bi].mean() > 0 and vv.iloc[bi] > 1.2 * vv.iloc[max(0, bi - 20):bi].mean():
                okvol = " på ökande volym"
        mal = f" och en vidare {'uppgång' if upp else 'nedgång'} till {_kr(h['mal'])} kronor indikeras" if ((upp and sist < h["mal"]) or (not upp and sist > h["mal"])) and d <= 10 else ""
        if d == 0:
            ut.append(f"Aktien bröt {'upp' if upp else 'ned'} genom {'motståndet' if upp else 'stödet'} vid {_kr(h['niva'])} kronor i en {nm_}. "
                      f"Det utlöste en {h['typ']}signal{okvol}{mal}.")
        else:
            rorelse = (sist / y[-1 - d] - 1) * 100
            ut.append(f"Aktien har {'därmed' if (rorelse >= 0) == upp else 'dock'} {'stigit' if rorelse >= 0 else 'sjunkit'} {rorelse:+.1f} % efter "
                      f"{h['typ']}signalen från en {nm_} {nar}{okvol and ', som kom' + okvol}."
                      + (f" En vidare {'uppgång' if upp else 'nedgång'} till {_kr(h['mal'])} kronor indikeras." if mal else ""))
    # teknik
    ma50, ma200 = s.rolling(50).mean().iloc[-1], s.rolling(200).mean().iloc[-1] if len(s) >= 200 else np.nan
    if sist > ma50 and (np.isnan(ma200) or ma50 > ma200):
        trend = "positiv"
    elif sist < ma50 and (np.isnan(ma200) or ma50 < ma200):
        trend = "negativ"
    else:
        trend = None
    prom = 0.06 * float(np.median(y[-252:]))
    top, _ = find_peaks(y[-252:], prominence=prom, distance=8)
    bot, _ = find_peaks(-y[-252:], prominence=prom, distance=8)
    pv = sorted(float(y[-252:][i]) for i in list(top) + list(bot))
    kl = []
    for p in pv:
        if kl and p / np.mean(kl[-1]) - 1 <= 0.025:
            kl[-1].append(p)
        else:
            kl.append([p])
    niv = [float(np.mean(k)) for k in kl if len(k) >= 2]
    under = [v for v in niv if v < sist * 0.995]
    over = [v for v in niv if v > sist * 1.005]
    delar = []
    if trend:
        delar.append(f"trendmässigt {trend} på medellång sikt")
    senast5 = y[-6:-1].max()
    brott = [v for v in niv if r > 0 and y[-6] < v <= sist]
    if brott:
        delar.append(f"har brutit upp igenom motstånd vid ca {_kr(max(brott))} kronor")
    elif under and sist / max(under) - 1 < 0.10:
        delar.append(f"har stöd vid ca {_kr(max(under))} kronor")
    if over and not brott and over[0] / sist - 1 < 0.10:
        delar.append(f"har motstånd vid ca {_kr(min(over))} kronor")
    if delar:
        t = "Tekniskt sett: aktien är " + delar[0] if trend else "Tekniskt sett: aktien " + delar[0]
        rest = delar[1:]
        txt = t + ("".join(f", {d}" for d in rest[:-1]) + (" och " + rest[-1] if rest else "") if rest else "")
        ut.append(txt + ".")
    import re
    return re.sub(r"(\d)\.(\d)", r"\1,\2", " ".join(ut))
