"""Förtest: läser Placeras robots.txt och sparar rå-HTML för några kända rekar-sidor (för att bygga tolken).
Respekterar robots.txt; 1 anrop per sekund; identifierar sig."""
import os, sys, time, urllib.robotparser, requests
UA = "insiderbot-research/1.0 (+https://github.com/wilhelmjohn/insiderbot-run)"
BAS = "https://www.placera.se"
UT = "placera_prov"
os.makedirs(UT, exist_ok=True)
S = requests.Session(); S.headers["User-Agent"] = UA

def hamta(url):
    time.sleep(1)
    r = S.get(url, timeout=30)
    return r

r = hamta(BAS + "/robots.txt")
open(f"{UT}/robots.txt", "w", encoding="utf-8").write(f"HTTP {r.status_code}\n{r.text}")
print("robots.txt", r.status_code, len(r.text))
rp = urllib.robotparser.RobotFileParser(); rp.parse(r.text.splitlines())
prov = [
 "/telegram/dagens-aktierekommendationer-i-oversikt-20260623",
 "/telegram/dagens-aktierekommendationer-i-oversikt-20250908",
 "/telegram/dagens-aktierekommendationer-i-oversikt-2024-04-12",
 "/nyheter/onsdagens-alla-nya-aktierekar-2024-10-16",
 "/nyheter/fredagens-alla-nya-aktierekar-2021-04-30",
 "/nyheter/fredagens-alla-nya-aktierekar-2022-10-14",
 "/placera/redaktionellt/2024/05/22/onsdagens-alla-nya-aktierekar.html",
 "/nyheter/flera-sankta-riktkurser-for-evolution--har-ar-dagens-aktierekar-2025-05-02",
]
for i, p in enumerate(prov):
    ok = rp.can_fetch(UA, BAS + p)
    print(p, "tillåten" if ok else "NEKAD av robots.txt")
    if not ok: continue
    try:
        x = hamta(BAS + p)
        open(f"{UT}/{i}.html", "w", encoding="utf-8").write(f"URL {p}\nHTTP {x.status_code}\n{x.text[:400000]}")
        print("  ", x.status_code, len(x.text))
    except Exception as e:
        print("  FEL", e)
sm = [l.split(":", 1)[1].strip() for l in r.text.splitlines() if l.lower().startswith("sitemap:")]
print("sitemaps", sm)
for j, u in enumerate(sm[:3]):
    if rp.can_fetch(UA, u):
        try:
            x = hamta(u); open(f"{UT}/sitemap{j}.xml", "w", encoding="utf-8").write(x.text[:200000]); print("  sitemap", u, x.status_code, len(x.text))
        except Exception as e: print("  FEL", e)
print("KLAR")
