"""Börsläget: sektionen Räntorna som färdig HTML (räntekurvan 10–2 år, graf med räntor och kommentar).

  python borslaget_rantor.py borslaget.json [--text "2-3 meningar om vad dagens rörelse kan betyda"] > rantor_sektion.html

Läser borslaget.json["rantor"] (från borslaget_data.py). Använder Börslägets CSS-klasser (card, hd, grp, fact,
pill, note) plus klasserna .rk och .kurva som definieras i sektionens egen <style>. Grafen är statisk SVG (ingen JS) med
löptider från 3 månader till 30 år på logaritmisk skala; heldragen linje = senaste dagen, streckad = en månad före; etiketter med
ränta och dagens förändring vid 3 mån, 2, 10 och 30 år.
Utan --text skrivs en enkel automatisk kommentar (den schemalagda uppgiften ska skriva en egen).
"""
import sys, json, math, html

a = sys.argv[1:]
R = json.load(open(a[0], encoding="utf-8"))["rantor"]
TEXT = a[a.index("--text") + 1] if "--text" in a else None
MON = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
dd = lambda s: f"{int(s[8:10])} {MON[int(s[5:7]) - 1]}"
nf = lambda x, d=2: "–" if x is None else f"{x:.{d}f}".replace(".", ",").replace("-", "−")
pe = lambda x: "–" if x is None else ("+" if x > 0 else "±" if x == 0 else "−") + f"{abs(x):.2f}".replace(".", ",") + " p.e."
LAND = {"US": ("USA", "US"), "SE": ("Sverige", "SE")}

if "fel" in R or not all(k in R for k in ("US", "SE")):
    print(f'<section class="card" aria-labelledby="h-rt"><div class="hd"><h2 id="h-rt">Räntorna</h2></div>'
          f'<div class="gap">Räntedatan saknas i kväll ({html.escape(R.get("fel", "USA eller Sverige saknas"))}).</div></section>')
    sys.exit()


def tile(land, lop):
    n = R[land]["nivaer"].get(lop)
    if not n: return ""
    return (f'<div class="tile"><span class="lbl"><span class="flag">{LAND[land][1]}</span>{lop}</span>'
            f'<span class="v num">{nf(n["ranta"])} %</span><span class="s">{pe(n["dag_pe"])} i dag · månad {pe(n["manad_pe"])}</span></div>')


def kurvtext(land):
    s = R[land]["spread_10_2"]; nu = s["nu"]
    if nu is None: return "", ""
    if nu < 0: pill, ord_ = ("down", "Inverterad"), "Tvåårsräntan är högre än tioårsräntan, vilket historiskt ofta har kommit före en lågkonjunktur"
    elif nu < 0.5: pill, ord_ = ("warn", "Positiv men flack"), "Långa räntor bara lite över korta"
    else: pill, ord_ = ("up", "Normal, positiv"), "Långa räntor tydligt över korta, det normala läget"
    ch = (nu - s["manad"]) if s["manad"] is not None else 0
    rikt = "brantare" if ch > 0.03 else "flackare" if ch < -0.03 else "ungefär lika"
    return pill, (f'{ord_}. {rikt.capitalize()} än för en månad sedan ({pe(s["manad"])})'
                  f'{"" if s["ar_min"] is None else f", senaste årets spann {pe(s['ar_min'])} till {pe(s['ar_max'])}"}')


def graf():
    """Kurvor 3 mån–30 år. Heldragen = senaste dagen, streckad = en månad före (fångar oro som byggs upp under veckor).
    Etiketter med nivå och dagens förändring."""
    W, H, L, Rm, T, B = 440, 262, 34, 14, 30, 62
    J = "kurva_manad"
    pts = [(m, r) for land in ("US", "SE") for k in ("kurva_nu", J) for m, _, r in R[land][k] if m >= 3]
    lo, hi = math.floor(min(r for _, r in pts) * 2) / 2, math.ceil(max(r for _, r in pts) * 2) / 2
    X = lambda m: L + (math.log(m) - math.log(3)) / (math.log(360) - math.log(3)) * (W - L - Rm)
    Y = lambda r: T + (hi - r) / (hi - lo) * (H - T - B)
    o = [f'<svg class="kurva" viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Räntekurvor för USA och Sverige, 3 månader till 30 år, '
         f'senaste dagen och en månad före, med räntan och dagens förändring vid 3 månader, 2, 10 och 30 år">']
    g = lo
    while g <= hi + 1e-9:
        o.append(f'<line x1="{L}" x2="{W - Rm}" y1="{Y(g):.1f}" y2="{Y(g):.1f}" style="stroke:var(--line)" stroke-dasharray="2 3"/>'
                 f'<text x="{L - 6}" y="{Y(g) + 4:.1f}" text-anchor="end">{nf(g, 1)}</text>'); g += 0.5
    for m, t in ((3, "3 mån"), (12, "1 år"), (24, "2 år"), (60, "5 år"), (120, "10 år"), (360, "30 år")):
        o.append(f'<text x="{X(m):.1f}" y="{H - 8}" text-anchor="{"start" if m == 3 else "end" if m == 360 else "middle"}">{t}</text>')
    for land, col, upp in (("US", "--accent", True), ("SE", "--warn", False)):
        igar = {m: r for m, _, r in R[land].get("kurva_igar", [])}
        for k, dash, w in ((J, ' stroke-dasharray="5 4"', 1.5), ("kurva_nu", "", 2.4)):
            p = [(m, r) for m, _, r in R[land][k] if m >= 3]
            o.append(f'<polyline points="{" ".join(f"{X(m):.1f},{Y(r):.1f}" for m, r in p)}" fill="none" style="stroke:var({col})" '
                     f'stroke-width="{w}" stroke-linejoin="round"{dash}{" opacity=\".7\"" if dash else ""}/>')
        for m, _, r in R[land]["kurva_nu"]:
            if m < 3: continue
            o.append(f'<circle cx="{X(m):.1f}" cy="{Y(r):.1f}" r="2.6" style="fill:var({col})"/>')
            if m in (3, 24, 120, 360):
                ch = (r - igar[m]) if m in igar else None
                anc = "start" if m == 3 else "end" if m == 360 else "middle"
                y1 = Y(r) - 22 if upp else Y(r) + 17
                o.append(f'<text x="{X(m):.1f}" y="{y1:.1f}" text-anchor="{anc}" style="fill:var({col});font-weight:600">{nf(r)}</text>'
                         f'<text x="{X(m):.1f}" y="{y1 + 12:.1f}" text-anchor="{anc}" style="font-size:11px">{"" if ch is None else pe(round(ch, 2)).replace(" p.e.", "")}</text>')
    o.append("</svg>")
    return "".join(o)


def auto_text():
    u, s = R["US"]["nivaer"]["10 år"], R["SE"]["nivaer"]["10 år"]
    rikt = lambda x: "steg" if x > 0.005 else "sjönk" if x < -0.005 else "var oförändrad"
    return (f"Den amerikanska tioårsräntan {rikt(u['dag_pe'])} ({pe(u['dag_pe'])}) till {nf(u['ranta'])} % och den svenska {rikt(s['dag_pe'])} "
            f"({pe(s['dag_pe'])}) till {nf(s['ranta'])} %. Fallande långräntor samtidigt som börsen faller brukar tyda på oro och flykt till säkerhet; "
            f"stigande långräntor tyder oftare på inflationsoro eller förväntningar om högre styrräntor.")


pu, tu = kurvtext("US"); ps, ts = kurvtext("SE")
datum = R["US"]["datum"] if R["US"]["datum"] == R["SE"]["datum"] else f'USA {dd(R["US"]["datum"])}, Sverige {dd(R["SE"]["datum"])}'
datum = dd(datum) if len(datum) == 10 else datum
print(f'''<section class="card" aria-labelledby="h-rt">
  <style>.rk{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}@media (max-width:560px){{.rk{{grid-template-columns:1fr}}}}
  .kurva{{max-width:600px;display:block}} .kurva text{{fill:var(--faint);font:12.5px var(--f-mono)}} .leg{{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--muted)}}
  .leg i{{display:inline-block;width:18px;border-top:2.4px solid;vertical-align:middle;margin-right:5px}} .leg i.d{{border-top:1.5px dashed}}</style>
  <div class="hd"><h2 id="h-rt">Räntorna</h2><span class="note">Statsräntor, stängning {datum}</span></div>
  <div class="grp">Räntekurvan, 10 år minus 2 år</div>
  <div class="rk">
    <div class="fact"><span class="k"><span class="flag">US</span>USA <span class="pill {pu[0]}">{pu[1]}</span></span><span class="v">{pe(R["US"]["spread_10_2"]["nu"])}</span><p class="note" style="margin-top:4px">{tu}</p></div>
    <div class="fact"><span class="k"><span class="flag">SE</span>Sverige <span class="pill {ps[0]}">{ps[1]}</span></span><span class="v">{pe(R["SE"]["spread_10_2"]["nu"])}</span><p class="note" style="margin-top:4px">{ts}</p></div>
  </div>
  <div class="grp">Räntekurvorna, 3 månader till 30 år</div>
  <div class="leg"><span><i style="border-color:var(--accent)"></i>USA {dd(R["US"]["datum"])}</span><span><i class="d" style="border-color:var(--accent)"></i>USA {dd(R["US"]["manad_datum"])}</span><span><i style="border-color:var(--warn)"></i>Sverige {dd(R["SE"]["datum"])}</span><span><i class="d" style="border-color:var(--warn)"></i>Sverige {dd(R["SE"]["manad_datum"])}</span></div>
  {graf()}
  <p class="note">Siffrorna i grafen är räntan i procent och, under den, förändringen sedan föregående handelsdag i procentenheter. Streckad linje: en månad tidigare.</p>
  <p class="note">Svenska statsräntor finns bara upp till 10 år hos Riksbanken. Den amerikanska kurvan publiceras efter stängning i New York, så på kvällen gäller den oftast föregående handelsdag.</p>
  <p>{TEXT or auto_text()}</p>
</section>''')
