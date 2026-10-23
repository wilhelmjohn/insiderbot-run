"""Hämtar Nordnets lista över mini futures (Nordnet Markets) och sparar vilka underliggande som har korta (MINI S) produkter.

  python mini_hamta.py            (körs i datarepot i GitHub Actions; skriver nordnet_mini.csv)

Källa: samma sökning som sidan https://www.nordnet.se/etp/warranter/mini-futures/lista använder
(/api/2/instrument_search/query/minifuturelist, filter nordnet_markets=true). Bläddrar 100 åt gången.
Utdata nordnet_mini.csv: Underliggande, Underliggande_id, Kort, Kort_oppna, Lang, Hamtad.
Misslyckas hämtningen behålls den gamla filen (skriptet avslutas med fel, filen skrivs inte över).
"""
import os, sys, time, datetime as dt
from collections import defaultdict
import pandas as pd, requests

BAS = "https://www.nordnet.se"
LISTA = BAS + "/etp/warranter/mini-futures/lista"
API = BAS + "/api/2/instrument_search/query/minifuturelist"
ALLA = os.environ.get("MINI_ALLA") == "1"   # alla emittenter hos Nordnet, inte bara Nordnet Markets
UTFIL = "nordnet_mini_alla.csv" if ALLA else "nordnet_mini.csv"
PARAM = {} if ALLA else {"apply_filters": "nordnet_markets=true"}
s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
                  "Accept-Language": "sv-SE,sv;q=0.9"})
s.get(LISTA, timeout=60).raise_for_status()          # cookies
s.headers.update({"client-id": "NEXT", "Accept": "application/json", "Referer": LISTA})

agg = defaultdict(lambda: dict(Underliggande_id=None, Kort=0, Kort_oppna=0, Lang=0, Emittenter=set()))
offset, total = 0, None
while total is None or offset < total:
    for forsok in range(4):
        r = s.get(API, params={**PARAM, "limit": 100, "offset": offset}, timeout=60)
        if r.status_code == 200: break
        print(f"offset {offset}: HTTP {r.status_code} {r.text[:200]}"); time.sleep(3 + 5 * forsok)
    r.raise_for_status()
    j = r.json(); total = j.get("total_hits", 0); res = j.get("results", [])
    if not res: break
    for x in res:
        e = x.get("etp_info") or {}
        namn = e.get("underlying_name")
        if not namn: continue
        a = agg[namn]; a["Underliggande_id"] = e.get("underlying_instrument_id")
        kort = e.get("market_view") == "D" or str(e.get("direction", "")).lower().startswith("kort")
        if kort:
            a["Kort"] += 1; a["Emittenter"].add((x.get("instrument_info") or {}).get("issuer_name", ""))
            if (x.get("status_info") or {}).get("trading_status") == "TRADING": a["Kort_oppna"] += 1
        else:
            a["Lang"] += 1
    offset += len(res)
    time.sleep(0.4)
print(f"Hämtade {offset} av {total} produkter, {len(agg)} underliggande, {sum(1 for a in agg.values() if a['Kort'])} med korta")
if total is None or offset < 0.9 * total:
    sys.exit("För få produkter hämtade: behåller gamla nordnet_mini.csv")
nu = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
df = pd.DataFrame([dict(Underliggande=k, **{**v, "Emittenter": "; ".join(sorted(e for e in v["Emittenter"] if e))}, Hamtad=nu) for k, v in agg.items()]).sort_values("Underliggande")
df.to_csv(UTFIL, index=False)
