"""Börsläget: sektionen "Oro inför AI-ras" som färdig HTML (läge, kreditspreadar, AI-aktier mot breda marknaden, två grafer).

  python borslaget_oro.py borslaget.json [--text "2-3 meningar om läget"] > oro_sektion.html

Läser borslaget.json["oro"] (från borslaget_data.py, data från oro_data.py). Använder Börslägets CSS-klasser (card, hd, grp,
facts, fact, pill, note) plus egna klasser i sektionens <style>. Graferna är statisk SVG:
  1) Högräntespreaden (HY) senaste året med zoner: under 3,5 lugnt, 3,5-5 normalt, över 5 stress (långsiktigt snitt ca 5).
  2) Nasdaq-100 och halvledarindexet SOX i förhållande till S&P 500 senaste halvåret, 100 = för ett halvår sedan.
"""
import sys, json, html

a = sys.argv[1:]
O = json.load(open(a[0], encoding="utf-8"))["oro"]
TEXT = a[a.index("--text") + 1] if "--text" in a else None
MON = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
dd = lambda s: f"{int(s[8:10])} {MON[int(s[5:7]) - 1]}"
nf = lambda x, d=2: "–" if x is None else f"{x:.{d}f}".replace(".", ",").replace("-", "−")
sg = lambda x, d=2, u="": "–" if x is None else ("+" if x > 0 else "±" if x == 0 else "−") + f"{abs(x):.{d}f}".replace(".", ",") + u

if "fel" in O:
    print(f'<section class="card" aria-labelledby="h-oro"><div class="hd"><h2 id="h-oro">Oro inför AI-ras</h2></div>'
          f'<div class="gap">Oromåtten saknas i kväll ({html.escape(O["fel"])}).</div></section>'); sys.exit()

L = O["lage"]; pill = {"Lugnt": "up", "Ökande oro": "warn", "Stress": "down"}[L["niva"]]


def linje(serier, W=420, H=200, zoner=None, ref=None, dec=0):
    """serier: [(namn, färgvariabel, [[datum, värde], ...])]. Statisk SVG med y-rutnät, månadsetiketter och slutvärde."""
    Lm, Rm, T, B = 36, 64, 12, 24
    alla = [v for _, _, h in serier for _, v in h if v is not None] + ([ref] if ref is not None else [])
    lo, hi = min(alla), max(alla); pad = (hi - lo) * 0.08 or 0.5; lo, hi = lo - pad, hi + pad
    n = max(len(h) for _, _, h in serier); d0 = serier[0][2]
    X = lambda i: Lm + i / max(n - 1, 1) * (W - Lm - Rm)
    Y = lambda v: T + (hi - v) / (hi - lo) * (H - T - B)
    o = [f'<svg class="oro-g" viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="{html.escape(", ".join(s[0] for s in serier))}">']
    if zoner:
        for z0, z1, col in zoner:
            y0, y1 = Y(min(z1, hi)), Y(max(z0, lo))
            if y1 > y0: o.append(f'<rect x="{Lm}" y="{y0:.1f}" width="{W - Lm - Rm}" height="{y1 - y0:.1f}" style="fill:var({col})" opacity=".55"/>')
    import math
    steg = [0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 20][[i for i, s_ in enumerate([0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 20]) if (hi - lo) / s_ <= 5][0]]
    g = math.ceil(lo / steg) * steg
    while g <= hi:
        o.append(f'<line x1="{Lm}" x2="{W - Rm}" y1="{Y(g):.1f}" y2="{Y(g):.1f}" style="stroke:var(--line)" stroke-dasharray="2 3"/>'
                 f'<text x="{Lm - 5}" y="{Y(g) + 4:.1f}" text-anchor="end">{nf(g, 0 if steg >= 1 else 1)}</text>'); g += steg
    if ref is not None:
        o.append(f'<line x1="{Lm}" x2="{W - Rm}" y1="{Y(ref):.1f}" y2="{Y(ref):.1f}" style="stroke:var(--faint)"/>')
    sedan, k = None, 0
    glesa = 2 if len({d[:7] for d, _ in d0}) > 7 else 1
    for i, (d, _) in enumerate(d0):
        m = d[:7]
        if m != sedan:
            if i > 3 and i < len(d0) - 8 and k % glesa == 0:
                o.append(f'<text x="{X(i):.1f}" y="{H - 6}" text-anchor="middle">{MON[int(d[5:7]) - 1]}</text>')
            k += 1
        sedan = m
    for namn, col, h in serier:
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, (_, v) in enumerate(h) if v is not None)
        o.append(f'<polyline points="{pts}" fill="none" style="stroke:var({col})" stroke-width="2" stroke-linejoin="round"/>')
        i, v = len(h) - 1, h[-1][1]
        o.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="3" style="fill:var({col})"/>'
                 f'<text x="{X(i) + 6:.1f}" y="{Y(v) + 4:.1f}" style="fill:var({col});font-weight:600">{nf(v, dec)}</text>')
    o.append("</svg>")
    return "".join(o)


hy, ig, nr, sr = O["hy"], O["ig"], O["ndx_mot_spx"], O["sox_mot_spx"]
g1 = linje([("Högräntespread USA", "--down", hy["historik"])], zoner=[(0, 3.5, "--up-bg"), (3.5, 5, "--sunk"), (5, 20, "--down-bg")], dec=2)
g2 = linje([("Nasdaq-100 mot S&P 500", "--accent", nr["historik"]), ("Halvledare (SOX) mot S&P 500", "--ras", sr["historik"])], ref=100)
skal = "".join(f"<li>{html.escape(x[0].upper() + x[1:])}</li>" for x in L["skal"]) or "<li>Inga varningssignaler just nu.</li>"
auto = (f"Högräntespreaden är {nf(hy['nu'])} procentenheter, {sg(hy['manad'])} på en månad. Nasdaq-100 har gått {sg(nr['v20'], 1, ' %')} "
        f"och halvledarna {sg(sr['v20'], 1, ' %')} mot S&P 500 på 20 dagar.")
print(f'''<section class="card" aria-labelledby="h-oro">
  <style>.oro2{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}}@media (max-width:640px){{.oro2{{grid-template-columns:1fr}}}}
  .oro-g{{display:block;max-width:520px}} .oro-g text{{fill:var(--faint);font:12px var(--f-mono)}} .oro-skal{{margin:0;padding-left:18px;font-size:14px;color:var(--muted)}}
  .oro-leg{{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--muted)}} .oro-leg i{{display:inline-block;width:16px;border-top:2.4px solid;vertical-align:middle;margin-right:5px}}</style>
  <div class="hd"><h2 id="h-oro">Oro inför AI-ras</h2><span class="pill {pill}">{L["niva"]}</span></div>
  <ul class="oro-skal">{skal}</ul>
  <div class="facts">
    <div class="fact"><span class="k">Högräntespread USA</span><span class="v">{nf(hy["nu"])} p.e. <span class="note">{sg(hy["manad"])} på en månad</span></span></div>
    <div class="fact"><span class="k">Spread kreditvärdiga bolag</span><span class="v">{nf(ig["nu"])} p.e. <span class="note">{sg(ig["manad"])} på en månad</span></span></div>
    <div class="fact"><span class="k">Nasdaq-100 mot S&amp;P 500, 20 d</span><span class="v {"down-t" if nr["v20"] < 0 else "up-t"}">{sg(nr["v20"], 1, " %")}</span></div>
    <div class="fact"><span class="k">Halvledare mot S&amp;P 500, 20 d</span><span class="v {"down-t" if sr["v20"] < 0 else "up-t"}">{sg(sr["v20"], 1, " %")} <span class="note">{nf(O["sox"]["fran_arshogsta"], 0)} % från årshögsta</span></span></div>
  </div>
  <div class="oro2">
    <div><div class="grp">Kreditspread högräntebolag, senaste året</div>
      <div class="oro-leg"><span><i style="border-color:var(--down)"></i>Procentenheter över statsräntan</span></div>{g1}
      <p class="note">Grönt fält: lugnt (under 3,5). Rött: stress (över 5). Långsiktigt snitt ca 5. Senast {dd(hy["datum"])}.</p></div>
    <div><div class="grp">AI-aktier mot breda marknaden, halvår</div>
      <div class="oro-leg"><span><i style="border-color:var(--accent)"></i>Nasdaq-100</span><span><i style="border-color:var(--ras)"></i>Halvledare (SOX)</span></div>{g2}
      <p class="note">Mot S&amp;P 500, 100 = för ett halvår sedan. Under 100: går sämre än marknaden. Senast {dd(nr["datum"])}.</p></div>
  </div>
  <p>{TEXT or auto}</p>
  <p class="note">{html.escape(L["regler"])} Kreditspreadarna kommer från FRED med ett par dagars eftersläpning.</p>
</section>''')
