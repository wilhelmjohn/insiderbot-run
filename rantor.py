"""Statsräntor till Börsläget: USA (finansdepartementets dagliga räntekurva) och Sverige (Riksbankens SWEA-API).

  python rantor.py            # skriver rantor.csv (Datum,Land,Lopetid,Manader,Ranta) med senaste ~400 dagarna

USA: daily_treasury_yield_curve (par yield, 1 mån–30 år), en CSV per år, publiceras efter stängning i New York.
Sverige: statsskuldväxlar (3, 6 mån) och statsobligationer (2, 5, 7, 10 år, och längre om serien finns) från
api.riksbank.se/swea/v1. Serie-id:n letas upp i serielistan. Fel för en källa stoppar inte den andra.
"""
import io, re, sys, time
from datetime import date, timedelta
from pathlib import Path
import pandas as pd, requests

UT = Path("rantor.csv")
H = {"User-Agent": "insiderbot/1.0 (https://github.com/wilhelmjohn/insiderbot-run; privat bevakning)"}
idag = date.today(); start = idag - timedelta(days=400)
rader, fel = [], []

# ---------- USA ----------
US_KOL = {"1 Mo": 1, "2 Mo": 2, "3 Mo": 3, "4 Mo": 4, "6 Mo": 6, "1 Yr": 12, "2 Yr": 24, "3 Yr": 36, "5 Yr": 60, "7 Yr": 84,
          "10 Yr": 120, "20 Yr": 240, "30 Yr": 360}
for ar in sorted({start.year, idag.year}):
    url = (f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{ar}/all"
           f"?type=daily_treasury_yield_curve&field_tdr_date_value={ar}&page&_format=csv")
    try:
        r = requests.get(url, headers=H, timeout=60); r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df["Date"] = pd.to_datetime(df["Date"])
        for k, m in US_KOL.items():
            if k not in df.columns: continue
            for d, v in zip(df.Date, df[k]):
                if pd.notna(v): rader.append(dict(Datum=d.date().isoformat(), Land="US", Lopetid=k.replace(" Mo", " mån").replace(" Yr", " år"), Manader=m, Ranta=float(v)))
        print(f"USA {ar}: {len(df)} dagar, senast {df.Date.max().date()}")
    except Exception as e:
        fel.append(f"USA {ar}: {e.__class__.__name__}: {str(e)[:120]}")

# ---------- Sverige ----------
SWEA = "https://api.riksbank.se/swea/v1"
def get(path):
    for f in range(5):
        r = requests.get(SWEA + path, headers=H, timeout=60)
        if r.status_code == 429: time.sleep(20 * (f + 1)); continue
        r.raise_for_status(); return r.json()
    raise RuntimeError("429")
try:
    serier = get("/Series")
    Path("rantor_logg.txt").write_text("\n".join(f"{x.get('seriesId')}\t{x.get('shortDescription')}\t{x.get('midDescription')}\t{x.get('observationMaxDate')}"
                                                for x in serier if re.search(r"GVB|SETB|GOV|BOND|SSVX", str(x.get('seriesId')) + str(x.get('shortDescription')), re.I)), encoding="utf-8")
    kand = {}
    for s in serier:
        sid = s.get("seriesId", ""); txt = " ".join(str(s.get(k, "")) for k in ("shortDescription", "midDescription", "longDescription"))
        m = re.fullmatch(r"SEGVB(\d+)YC", sid)
        if m: kand[sid] = (f"{int(m.group(1))} år", 12 * int(m.group(1)))
        m = re.fullmatch(r"SETB(\d+)MBENCHC?", sid)
        if m and int(m.group(1)) in (1, 3, 6): kand[sid] = (f"{int(m.group(1))} mån", int(m.group(1)))
    print("Riksbanken, serier:", ", ".join(f"{k}={v[0]}" for k, v in sorted(kand.items(), key=lambda x: x[1][1])))
    # en serie per löptid (föredra ...C, dvs. stängningsnotering)
    per_lop = {}
    for sid, (namn, m) in kand.items():
        if m not in per_lop or sid.endswith("C"): per_lop[m] = (sid, namn)
    for m, (sid, namn) in sorted(per_lop.items()):
        try:
            obs = get(f"/Observations/{sid}/{start.isoformat()}/{idag.isoformat()}")
            for o in obs:
                if o.get("value") is not None: rader.append(dict(Datum=o["date"], Land="SE", Lopetid=namn, Manader=m, Ranta=float(o["value"])))
            print(f"  {sid}: {len(obs)} obs, senast {obs[-1]['date'] if obs else '-'}")
            time.sleep(13)   # Riksbankens API utan nyckel: ca 5 anrop per minut
        except Exception as e:
            fel.append(f"SE {sid}: {e.__class__.__name__}: {str(e)[:120]}")
except Exception as e:
    fel.append(f"Riksbanken: {e.__class__.__name__}: {str(e)[:160]}")

if rader:
    ny = pd.DataFrame(rader)
    gammal = pd.read_csv(UT) if UT.exists() else ny.iloc[:0]
    alla = pd.concat([gammal, ny]).drop_duplicates(["Datum", "Land", "Manader"], keep="last")
    alla = alla[alla.Datum >= start.isoformat()].sort_values(["Land", "Manader", "Datum"])
    alla.to_csv(UT, index=False)
    for l, g in alla.groupby("Land"):
        print(l, "senast", g.Datum.max(), {r.Lopetid: r.Ranta for r in g[g.Datum == g.Datum.max()].itertuples()})
print("FEL:", "; ".join(fel) if fel else "inga")
with open("rantor_logg.txt", "a", encoding="utf-8") as f: f.write("\nFEL: " + ("; ".join(fel) if fel else "inga") + "\n")
sys.exit(1 if not rader else 0)
