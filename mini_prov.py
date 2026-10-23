"""Prov: hämtar Nordnets lista över mini futures (rå HTML) för att hitta hur listan laddas (API, parametrar).
Sparar hamtat/mini_prov/*.html och en sammanfattning i datarepot. Engångsprov, ingen handel."""
import re, json, pathlib, requests
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept-Language": "sv-SE,sv;q=0.9"}
UT = pathlib.Path("hamtat/mini_prov"); UT.mkdir(parents=True, exist_ok=True)
s = requests.Session(); s.headers.update(UA)
rap = []
for namn, url in [("lista", "https://www.nordnet.se/etp/warranter/mini-futures/lista"),
                  ("lista_p2", "https://www.nordnet.se/etp/warranter/mini-futures/lista?page=2")]:
    r = s.get(url, timeout=60)
    (UT / f"{namn}.html").write_text(r.text, encoding="utf-8")
    t = r.text
    rap.append(f"== {namn} {r.status_code} {len(t)} tecken")
    rap += sorted(set(re.findall(r'["\'](/api/[^"\'\s]{3,200})', t)))[:80]
    rap += sorted(set(re.findall(r'https://[a-z0-9.\-]*nordnet[a-z.]*/api[^"\'\s<>]{0,200}', t)))[:40]
    rap += sorted(set(re.findall(r'href="([^"]*mini-futures[^"]*)"', t)))[:60]
    for m in re.finditer(r'<script[^>]*id="([^"]+)"[^>]*>', t): rap.append("script id: " + m.group(1))
    rap.append("nyckelord: " + ", ".join(k for k in ("__NEXT_DATA__", "__INITIAL_STATE__", "initialState", "apollo", "graphql", "underlying", "direction", "SHORT", "instrument_id") if k in t))
(UT / "rapport.txt").write_text("\n".join(rap), encoding="utf-8")
print("\n".join(rap)[:20000])
