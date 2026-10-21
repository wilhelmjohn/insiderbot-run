"""Länkar till Nordnets aktiesidor (öppnas i Nordnets app på mobilen om appen tar emot länken).
Läser Nordnets publika sitemap för aktier och matchar på ticker + marknad, t.ex. BIOA-B.ST -> .../bio-arctic-b-bioa-b-xsto.
Skriver nordnet_lankar.csv (Ticker,Url). Körs av workflowen Divergens nordiska kurser en gång per vardag."""
import re, time
import pandas as pd, requests

H = {"User-Agent": "Mozilla/5.0 (insiderbot; privat bevakning)"}
urls = []
for n in range(1, 30):
    r = requests.get(f"https://www.nordnet.se/sitemaps/stocks/{n}.xml", headers=H, timeout=30)
    if r.status_code != 200 or "<loc>" not in r.text: break
    urls += re.findall(r"<loc>([^<]+)</loc>", r.text); time.sleep(0.5)
print(f"Nordnet sitemap: {len(urls)} aktiesidor")
kanda = set(pd.read_csv("kurser.csv", usecols=["Ticker"]).Ticker)
for f_ in ("divergens_nordic.csv", "divergens_usa.csv"):
    try: kanda |= set(pd.read_csv(f_, usecols=["Ticker"]).Ticker)
    except FileNotFoundError: pass
# marknadsplats (MIC) -> Yahoo-suffix; okända MIC prövas mot alla suffix och matchas bara mot tickers vi har
MIC = {"xsto": ".ST", "ssme": ".ST", "fnse": ".ST", "xngm": ".ST", "sspx": ".ST", "xsat": ".ST",
       "xosl": ".OL", "merk": ".OL", "xoas": ".OL", "xcse": ".CO", "fndk": ".CO",
       "xnas": "", "xnys": "", "arcx": "", "bats": "", "xase": ""}
PRIO = {"xsto": 0, "xosl": 0, "xcse": 0, "xnas": 0, "xnys": 0, "ssme": 1, "fnse": 1, "merk": 1, "xoas": 1, "fndk": 1}
basta = {}
for u in urls:
    m = re.search(r"/aktier/kurser/(.+)-([a-z]{4})/?$", u)
    if not m: continue
    delar, mic = m.group(1).split("-"), m.group(2)
    suffix = [MIC[mic]] if mic in MIC else [".ST", ".OL", ".CO", ""]
    for n_ in (1, 2, 3):   # tickern är de sista leden i sluggen
        if len(delar) < n_: break
        for suf in suffix:
            t = "-".join(delar[-n_:]).upper() + suf
            if t in kanda and (t not in basta or PRIO.get(mic, 5) < basta[t][0]):
                basta[t] = (PRIO.get(mic, 5), u)
df = pd.DataFrame(sorted((t, u) for t, (_, u) in basta.items()), columns=["Ticker", "Url"])
df.to_csv("nordnet_lankar.csv", index=False)
print(f"Matchade {len(df)} av {len(kanda)} tickers")
